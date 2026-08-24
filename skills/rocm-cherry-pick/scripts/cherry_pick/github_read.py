# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Read-only GitHub evidence client shared by local and production planners."""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .authorization import LabelTransition

GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"
PR_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?\Z"
)
RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30


class ApiError(RuntimeError):
    """Report a sanitized GitHub API operation failure."""

    def __init__(self, status: int, message: str):
        """Initialize a sanitized API failure from its HTTP status and message."""

        super().__init__(f"API request failed with HTTP {status}: {message}")
        self.status = status
        self.message = message


class ReadOnlyGitHubError(RuntimeError):
    """Report local read-credential discovery failures without exposing secrets."""

    pass


class RemoteAccessDisabled(ApiError):
    """Raised when a local-review client attempts network access."""

    def __init__(self) -> None:
        """Initialize the fail-closed error raised by deny-network transports."""

        super().__init__(0, "network access is disabled in local-review mode")


class ReadTransport(Protocol):
    """Define the GET-only transport boundary used to acquire GitHub evidence."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        """Fetch one JSON resource without exposing an arbitrary HTTP method."""

        ...


class DenyNetworkReadTransport:
    """Fail every evidence read locally without opening a socket."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        """Reject a read when no explicit credential adapter was installed."""

        del url, headers
        raise RemoteAccessDisabled()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so GitHub credentials stay on the reviewed origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse every redirect instead of forwarding request headers."""

        return None


def _read_bounded(response, *, status: int) -> bytes:
    """Read at most the configured response limit or raise a safe API error."""

    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ApiError(status, "response was too large")
    return payload


class UrlLibReadTransport:
    """Opt-in GET-only urllib JSON transport with bounded responses.

    Redirects are disabled so a bearer credential cannot leave the reviewed
    GitHub API origin. Responses are capped at two MiB and calls have a fixed
    timeout. The transport has no generic method or request-body surface.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        """Fetch and decode one JSON response with the read-only safety policy."""

        request = urllib.request.Request(
            url=url,
            method="GET",
            headers=headers or {},
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


@dataclass(frozen=True)
class RetryPolicy:
    """Define bounded retry behavior for transient GitHub API failures."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5

    def __post_init__(self) -> None:
        """Validate retry policy invariants after dataclass initialization."""

        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")


@dataclass(frozen=True)
class BranchInfo:
    """Represent immutable destination branch identity and protection evidence."""

    exists: bool
    sha: str | None


@dataclass(frozen=True)
class DestinationPolicy:
    """Represent the reviewed write policy for one destination branch."""

    pull_request_required: bool
    rule_ids: tuple[int, ...] = ()
    required_approvals: int = 0
    require_last_push_approval: bool = False
    allowed_merge_methods: tuple[str, ...] = ()


def _object(value: object, context: str) -> dict[str, object]:
    """Validate and return an object-shaped contract value."""

    if not isinstance(value, dict):
        raise ApiError(0, f"{context} response was not an object")
    return value


def _array(value: object, context: str) -> list[object]:
    """Validate and return an array-shaped API value."""

    if not isinstance(value, list):
        raise ApiError(0, f"{context} response was not an array")
    return value


def parse_pull_request_url(url: str) -> tuple[str, str, int]:
    """Return owner, repository, and number from one canonical GitHub PR URL."""

    match = PR_URL_RE.fullmatch(url)
    if match is None:
        raise ValueError(f"not a canonical GitHub pull request URL: {url!r}")
    return match.group(1), match.group(2), int(match.group(3))


@dataclass
class GitHubReadClient:
    """Provide only the GitHub evidence reads required by cherry-pick planning."""

    token: str = field(repr=False)
    transport: ReadTransport = field(default_factory=DenyNetworkReadTransport)
    api_url: str = "https://api.github.com"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep

    def _get(self, path: str) -> object:
        """Fetch authenticated GitHub evidence with bounded transient retries."""

        headers = {
            "Accept": GITHUB_ACCEPT,
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "rocm-rockrel-cherry-pick",
        }
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                return self.transport.get(
                    f"{self.api_url.rstrip('/')}{path}",
                    headers=headers,
                )
            except ApiError as exc:
                retryable = exc.status in RETRYABLE_HTTP_STATUSES or (
                    exc.status == 403 and "rate limit" in exc.message.lower()
                )
                if not retryable or attempt == self.retry_policy.max_attempts:
                    raise
                self.sleep(self.retry_policy.base_delay_seconds * (2 ** (attempt - 1)))
        raise AssertionError("unreachable retry loop")

    def pull(self, owner: str, repo: str, number: int) -> dict[str, object]:
        """Fetch and validate one GitHub pull request response."""

        return _object(
            self._get(f"/repos/{owner}/{repo}/pulls/{number}"),
            "GitHub pull",
        )

    def pull_commits(self, owner: str, repo: str, number: int) -> tuple[str, ...]:
        """Fetch the complete ordered commit list for one pull request."""

        commits: list[str] = []
        page = 1
        while True:
            items = _array(
                self._get(
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

    def label_transitions(
        self,
        owner: str,
        repo: str,
        number: int,
        label: str,
    ) -> tuple[LabelTransition, ...]:
        """Return every durable transition for one exact label."""

        matches: list[LabelTransition] = []
        page = 1
        while True:
            events = _array(
                self._get(
                    f"/repos/{owner}/{repo}/issues/{number}/timeline?per_page=100&page={page}",
                ),
                "GitHub timeline",
            )
            for raw_event in events:
                event = _object(raw_event, "GitHub timeline event")
                action = event.get("event")
                label_value = event.get("label")
                if (
                    action not in {"labeled", "unlabeled"}
                    or not isinstance(label_value, dict)
                    or label_value.get("name") != label
                ):
                    continue
                actor = event.get("actor")
                app = event.get("performed_via_github_app")
                event_id = event.get("id")
                node_id = event.get("node_id")
                created_at = event.get("created_at")
                actor_id = actor.get("id") if isinstance(actor, dict) else None
                actor_login = actor.get("login") if isinstance(actor, dict) else None
                app_id = app.get("id") if isinstance(app, dict) else None
                if (
                    isinstance(event_id, bool)
                    or not isinstance(event_id, int)
                    or not isinstance(node_id, str)
                    or not isinstance(created_at, str)
                    or isinstance(actor_id, bool)
                    or not isinstance(actor_id, int)
                    or not isinstance(actor_login, str)
                    or (
                        app_id is not None
                        and (isinstance(app_id, bool) or not isinstance(app_id, int))
                    )
                ):
                    raise ApiError(0, "GitHub label transition evidence is malformed")
                matches.append(
                    LabelTransition(
                        event_id=event_id,
                        node_id=node_id,
                        label=label,
                        action=action,
                        created_at=created_at,
                        actor_id=actor_id,
                        actor_login=actor_login,
                        performed_via_app_id=app_id,
                    )
                )
            if len(events) < 100:
                return tuple(matches)
            page += 1

    def permission(self, owner: str, repo: str, username: str) -> str:
        """Fetch and validate the actor permission for one repository."""

        encoded = urllib.parse.quote(username, safe="")
        response = _object(
            self._get(f"/repos/{owner}/{repo}/collaborators/{encoded}/permission"),
            "GitHub permission",
        )
        permission = response.get("permission")
        if not isinstance(permission, str):
            raise ApiError(0, "GitHub permission response omitted permission")
        return permission

    def branch(self, owner: str, repo: str, branch: str) -> BranchInfo:
        """Fetch and validate one repository branch response."""

        encoded = urllib.parse.quote(branch, safe="")
        try:
            response = _object(
                self._get(f"/repos/{owner}/{repo}/branches/{encoded}"),
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
        """Fetch the destination branch protection and write policy."""

        encoded = urllib.parse.quote(branch, safe="")
        rules = _array(
            self._get(f"/repos/{owner}/{repo}/rules/branches/{encoded}"),
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
                last_push = (
                    last_push or parameters.get("require_last_push_approval") is True
                )
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
        """List destination pull requests relevant to idempotency checks."""

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
                self._get(f"/repos/{owner}/{repo}/pulls?{query}"),
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
        """Find an existing pull request for the exact automation head branch."""

        expected_repository = f"{owner}/{repo}"
        for pull in self.pulls(owner, repo, base=base, state="all"):
            pull_head = pull.get("head")
            head_repository = (
                pull_head.get("repo") if isinstance(pull_head, dict) else None
            )
            if (
                isinstance(pull_head, dict)
                and pull_head.get("ref") == head
                and isinstance(head_repository, dict)
                and head_repository.get("full_name") == expected_repository
            ):
                return pull
        return None

    def search_merged_labeled_pull_requests(
        self,
        owner: str,
        repo: str,
        label: str,
    ) -> list[str]:
        """Search merged labeled pull requests for reviewed dependency evidence."""

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
                self._get(f"/search/issues?{query}"),
                "GitHub search",
            )
            total_count = response.get("total_count")
            if (
                isinstance(total_count, bool)
                or not isinstance(total_count, int)
                or total_count < 0
            ):
                raise ApiError(0, "GitHub search total_count is invalid")
            if response.get("incomplete_results") is not False:
                raise ApiError(0, "GitHub search results are incomplete")
            if total_count > 1000:
                raise ApiError(0, "GitHub search exceeds the 1,000-result API cap")
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
        """Fetch and validate one GitHub commit response."""

        encoded = urllib.parse.quote(sha, safe="")
        return _object(
            self._get(f"/repos/{owner}/{repo}/commits/{encoded}"),
            "GitHub commit",
        )

    def compare(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
    ) -> dict[str, object]:
        """Compare two Git revisions through the narrow GitHub client."""

        encoded_base = urllib.parse.quote(base, safe="")
        encoded_head = urllib.parse.quote(head, safe="")
        return _object(
            self._get(
                f"/repos/{owner}/{repo}/compare/{encoded_base}...{encoded_head}",
            ),
            "GitHub compare",
        )

    def trusted_check_external_ids(
        self,
        owner: str,
        repo: str,
        *,
        head_sha: str,
        name: str,
        executor_app_id: int,
    ) -> tuple[str, ...]:
        """Read authorization snapshots created by one exact executor App."""

        if isinstance(executor_app_id, bool) or executor_app_id < 1:
            raise ValueError("executor_app_id must be a positive integer")
        external_ids: set[str] = set()
        page = 1
        while True:
            response = _object(
                self._get(
                    f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs?per_page=100&page={page}",
                ),
                "GitHub check runs",
            )
            runs = _array(response.get("check_runs"), "GitHub check runs")
            for raw_run in runs:
                run = _object(raw_run, "GitHub check run")
                if run.get("name") != name:
                    continue
                app = _object(run.get("app"), "GitHub check run App")
                app_id = app.get("id")
                if isinstance(app_id, bool) or not isinstance(app_id, int):
                    raise ApiError(0, "GitHub check run App omitted id")
                external_id = run.get("external_id")
                if external_id is not None and not isinstance(external_id, str):
                    raise ApiError(0, "GitHub check run external_id is invalid")
                if app_id == executor_app_id and external_id:
                    external_ids.add(external_id)
            if len(runs) < 100:
                return tuple(sorted(external_ids))
            page += 1


def github_cli_token(
    environment: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Read a github.com token from gh without changing authentication state.

    GitHub Actions and alternate hosts are rejected because this adapter is for
    an interactive engineer's existing github.com login. All subprocess errors
    are collapsed into a stable message so credential material cannot leak.
    """

    if environment.get("GITHUB_ACTIONS") == "true":
        raise ReadOnlyGitHubError(
            "local gh authentication is unavailable in GitHub Actions"
        )
    if environment.get("GH_HOST", "github.com") != "github.com":
        raise ReadOnlyGitHubError("local gh authentication supports github.com only")
    try:
        result = run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReadOnlyGitHubError("GitHub CLI credentials are unavailable") from exc
    token = result.stdout.strip() if result.returncode == 0 else ""
    if not token or any(character.isspace() for character in token):
        raise ReadOnlyGitHubError("GitHub CLI credentials are unavailable")
    return token


def gh_github_read_client(
    environment: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> GitHubReadClient:
    """Build a GET-only GitHub evidence client from existing gh credentials."""

    return GitHubReadClient(
        github_cli_token(environment, run=run),
        transport=UrlLibReadTransport(),
    )
