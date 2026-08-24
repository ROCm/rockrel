# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Write-capable GitHub client reserved for trusted rockrel control planes.

The SLAI Marketplace bundle excludes this module. Planning code consumes the
GET-only :mod:`github_read` boundary, while production feedback and draft-PR
transactions explicitly import this wider client.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .github_read import (
    DEFAULT_TIMEOUT_SECONDS,
    GITHUB_ACCEPT,
    GITHUB_API_VERSION,
    MAX_RESPONSE_BYTES,
    RETRYABLE_HTTP_STATUSES,
    ApiError,
    BranchInfo,
    DestinationPolicy,
    GitHubReadClient,
    RemoteAccessDisabled,
    RetryPolicy,
    _array,
    _NoRedirect,
    _object,
    _read_bounded,
    parse_pull_request_url,
)

__all__ = (
    "ApiError",
    "BranchInfo",
    "DenyNetworkTransport",
    "DestinationPolicy",
    "GitHubClient",
    "MAX_RESPONSE_BYTES",
    "RemoteAccessDisabled",
    "RetryPolicy",
    "Transport",
    "UrlLibTransport",
    "parse_pull_request_url",
)


class Transport(Protocol):
    """Define the production HTTP boundary that can send JSON mutations."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> object:
        """Perform one request under the production transport safety policy."""

        ...


class DenyNetworkTransport:
    """Fail every production API call until an adapter is explicitly installed."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> object:
        """Reject a request without opening a socket."""

        del method, url, headers, body
        raise RemoteAccessDisabled()


class UrlLibTransport:
    """Send bounded JSON requests without following credential-bearing redirects."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> object:
        """Send one JSON request with a fixed timeout and two-MiB response cap."""

        request = urllib.request.Request(
            url=url,
            method=method,
            headers=headers or {},
            data=body,
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                payload = _read_bounded(response, status=0)
        except urllib.error.HTTPError as exc:
            detail = _read_bounded(exc, status=exc.code).decode(errors="replace")
            raise ApiError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise ApiError(0, str(exc.reason)) from exc
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ApiError(0, "response was not valid JSON") from exc


@dataclass
class GitHubClient(GitHubReadClient):
    """Add the narrow production mutation surface to the shared read client."""

    transport: Transport = field(default_factory=DenyNetworkTransport)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> object:
        """Send an authenticated request with bounded transient retries."""

        headers = {
            "Accept": GITHUB_ACCEPT,
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "rocm-rockrel-cherry-pick",
        }
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body, separators=(",", ":")).encode()
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                return self.transport.request(
                    method,
                    f"{self.api_url.rstrip('/')}{path}",
                    headers=headers,
                    body=payload,
                )
            except ApiError as exc:
                retryable = exc.status in RETRYABLE_HTTP_STATUSES or (
                    exc.status == 403 and "rate limit" in exc.message.lower()
                )
                if not retryable or attempt == self.retry_policy.max_attempts:
                    raise
                self.sleep(self.retry_policy.base_delay_seconds * (2 ** (attempt - 1)))
        raise AssertionError("unreachable retry loop")

    def _get(self, path: str) -> object:
        """Route inherited evidence reads through the production transport."""

        return self._request("GET", path)

    def issue_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, object]]:
        """Fetch issue comments used for idempotent status feedback."""

        comments: list[dict[str, object]] = []
        page = 1
        while True:
            items = _array(
                self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/issues/{number}/comments?per_page=100&page={page}",
                ),
                "GitHub comments",
            )
            comments.extend(_object(item, "GitHub comment") for item in items)
            if len(items) < 100:
                return comments
            page += 1

    def upsert_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        marker: str,
        body: str,
    ) -> None:
        """Create or update the deterministic cherry-pick status comment."""

        marked_body = f"{marker}\n{body}"
        for comment in self.issue_comments(owner, repo, number):
            comment_body = comment.get("body")
            if isinstance(comment_body, str) and marker in comment_body:
                comment_id = comment.get("id")
                if not isinstance(comment_id, int):
                    raise ApiError(0, "matching GitHub comment omitted id")
                self._request(
                    "PATCH",
                    f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
                    body={"body": marked_body},
                )
                return
        self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            body={"body": marked_body},
        )

    def create_pull(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> str:
        """Create one pull request and reject every non-draft request."""

        if not draft:
            raise ValueError("cherry-pick automation may create draft PRs only")
        response = _object(
            self._request(
                "POST",
                f"/repos/{owner}/{repo}/pulls",
                body={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                    "draft": True,
                },
            ),
            "created pull request",
        )
        url = response.get("html_url")
        if not isinstance(url, str):
            raise ApiError(0, "created pull request response omitted html_url")
        return url

    def ensure_label(
        self,
        owner: str,
        repo: str,
        *,
        name: str,
        description: str,
        color: str = "7B42BC",
    ) -> None:
        """Create or update one configured status label."""

        encoded = urllib.parse.quote(name, safe="")
        payload = {"new_name": name, "description": description, "color": color}
        try:
            self._request(
                "PATCH", f"/repos/{owner}/{repo}/labels/{encoded}", body=payload
            )
        except ApiError as exc:
            if exc.status != 404:
                raise
            self._request(
                "POST",
                f"/repos/{owner}/{repo}/labels",
                body={"name": name, "description": description, "color": color},
            )

    def upsert_check_run(
        self,
        owner: str,
        repo: str,
        *,
        head_sha: str,
        name: str,
        external_id: str,
        conclusion: str,
        title: str,
        summary: str,
    ) -> str:
        """Create or update one deterministic cherry-pick Check Run."""

        if conclusion not in {
            "success",
            "neutral",
            "action_required",
            "cancelled",
        }:
            raise ValueError("unsupported Check conclusion")
        matching_id: int | None = None
        page = 1
        while True:
            response = _object(
                self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs?per_page=100&page={page}",
                ),
                "GitHub check runs",
            )
            runs = _array(response.get("check_runs"), "GitHub check runs")
            for raw_run in runs:
                run = _object(raw_run, "GitHub check run")
                if run.get("name") == name and run.get("external_id") == external_id:
                    run_id = run.get("id")
                    if isinstance(run_id, bool) or not isinstance(run_id, int):
                        raise ApiError(0, "matching GitHub check run omitted id")
                    matching_id = run_id
            if len(runs) < 100:
                break
            page += 1
        payload: dict[str, object] = {
            "name": name,
            "external_id": external_id,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": title, "summary": summary},
        }
        if matching_id is None:
            payload["head_sha"] = head_sha
            path = f"/repos/{owner}/{repo}/check-runs"
            method = "POST"
        else:
            path = f"/repos/{owner}/{repo}/check-runs/{matching_id}"
            method = "PATCH"
        result = _object(self._request(method, path, body=payload), "GitHub check run")
        url = result.get("html_url")
        if not isinstance(url, str):
            raise ApiError(0, "GitHub check run response omitted html_url")
        return url
