"""Minimal injectable REST clients for GitHub and Jira."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"
PR_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?\Z"
)
JIRA_KEY_RE = re.compile(r"\bROCM-[0-9]+\b", re.IGNORECASE)


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"API request failed with HTTP {status}: {message}")
        self.status = status
        self.message = message


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> Any: ...


class UrlLibTransport:
    """urllib-backed JSON transport with bounded requests."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> Any:
        request = urllib.request.Request(
            url=url,
            method=method,
            headers=headers or {},
            data=body,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise ApiError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise ApiError(0, str(exc.reason)) from exc
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ApiError(0, "response was not valid JSON") from exc


def parse_pull_request_url(url: str) -> tuple[str, str, int]:
    match = PR_URL_RE.fullmatch(url)
    if match is None:
        raise ValueError(f"not a canonical GitHub pull request URL: {url!r}")
    return match.group(1), match.group(2), int(match.group(3))


def extract_jira_keys(text: str) -> list[str]:
    """Return unique ROCm Jira keys in first-appearance order."""

    seen: set[str] = set()
    result: list[str] = []
    for match in JIRA_KEY_RE.finditer(text):
        key = match.group(0).upper()
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


@dataclass
class GitHubClient:
    token: str
    transport: Transport = UrlLibTransport()
    api_url: str = "https://api.github.com"

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> Any:
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
        return self.transport.request(
            method,
            f"{self.api_url.rstrip('/')}{path}",
            headers=headers,
            body=payload,
        )

    def pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def label_actor(
        self,
        owner: str,
        repo: str,
        number: int,
        label: str,
    ) -> str | None:
        matches: list[str] = []
        page = 1
        while True:
            events = self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues/{number}/timeline?per_page=100&page={page}",
            )
            if not isinstance(events, list):
                raise ApiError(0, "GitHub timeline response was not an array")
            for event in events:
                if (
                    event.get("event") == "labeled"
                    and event.get("label", {}).get("name") == label
                    and isinstance(event.get("actor", {}).get("login"), str)
                ):
                    matches.append(event["actor"]["login"])
            if len(events) < 100:
                break
            page += 1
        return matches[-1] if matches else None

    def permission(self, owner: str, repo: str, username: str) -> str:
        encoded = urllib.parse.quote(username, safe="")
        response = self._request(
            "GET", f"/repos/{owner}/{repo}/collaborators/{encoded}/permission"
        )
        permission = response.get("permission")
        if not isinstance(permission, str):
            raise ApiError(0, "GitHub permission response omitted permission")
        return permission

    def branch(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(branch, safe="")
        try:
            response = self._request("GET", f"/repos/{owner}/{repo}/branches/{encoded}")
        except ApiError as exc:
            if exc.status == 404:
                return {"exists": False, "protected": False, "sha": None}
            raise
        return {
            "exists": True,
            "protected": response.get("protected") is True,
            "sha": response.get("commit", {}).get("sha"),
        }

    def pulls(
        self,
        owner: str,
        repo: str,
        *,
        base: str,
        state: str = "all",
    ) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {"state": state, "base": base, "per_page": 100, "sort": "updated"}
        )
        response = self._request("GET", f"/repos/{owner}/{repo}/pulls?{query}")
        if not isinstance(response, list):
            raise ApiError(0, "GitHub pulls response was not an array")
        return response

    def search_merged_labeled_pull_requests(
        self,
        owner: str,
        repo: str,
        label: str,
    ) -> list[str]:
        query = urllib.parse.urlencode(
            {
                "q": (
                    f'repo:{owner}/{repo} is:pr is:merged label:"{label}"'
                ),
                "per_page": 100,
            }
        )
        response = self._request("GET", f"/search/issues?{query}")
        items = response.get("items", [])
        if not isinstance(items, list):
            raise ApiError(0, "GitHub search response omitted its items array")
        return [
            item["html_url"]
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("pull_request"), dict)
            and isinstance(item.get("html_url"), str)
        ]

    def commit(
        self, owner: str, repo: str, sha: str
    ) -> dict[str, Any]:
        encoded = urllib.parse.quote(sha, safe="")
        response = self._request(
            "GET", f"/repos/{owner}/{repo}/commits/{encoded}"
        )
        if not isinstance(response, dict):
            raise ApiError(0, "GitHub commit response was not an object")
        return response

    def compare(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
    ) -> dict[str, Any]:
        encoded_base = urllib.parse.quote(base, safe="")
        encoded_head = urllib.parse.quote(head, safe="")
        response = self._request(
            "GET",
            f"/repos/{owner}/{repo}/compare/{encoded_base}...{encoded_head}",
        )
        if not isinstance(response, dict):
            raise ApiError(0, "GitHub compare response was not an object")
        return response

    def remove_label(
        self, owner: str, repo: str, number: int, label: str
    ) -> None:
        encoded = urllib.parse.quote(label, safe="")
        try:
            self._request(
                "DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/{encoded}"
            )
        except ApiError as exc:
            if exc.status != 404:
                raise

    def issue_comments(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        response = self._request(
            "GET", f"/repos/{owner}/{repo}/issues/{number}/comments?per_page=100"
        )
        if not isinstance(response, list):
            raise ApiError(0, "GitHub comments response was not an array")
        return response

    def upsert_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        marker: str,
        body: str,
    ) -> None:
        marked_body = f"{marker}\n{body}"
        for comment in self.issue_comments(owner, repo, number):
            if marker in (comment.get("body") or ""):
                self._request(
                    "PATCH",
                    f"/repos/{owner}/{repo}/issues/comments/{comment['id']}",
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
    ) -> str:
        response = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            body={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": True,
            },
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
        encoded = urllib.parse.quote(name, safe="")
        payload = {"new_name": name, "description": description, "color": color}
        try:
            self._request("PATCH", f"/repos/{owner}/{repo}/labels/{encoded}", body=payload)
        except ApiError as exc:
            if exc.status != 404:
                raise
            self._request(
                "POST",
                f"/repos/{owner}/{repo}/labels",
                body={"name": name, "description": description, "color": color},
            )


@dataclass
class JiraClient:
    base_url: str
    token: str
    transport: Transport = UrlLibTransport()

    def fix_versions(self, issue_key: str) -> set[str]:
        encoded = urllib.parse.quote(issue_key, safe="")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "rocm-rockrel-cherry-pick",
        }
        response = self.transport.request(
            "GET",
            f"{self.base_url.rstrip('/')}/rest/api/2/issue/{encoded}?fields=fixVersions",
            headers=headers,
            body=None,
        )
        versions = response.get("fields", {}).get("fixVersions", [])
        if not isinstance(versions, list):
            raise ApiError(0, "Jira fixVersions response was not an array")
        return {
            item["name"]
            for item in versions
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
