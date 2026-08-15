# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Capability-gated, draft-only cherry-pick transaction."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .clients import ApiError, GitHubClient
from .config import TrainConfig
from .models import Result, Status
from .orchestrator import automation_branch, identity_marker, render_pull_body


BOT_NAME = "ROCm Cherry-Pick Automation"
BOT_EMAIL = "cherry-pick-automation@users.noreply.github.com"
_TEST_CAPABILITY_KEY = object()


@dataclass(frozen=True)
class RemoteWriteCapability:
    """Unforgeable-by-configuration authority required by the writer."""

    _key: object


def test_write_capability() -> RemoteWriteCapability:
    """Return a capability for filesystem/fake-API tests only."""

    return RemoteWriteCapability(_TEST_CAPABILITY_KEY)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def _remote_head(repo: Path, branch: str) -> str | None:
    result = _run(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-remote failed")
    line = result.stdout.strip()
    return line.split()[0] if line else None


def _tree_for_commit(repo: Path, sha: str) -> str | None:
    available = _run(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    if available.returncode != 0:
        fetch = _run(repo, "fetch", "--no-tags", "origin", sha)
        if fetch.returncode != 0:
            return None
    tree = _run(repo, "rev-parse", f"{sha}^{{tree}}")
    return tree.stdout.strip() if tree.returncode == 0 else None


def _result_from(
    plan: Result,
    status: Status,
    reason_code: str,
    message: str,
    *,
    evidence: dict[str, object] | None = None,
    pull_request_url: str | None = None,
) -> Result:
    combined = dict(plan.evidence)
    combined.update(evidence or {})
    return Result(
        status=status,
        reason_code=reason_code,
        message=message,
        evidence=combined,
        source_pr=plan.source_pr,
        source_repository=plan.source_repository,
        train_id=plan.train_id,
        destination_branch=plan.destination_branch,
        pull_request_url=pull_request_url,
    )


def _string_list(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or not item for item in value):
        return None
    return tuple(value)


class DraftWriter:
    """Create one deterministic branch and draft; never ready or merge it."""

    def __init__(
        self,
        github: GitHubClient,
        *,
        capability: RemoteWriteCapability | None = None,
    ) -> None:
        if capability is None or capability._key is not _TEST_CAPABILITY_KEY:
            raise PermissionError("an explicit remote write capability is required")
        self.github = github

    def create(
        self,
        repo_dir: str | Path,
        train: TrainConfig,
        plan: Result,
    ) -> Result:
        repo = Path(repo_dir)
        if train.mode != "create-draft":
            return _result_from(
                plan,
                Status.BLOCKED_POLICY,
                "write_mode_disabled",
                f"Train mode {train.mode!r} does not permit draft creation.",
            )
        if plan.status is not Status.DRAFT_PLANNED:
            return _result_from(
                plan,
                Status.BLOCKED_POLICY,
                "plan_not_writable",
                f"Only draft_planned results are writable, got {plan.status.value}.",
            )
        required = (
            "source_number",
            "source_title",
            "source_body",
            "source_head",
            "source_merge_commit",
            "destination_head",
            "changeset_kind",
            "proof_method",
        )
        missing = [key for key in required if plan.evidence.get(key) is None]
        ordered_commits = _string_list(plan.evidence.get("ordered_commits"))
        if ordered_commits is None:
            missing.append("ordered_commits")
        if (
            missing
            or plan.destination_branch is None
            or plan.train_id is None
            or plan.source_repository is None
            or plan.source_pr is None
        ):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "incomplete_plan",
                "The plan is missing required write evidence: "
                + ", ".join(sorted(set(missing))),
            )
        assert ordered_commits is not None
        source_number = int(plan.evidence["source_number"])
        branch = automation_branch(plan.train_id, source_number)
        owner, repo_name = plan.source_repository.split("/", 1)
        existing_pull = self.github.pull_for_head(
            owner,
            repo_name,
            head=branch,
            base=plan.destination_branch,
        )
        if existing_pull is not None:
            url = existing_pull.get("html_url")
            if isinstance(url, str):
                return _result_from(
                    plan,
                    Status.DRAFT_EXISTS,
                    "draft_pull_exists",
                    "The expected destination draft already exists.",
                    evidence={"automation_branch": branch},
                    pull_request_url=url,
                )

        expected_destination = str(plan.evidence["destination_head"])
        try:
            actual_destination = _remote_head(repo, plan.destination_branch)
        except RuntimeError as exc:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "destination_head_unavailable",
                "Git could not read the destination branch.",
                evidence={"error": str(exc)},
            )
        if actual_destination != expected_destination:
            return _result_from(
                plan,
                Status.BLOCKED_POLICY,
                "destination_head_moved",
                "The destination moved after planning; recompute before writing.",
                evidence={
                    "expected_destination_head": expected_destination,
                    "actual_destination_head": actual_destination,
                },
            )
        try:
            existing_branch = _remote_head(repo, branch)
        except RuntimeError as exc:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "automation_branch_unavailable",
                "Git could not inspect the automation branch.",
                evidence={"error": str(exc)},
            )

        mainline = plan.evidence.get("mainline")
        if mainline is not None and not isinstance(mainline, int):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "invalid_mainline",
                "The plan mainline evidence is invalid.",
            )
        with tempfile.TemporaryDirectory(prefix="cherry-pick-write-") as temp_root:
            worktree = Path(temp_root) / "worktree"
            add = _run(
                repo,
                "worktree",
                "add",
                "--detach",
                str(worktree),
                expected_destination,
            )
            if add.returncode != 0:
                return _result_from(
                    plan,
                    Status.BLOCKED_EVIDENCE,
                    "worktree_creation_failed",
                    "Git could not create the disposable write worktree.",
                    evidence={"stderr": add.stderr.strip()},
                )
            try:
                switch = _run(worktree, "switch", "-c", branch)
                if switch.returncode != 0:
                    return _result_from(
                        plan,
                        Status.BLOCKED_EVIDENCE,
                        "local_branch_creation_failed",
                        "Git could not create the local automation branch.",
                    )
                if (
                    _run(worktree, "config", "user.name", BOT_NAME).returncode != 0
                    or _run(worktree, "config", "user.email", BOT_EMAIL).returncode
                    != 0
                ):
                    return _result_from(
                        plan,
                        Status.BLOCKED_EVIDENCE,
                        "git_identity_configuration_failed",
                        "Git could not configure the automation committer identity.",
                    )
                for commit in ordered_commits:
                    args = ["cherry-pick", "-x"]
                    if mainline is not None:
                        args.extend(["-m", str(mainline)])
                    args.append(commit)
                    cherry_pick = _run(worktree, *args)
                    if cherry_pick.returncode != 0:
                        unmerged = _run(worktree, "ls-files", "-u").stdout.strip()
                        if unmerged:
                            return _result_from(
                                plan,
                                Status.BLOCKED_CONFLICT,
                                "cherry_pick_conflict",
                                "The write preflight conflicted; no branch was pushed.",
                            )
                        return _result_from(
                            plan,
                            Status.BLOCKED_EVIDENCE,
                            "cherry_pick_write_failed",
                            "The write cherry-pick failed; no branch was pushed.",
                            evidence={"stderr": cherry_pick.stderr.strip()},
                        )

                new_head = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                new_tree = _run(worktree, "rev-parse", "HEAD^{tree}").stdout.strip()
                reused_branch = False
                if existing_branch:
                    existing_tree = _tree_for_commit(repo, existing_branch)
                    if existing_tree != new_tree:
                        return _result_from(
                            plan,
                            Status.BLOCKED_POLICY,
                            "automation_branch_mismatch",
                            "The deterministic branch exists with a different tree.",
                            evidence={"existing_branch_head": existing_branch},
                        )
                    reused_branch = True
                else:
                    push = _run(
                        worktree,
                        "push",
                        "origin",
                        f"HEAD:refs/heads/{branch}",
                    )
                    if push.returncode != 0:
                        raced_head = _remote_head(repo, branch)
                        raced_tree = (
                            _tree_for_commit(repo, raced_head) if raced_head else None
                        )
                        if raced_tree == new_tree:
                            existing_branch = raced_head
                            reused_branch = True
                        elif raced_head:
                            return _result_from(
                                plan,
                                Status.BLOCKED_POLICY,
                                "automation_branch_mismatch",
                                "A concurrent writer created a different branch tree.",
                                evidence={"existing_branch_head": raced_head},
                            )
                        else:
                            return _result_from(
                                plan,
                                Status.BLOCKED_EVIDENCE,
                                "branch_push_failed",
                                "The creation-only branch push failed.",
                                evidence={"push_stderr": push.stderr.strip()},
                            )

                marker = identity_marker(
                    plan.source_repository, source_number, plan.train_id
                )
                body = render_pull_body(
                    marker=marker,
                    source_url=plan.source_pr,
                    source_repository=plan.source_repository,
                    source_sha=str(plan.evidence["source_merge_commit"]),
                    source_head=str(plan.evidence["source_head"]),
                    train_id=plan.train_id,
                    destination_branch=plan.destination_branch,
                    destination_head=expected_destination,
                    changeset_kind=str(plan.evidence["changeset_kind"]),
                    ordered_commits=ordered_commits,
                    mainline=mainline,
                    jira_keys=tuple(
                        item
                        for item in plan.evidence.get("jira_keys", [])
                        if isinstance(item, str)
                    ),
                    jira_fix_versions=tuple(
                        item
                        for item in plan.evidence.get("jira_fix_versions", [])
                        if isinstance(item, str)
                    ),
                    unresolved_dependencies=tuple(
                        item
                        for item in plan.evidence.get("unresolved_dependencies", [])
                        if isinstance(item, str)
                    ),
                    proof_method=str(plan.evidence["proof_method"]),
                    source_body=str(plan.evidence["source_body"]),
                )
                try:
                    url = self.github.create_pull(
                        owner,
                        repo_name,
                        title=f"{plan.evidence['source_title']} (#{source_number})",
                        body=body,
                        head=branch,
                        base=plan.destination_branch,
                        draft=True,
                    )
                except ApiError as exc:
                    return _result_from(
                        plan,
                        Status.RETRYABLE_PARTIAL_WRITE,
                        "branch_pushed_pull_missing",
                        "The branch exists, but draft creation must be retried.",
                        evidence={
                            "automation_branch": branch,
                            "automation_head": existing_branch or new_head,
                            "api_status": exc.status,
                        },
                    )
                return _result_from(
                    plan,
                    Status.DRAFT_CREATED,
                    "draft_pull_created",
                    "A draft cherry-pick pull request was created for review.",
                    evidence={
                        "automation_branch": branch,
                        "automation_head": existing_branch or new_head,
                        "reused_existing_branch": reused_branch,
                        "ordered_commits": list(ordered_commits),
                    },
                    pull_request_url=url,
                )
            finally:
                _run(repo, "worktree", "remove", "--force", str(worktree))
