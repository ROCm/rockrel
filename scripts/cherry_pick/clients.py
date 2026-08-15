# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Injectable, typed REST clients for GitHub and Jira."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"
PR_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?\Z"
)
JIRA_KEY_RE = re.compile(r"\bROCM-[0-9]+\b", re.IGNORECASE)
DEPENDENCY_TRAILER_RE = re.compile(
    r"^Cherry-Pick-(?:Depends-On|After):\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"API request failed with HTTP {status}: {message}")
        self.status = status
        self.message = message


class RemoteAccessDisabled(ApiError):
    """Raised when a local-review client attempts network access."""

    def __init__(self) -> None:
        super().__init__(0, "network access is disabled in local-review mode")


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> object: ...


class DenyNetworkTransport:
    """Default transport: fail locally without opening a socket."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> object:
        del method, url, headers, body
        raise RemoteAccessDisabled()


class UrlLibTransport:
    """Future urllib-backed JSON transport with bounded requests.

    It is never the default and is not constructed by the local-review CLI.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> object:
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


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")


@dataclass(frozen=True)
class BranchInfo:
    exists: bool
    sha: str | None


@dataclass(frozen=True)
class DestinationPolicy:
    pull_request_required: bool
    rule_ids: tuple[int, ...] = ()
    required_approvals: int = 0
    require_last_push_approval: bool = False
    allowed_merge_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class JiraIssueEvidence:
    fix_versions: frozenset[str]
    dependencies: tuple[str, ...]
    ordering_notes: tuple[str, ...]


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ApiError(0, f"{context} response was not an object")
    return value


def _array(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ApiError(0, f"{context} response was not an array")
    return value


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


def extract_dependency_trailers(text: str) -> tuple[str, ...]:
    """Extract exact machine-readable dependency/order trailer values."""

    values: list[str] = []
    seen: set[str] = set()
    for match in DEPENDENCY_TRAILER_RE.finditer(text):
        for raw in match.group(1).split(","):
            value = raw.strip()
            if value and value not in seen:
                values.append(value)
                seen.add(value)
    return tuple(values)


@dataclass
class GitHubClient:
    token: str
    transport: Transport = field(default_factory=DenyNetworkTransport)
    api_url: str = "https://api.github.com"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> object:
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
                retryable = exc.status in RETRYABLE_HTTP_STATUSES
                if not retryable or attempt == self.retry_policy.max_attempts:
                    raise
                self.sleep(
                    self.retry_policy.base_delay_seconds * (2 ** (attempt - 1))
                )
        raise AssertionError("unreachable retry loop")

    def pull(self, owner: str, repo: str, number: int) -> dict[str, object]:
        return _object(
            self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}"),
            "GitHub pull",
        )

    def pull_commits(self, owner: str, repo: str, number: int) -> tuple[str, ...]:
        commits: list[str] = []
        page = 1
        while True:
            items = _array(
                self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/pulls/{number}/commits?per_page=100&page={page}",
                ),
                "GitHub pull commits",
            )
            for item in items:
                value = _object(item, "GitHub pull commit")
                sha = value.get("sha")
                if not isinstance(sha, str):
                    raise ApiError(0, "GitHub pull commit omitted sha")
                commits.append(sha)
            if len(items) < 100:
                return tuple(commits)
            page += 1

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
            events = _array(
                self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/issues/{number}/timeline?per_page=100&page={page}",
                ),
                "GitHub timeline",
            )
            for raw_event in events:
                event = _object(raw_event, "GitHub timeline event")
                label_value = event.get("label")
                actor_value = event.get("actor")
                if not isinstance(label_value, dict) or not isinstance(actor_value, dict):
                    continue
                login = actor_value.get("login")
                if (
                    event.get("event") == "labeled"
                    and label_value.get("name") == label
                    and isinstance(login, str)
                ):
                    matches.append(login)
            if len(events) < 100:
                return matches[-1] if matches else None
            page += 1

    def permission(self, owner: str, repo: str, username: str) -> str:
        encoded = urllib.parse.quote(username, safe="")
        response = _object(
            self._request(
                "GET", f"/repos/{owner}/{repo}/collaborators/{encoded}/permission"
            ),
            "GitHub permission",
        )
        permission = response.get("permission")
        if not isinstance(permission, str):
            raise ApiError(0, "GitHub permission response omitted permission")
        return permission

    def branch(self, owner: str, repo: str, branch: str) -> BranchInfo:
        encoded = urllib.parse.quote(branch, safe="")
        try:
            response = _object(
                self._request("GET", f"/repos/{owner}/{repo}/branches/{encoded}"),
                "GitHub branch",
            )
        except ApiError as exc:
            if exc.status == 404:
                return BranchInfo(exists=False, sha=None)
            raise
        commit = response.get("commit")
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if sha is not None and not isinstance(sha, str):
            raise ApiError(0, "GitHub branch response contained an invalid sha")
        return BranchInfo(exists=True, sha=sha)

    def destination_policy(
        self, owner: str, repo: str, branch: str
    ) -> DestinationPolicy:
        encoded = urllib.parse.quote(branch, safe="")
        rules = _array(
            self._request("GET", f"/repos/{owner}/{repo}/rules/branches/{encoded}"),
            "GitHub effective rules",
        )
        rule_ids: list[int] = []
        approvals = 0
        last_push = False
        methods: list[str] = []
        for raw_rule in rules:
            rule = _object(raw_rule, "GitHub effective rule")
            if rule.get("type") != "pull_request":
                continue
            rule_id = rule.get("ruleset_id")
            if isinstance(rule_id, int):
                rule_ids.append(rule_id)
            parameters = rule.get("parameters")
            if isinstance(parameters, dict):
                count = parameters.get("required_approving_review_count")
                if isinstance(count, int):
                    approvals = max(approvals, count)
                last_push = last_push or parameters.get("require_last_push_approval") is True
                allowed = parameters.get("allowed_merge_methods")
                if isinstance(allowed, list):
                    methods.extend(item for item in allowed if isinstance(item, str))
        return DestinationPolicy(
            pull_request_required=bool(rule_ids),
            rule_ids=tuple(sorted(set(rule_ids))),
            required_approvals=approvals,
            require_last_push_approval=last_push,
            allowed_merge_methods=tuple(dict.fromkeys(methods)),
        )

    def pulls(
        self,
        owner: str,
        repo: str,
        *,
        base: str,
        state: str = "all",
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        page = 1
        while True:
            query = urllib.parse.urlencode(
                {
                    "state": state,
                    "base": base,
                    "per_page": 100,
                    "sort": "updated",
                    "page": page,
                }
            )
            items = _array(
                self._request("GET", f"/repos/{owner}/{repo}/pulls?{query}"),
                "GitHub pulls",
            )
            results.extend(_object(item, "GitHub pull") for item in items)
            if len(items) < 100:
                return results
            page += 1

    def pull_for_head(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
    ) -> dict[str, object] | None:
        for pull in self.pulls(owner, repo, base=base, state="all"):
            pull_head = pull.get("head")
            if isinstance(pull_head, dict) and pull_head.get("ref") == head:
                return pull
        return None

    def search_merged_labeled_pull_requests(
        self,
        owner: str,
        repo: str,
        label: str,
    ) -> list[str]:
        results: list[str] = []
        page = 1
        while True:
            query = urllib.parse.urlencode(
                {
                    "q": f'repo:{owner}/{repo} is:pr is:merged label:"{label}"',
                    "per_page": 100,
                    "page": page,
                }
            )
            response = _object(
                self._request("GET", f"/search/issues?{query}"),
                "GitHub search",
            )
            items = _array(response.get("items"), "GitHub search items")
            for raw_item in items:
                item = _object(raw_item, "GitHub search item")
                url = item.get("html_url")
                if isinstance(item.get("pull_request"), dict) and isinstance(url, str):
                    results.append(url)
            if len(items) < 100:
                return results
            page += 1

    def commit(self, owner: str, repo: str, sha: str) -> dict[str, object]:
        encoded = urllib.parse.quote(sha, safe="")
        return _object(
            self._request("GET", f"/repos/{owner}/{repo}/commits/{encoded}"),
            "GitHub commit",
        )

    def compare(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
    ) -> dict[str, object]:
        encoded_base = urllib.parse.quote(base, safe="")
        encoded_head = urllib.parse.quote(head, safe="")
        return _object(
            self._request(
                "GET",
                f"/repos/{owner}/{repo}/compare/{encoded_base}...{encoded_head}",
            ),
            "GitHub compare",
        )

    def remove_label(self, owner: str, repo: str, number: int, label: str) -> None:
        encoded = urllib.parse.quote(label, safe="")
        try:
            self._request(
                "DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/{encoded}"
            )
        except ApiError as exc:
            if exc.status != 404:
                raise

    def issue_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, object]]:
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
    transport: Transport = field(default_factory=DenyNetworkTransport)

    def issue_evidence(
        self, issue_key: str, *, ordering_fields: tuple[str, ...] = ()
    ) -> JiraIssueEvidence:
        encoded = urllib.parse.quote(issue_key, safe="")
        fields = ("fixVersions", "issuelinks", *ordering_fields)
        query = urllib.parse.urlencode({"fields": ",".join(fields)})
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "rocm-rockrel-cherry-pick",
        }
        response = _object(
            self.transport.request(
                "GET",
                f"{self.base_url.rstrip('/')}/rest/api/2/issue/{encoded}?{query}",
                headers=headers,
                body=None,
            ),
            "Jira issue",
        )
        raw_fields = _object(response.get("fields"), "Jira fields")
        versions = _array(raw_fields.get("fixVersions", []), "Jira fixVersions")
        fix_versions = frozenset(
            name
            for item in versions
            if isinstance(item, dict)
            for name in [item.get("name")]
            if isinstance(name, str)
        )
        links = _array(raw_fields.get("issuelinks", []), "Jira issue links")
        dependencies: list[str] = []
        for raw_link in links:
            link = _object(raw_link, "Jira issue link")
            relation = link.get("type")
            inward = link.get("inwardIssue")
            inward_name = relation.get("inward") if isinstance(relation, dict) else None
            if (
                isinstance(inward_name, str)
                and ("block" in inward_name.lower() or "depend" in inward_name.lower())
                and isinstance(inward, dict)
                and isinstance(inward.get("key"), str)
            ):
                dependencies.append(str(inward["key"]))
        ordering_notes = tuple(
            str(value).strip()
            for field_name in ordering_fields
            for value in [raw_fields.get(field_name)]
            if isinstance(value, str) and value.strip()
        )
        return JiraIssueEvidence(
            fix_versions=fix_versions,
            dependencies=tuple(dict.fromkeys(dependencies)),
            ordering_notes=ordering_notes,
        )

    def fix_versions(self, issue_key: str) -> set[str]:
        """Compatibility wrapper used by older local coverage fixtures."""

        return set(self.issue_evidence(issue_key).fix_versions)
