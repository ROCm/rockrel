# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Capability-gated, draft-only cherry-pick transaction."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .clients import ApiError, GitHubClient
from .config import TrainConfig
from .git import cherry_pick_command, conflict_evidence
from .git_auth import action_git_environment
from .models import Result, Status
from .orchestrator import (
    automation_branch,
    coverage_snapshot_sha256,
    identity_marker,
    normalize_coverage_pulls,
    render_pull_body,
)
from .write_authority import (
    DraftWriteAuthority,
    is_valid_authority,
    test_draft_write_authority,
)

BOT_NAME = "ROCm Cherry-Pick Automation"
BOT_EMAIL = "cherry-pick-automation@users.noreply.github.com"
TREE_RE = re.compile(r"[0-9a-f]{40}\Z")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one noninteractive Git command with hooks and prompts disabled."""

    environment = action_git_environment(os.environ)
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def _remote_head(
    repo: Path,
    branch: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Read one exact remote branch head, failing closed on transport errors."""

    if environment is None:
        result = _run(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    else:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{branch}",
            ],
            cwd=repo,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-remote failed")
    line = result.stdout.strip()
    return line.split()[0] if line else None


def _tree_for_commit(
    repo: Path,
    sha: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a commit tree, fetching the exact object when it is absent."""

    run = _run
    if environment is not None:

        def run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
            """Run Git with the caller-provided process-scoped environment."""

            return subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", *args],
                cwd=path,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )

    available = run(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    if available.returncode != 0:
        fetch = run(repo, "fetch", "--no-tags", "origin", sha)
        if fetch.returncode != 0:
            return None
    tree = run(repo, "rev-parse", f"{sha}^{{tree}}")
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
    """Derive a writer result without discarding any planner evidence."""

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
    """Validate a nonempty ordered list of full Git object identifiers."""

    if not isinstance(value, list) or not value:
        return None
    if any(
        not isinstance(item, str) or TREE_RE.fullmatch(item) is None for item in value
    ):
        return None
    return tuple(value)


def _commit_identity_marker(
    repository: str,
    source_commit: str,
    train_id: str,
    plan_fingerprint: str,
) -> str:
    """Build the durable identity marker for a standalone commit request."""

    return (
        f"<!-- cherry-pick:v2:{repository}@{source_commit}:{train_id}:"
        f"{plan_fingerprint} -->"
    )


def _created_draft_matches(
    pull: object,
    *,
    url: str,
    repository: str,
    branch: str,
    destination_branch: str,
    body: str,
) -> bool:
    """Verify that GitHub readback exactly matches the requested draft."""

    if not isinstance(pull, dict):
        return False
    head = pull.get("head")
    base = pull.get("base")
    head_repository = head.get("repo") if isinstance(head, dict) else None
    return (
        pull.get("html_url") == url
        and pull.get("state") == "open"
        and not pull.get("merged_at")
        and pull.get("draft") is True
        and pull.get("body") == body
        and isinstance(head, dict)
        and head.get("ref") == branch
        and isinstance(head_repository, dict)
        and head_repository.get("full_name") == repository
        and isinstance(base, dict)
        and base.get("ref") == destination_branch
    )


@dataclass(frozen=True)
class _SourceIdentity:
    """Hold deterministic branch, marker, and title values for one source."""

    branch: str
    marker: str
    title: str


@dataclass(frozen=True)
class _WritePlan:
    """Hold validated planner evidence required throughout the write transaction."""

    repo: Path
    plan: Result
    owner: str
    repo_name: str
    destination_branch: str
    expected_destination: str
    ordered_commits: tuple[str, ...]
    raw_mainline: object
    identity: _SourceIdentity


@dataclass(frozen=True)
class _ExistingPullState:
    """Record prior pull-request evidence and whether it remains authoritative."""

    pull: dict[str, object] | None
    active: bool


@dataclass(frozen=True)
class _RemoteState:
    """Record initial remote branch state after target and mainline validation."""

    existing_branch: str | None
    mainline: int | None


@dataclass(frozen=True)
class _MaterializedChange:
    """Record the exact commit and tree created in the disposable worktree."""

    head: str
    tree: str


@dataclass(frozen=True)
class _BranchPublication:
    """Record tentative branch publication before mandatory remote readback."""

    candidate_head: str | None
    reused: bool


@dataclass(frozen=True)
class _VerifiedBranch:
    """Record the observed remote branch head and whether it was reused."""

    head: str
    reused: bool


class DraftWriter:
    """Create one deterministic branch and draft; never ready or merge it."""

    def __init__(
        self,
        github: GitHubClient,
        *,
        capability: DraftWriteAuthority | None = None,
        scratch_root: str | Path | None = None,
        git_environment: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize a writer only when an explicit write authority is supplied."""

        if not is_valid_authority(capability):
            raise PermissionError("an explicit remote write capability is required")
        self.github = github
        self.capability = capability
        self.scratch_root = Path(scratch_root) if scratch_root is not None else None
        self.git_environment = dict(git_environment) if git_environment else None

    def _git(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run Git through the writer's optional process-scoped environment."""

        if self.git_environment is None:
            return _run(repo, *args)
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo,
            env=self.git_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )

    @staticmethod
    def _source_identity(
        plan: Result,
        source_kind: object,
        plan_fingerprint: str,
    ) -> _SourceIdentity | Result:
        """Validate source identity and derive its deterministic draft metadata."""

        assert plan.source_repository is not None
        assert plan.train_id is not None
        if source_kind == "commit":
            source_commit = plan.evidence.get("source_commit")
            if (
                not isinstance(source_commit, str)
                or TREE_RE.fullmatch(source_commit) is None
            ):
                return _result_from(
                    plan,
                    Status.BLOCKED_EVIDENCE,
                    "invalid_source_commit",
                    "The standalone source commit is invalid.",
                )
            return _SourceIdentity(
                branch=(
                    f"shared/cherry-pick/{plan.train_id}/"
                    f"commit-{source_commit[:12]}"
                ),
                marker=_commit_identity_marker(
                    plan.source_repository,
                    source_commit,
                    plan.train_id,
                    plan_fingerprint,
                ),
                title=str(plan.evidence["source_title"]),
            )

        source_number = plan.evidence.get("source_number")
        if (
            isinstance(source_number, bool)
            or not isinstance(source_number, int)
            or source_number < 1
        ):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "invalid_source_number",
                "The plan source number is invalid.",
            )
        return _SourceIdentity(
            branch=automation_branch(plan.train_id, source_number),
            marker=identity_marker(
                plan.source_repository,
                source_number,
                plan.train_id,
                plan_fingerprint,
            ),
            title=f"{plan.evidence['source_title']} (#{source_number})",
        )

    def _prepare_write_plan(
        self,
        repo_dir: str | Path,
        train: TrainConfig,
        plan: Result,
    ) -> _WritePlan | Result:
        """Validate immutable planner evidence before any GitHub or Git I/O."""

        plan_fingerprint = plan.evidence.get("plan_fingerprint")
        if not isinstance(plan_fingerprint, str) or not is_valid_authority(
            self.capability, plan_fingerprint
        ):
            return _result_from(
                plan,
                Status.BLOCKED_AUTHORIZATION,
                "write_authority_mismatch",
                "The write authority is not bound to this exact plan.",
            )
        if train.mode != "create-draft":
            return _result_from(
                plan,
                Status.BLOCKED_AUTHORIZATION,
                "write_mode_disabled",
                f"Train mode {train.mode!r} does not permit draft creation.",
            )
        if plan.status is not Status.DRAFT_PLANNED:
            return _result_from(
                plan,
                Status.BLOCKED_AUTHORIZATION,
                "plan_not_writable",
                f"Only draft_planned results are writable, got {plan.status.value}.",
            )
        configured_repository = (
            train.repositories.get(plan.source_repository)
            if plan.source_repository is not None
            else None
        )
        if (
            configured_repository is None
            or plan.destination_branch is None
            or configured_repository.destination_branch != plan.destination_branch
        ):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "configured_destination_mismatch",
                "The reviewed train configuration does not authorize this repository and destination.",
            )
        source_kind = plan.evidence.get("source_kind", "pull_request")
        if source_kind not in {"pull_request", "commit"}:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "invalid_source_kind",
                "The plan source kind is invalid.",
            )
        required = (
            "source_title",
            "source_body",
            "source_head",
            "source_merge_commit",
            "destination_head",
            "changeset_kind",
            "proof_method",
            "planned_tree",
            "coverage_snapshot_sha256",
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
        owner, repo_name = plan.source_repository.split("/", 1)
        source_identity = self._source_identity(plan, source_kind, plan_fingerprint)
        if isinstance(source_identity, Result):
            return source_identity
        return _WritePlan(
            repo=Path(repo_dir),
            plan=plan,
            owner=owner,
            repo_name=repo_name,
            destination_branch=plan.destination_branch,
            expected_destination=str(plan.evidence["destination_head"]),
            ordered_commits=ordered_commits,
            raw_mainline=plan.evidence.get("mainline"),
            identity=source_identity,
        )

    def _existing_pull_state(
        self,
        context: _WritePlan,
    ) -> _ExistingPullState | Result:
        """Load and validate any pull request bound to the deterministic branch."""

        plan = context.plan
        try:
            existing_pull = self.github.pull_for_head(
                context.owner,
                context.repo_name,
                head=context.identity.branch,
                base=context.destination_branch,
            )
        except ApiError as exc:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "existing_pull_evidence_unavailable",
                "GitHub could not establish whether the destination draft exists.",
                evidence={"api_status": exc.status},
            )
        active = existing_pull is not None and (
            existing_pull.get("state") == "open" or bool(existing_pull.get("merged_at"))
        )
        if not active or existing_pull is None:
            return _ExistingPullState(pull=existing_pull, active=False)

        body = existing_pull.get("body")
        exact_identity = isinstance(body, str) and context.identity.marker in body
        if existing_pull.get("state") == "open":
            exact_identity = exact_identity and existing_pull.get("draft") is True
        if not isinstance(existing_pull.get("html_url"), str):
            exact_identity = False
        if not exact_identity:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "existing_pull_identity_mismatch",
                "The deterministic branch is associated with a pull request "
                "that is not this exact draft plan.",
            )
        return _ExistingPullState(pull=existing_pull, active=True)

    def _initial_remote_state(self, context: _WritePlan) -> _RemoteState | Result:
        """Validate destination, branch, and mainline state before materializing."""

        plan = context.plan
        try:
            actual_destination = _remote_head(
                context.repo,
                context.destination_branch,
                environment=self.git_environment,
            )
        except RuntimeError as exc:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "destination_head_unavailable",
                "Git could not read the destination branch.",
                evidence={"error": str(exc)},
            )
        if actual_destination != context.expected_destination:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "destination_head_moved",
                "The destination moved after planning; recompute before writing.",
                evidence={
                    "expected_destination_head": context.expected_destination,
                    "actual_destination_head": actual_destination,
                },
            )
        try:
            existing_branch = _remote_head(
                context.repo,
                context.identity.branch,
                environment=self.git_environment,
            )
        except RuntimeError as exc:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "automation_branch_unavailable",
                "Git could not inspect the automation branch.",
                evidence={"error": str(exc)},
            )
        mainline = context.raw_mainline
        if mainline is not None and (
            isinstance(mainline, bool) or not isinstance(mainline, int) or mainline < 1
        ):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "invalid_mainline",
                "The plan mainline evidence is invalid.",
            )
        assert mainline is None or isinstance(mainline, int)
        return _RemoteState(existing_branch=existing_branch, mainline=mainline)

    def _materialize(
        self,
        worktree: Path,
        context: _WritePlan,
        mainline: int | None,
    ) -> _MaterializedChange | Result:
        """Cherry-pick the proven sequence and verify its exact resulting tree."""

        plan = context.plan
        if (
            self._git(worktree, "config", "user.name", BOT_NAME).returncode != 0
            or self._git(worktree, "config", "user.email", BOT_EMAIL).returncode != 0
        ):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "git_identity_configuration_failed",
                "Git could not configure the automation committer identity.",
            )
        for commit in context.ordered_commits:
            command = cherry_pick_command(commit, mainline, commit_result=True)
            cherry_pick = self._git(worktree, *command)
            if cherry_pick.returncode == 0:
                continue
            unmerged = self._git(worktree, "ls-files", "-u").stdout.strip()
            if unmerged:
                return _result_from(
                    plan,
                    Status.BLOCKED_CONFLICT,
                    "cherry_pick_conflict",
                    "The write preflight conflicted; no branch was pushed.",
                    evidence=conflict_evidence(worktree),
                )
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "cherry_pick_write_failed",
                "The write cherry-pick failed; no branch was pushed.",
                evidence={"stderr": cherry_pick.stderr.strip()},
            )

        head_result = self._git(worktree, "rev-parse", "HEAD")
        tree_result = self._git(worktree, "rev-parse", "HEAD^{tree}")
        head = head_result.stdout.strip()
        tree = tree_result.stdout.strip()
        if (
            head_result.returncode != 0
            or tree_result.returncode != 0
            or TREE_RE.fullmatch(head) is None
            or TREE_RE.fullmatch(tree) is None
        ):
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "materialized_tree_unavailable",
                "Git could not prove the materialized commit and tree.",
            )
        planned_tree = plan.evidence.get("planned_tree")
        if planned_tree is not None and planned_tree != tree:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "planned_tree_mismatch",
                "Write-time materialization does not match the core plan tree.",
                evidence={
                    "expected_tree": planned_tree,
                    "materialized_tree": tree,
                },
            )
        return _MaterializedChange(head=head, tree=tree)

    def _revalidate_write(self, context: _WritePlan) -> Result | None:
        """Recheck mutable destination and pull coverage just before publishing."""

        plan = context.plan
        try:
            current_destination = _remote_head(
                context.repo,
                context.destination_branch,
                environment=self.git_environment,
            )
        except RuntimeError as exc:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "destination_head_unavailable_during_write",
                "Git could not revalidate the destination before branch creation.",
                evidence={"error": str(exc)},
            )
        if current_destination != context.expected_destination:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "destination_head_moved_during_write",
                "The destination moved during materialization; no branch was pushed.",
                evidence={
                    "expected_destination_head": context.expected_destination,
                    "actual_destination_head": current_destination,
                },
            )

        expected_snapshot = plan.evidence.get("coverage_snapshot_sha256")
        if plan.evidence.get("source_kind") == "commit":
            # The core schema does not apply open-PR coverage proof to a
            # standalone commit. Its canonical coverage snapshot is empty, so
            # unrelated pull requests must not become mutable write evidence.
            current_snapshot = coverage_snapshot_sha256([])
        else:
            try:
                current_pulls = self.github.pulls(
                    context.owner,
                    context.repo_name,
                    base=context.destination_branch,
                    state="open",
                )
                candidates = normalize_coverage_pulls(
                    current_pulls,
                    repository=plan.source_repository,
                    destination_branch=context.destination_branch,
                )
                current_snapshot = coverage_snapshot_sha256(candidates)
            except (ApiError, ValueError) as exc:
                return _result_from(
                    plan,
                    Status.BLOCKED_EVIDENCE,
                    "coverage_snapshot_unavailable_during_write",
                    "Open pull request evidence could not be revalidated before branch creation.",
                    evidence={"error_type": type(exc).__name__},
                )
        if current_snapshot != expected_snapshot:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "coverage_snapshot_moved_during_write",
                "Open pull request state changed after planning; no branch was pushed.",
                evidence={
                    "expected_coverage_snapshot_sha256": expected_snapshot,
                    "actual_coverage_snapshot_sha256": current_snapshot,
                },
            )
        return None

    def _publish_or_reuse_branch(
        self,
        worktree: Path,
        context: _WritePlan,
        existing_branch: str | None,
        *,
        active_existing_pull: bool,
        expected_tree: str,
    ) -> _BranchPublication | Result:
        """Reuse an exact branch or publish once with a creation-only lease."""

        plan = context.plan
        branch = context.identity.branch
        if existing_branch:
            existing_tree = _tree_for_commit(
                context.repo,
                existing_branch,
                environment=self.git_environment,
            )
            if existing_tree != expected_tree:
                return _result_from(
                    plan,
                    Status.BLOCKED_EVIDENCE,
                    "automation_branch_mismatch",
                    "The deterministic branch exists with a different tree.",
                    evidence={"existing_branch_head": existing_branch},
                )
            return _BranchPublication(candidate_head=existing_branch, reused=True)
        if active_existing_pull:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "existing_pull_branch_missing",
                "An active identity PR exists but its remote branch is missing.",
            )

        push = self._git(
            worktree,
            "push",
            f"--force-with-lease=refs/heads/{branch}:",
            "origin",
            f"HEAD:refs/heads/{branch}",
        )
        if push.returncode == 0:
            return _BranchPublication(candidate_head=None, reused=False)

        raced_head = _remote_head(
            context.repo,
            branch,
            environment=self.git_environment,
        )
        raced_tree = (
            _tree_for_commit(
                context.repo,
                raced_head,
                environment=self.git_environment,
            )
            if raced_head
            else None
        )
        if raced_tree == expected_tree:
            return _BranchPublication(candidate_head=raced_head, reused=True)
        if raced_head:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "automation_branch_mismatch",
                "A concurrent writer created a different branch tree.",
                evidence={"existing_branch_head": raced_head},
            )
        return _result_from(
            plan,
            Status.BLOCKED_EVIDENCE,
            "branch_push_failed",
            "The creation-only branch push failed.",
            evidence={"push_stderr": push.stderr.strip()},
        )

    def _readback_branch(
        self,
        context: _WritePlan,
        publication: _BranchPublication,
        expected_tree: str,
    ) -> _VerifiedBranch | Result:
        """Verify the exact remote branch after push, reuse, or a resolved race."""

        plan = context.plan
        branch = context.identity.branch
        try:
            readback_head = _remote_head(
                context.repo,
                branch,
                environment=self.git_environment,
            )
        except RuntimeError as exc:
            return _result_from(
                plan,
                Status.RETRYABLE_PARTIAL_WRITE,
                "branch_push_readback_unavailable",
                "The branch write may have succeeded, but its remote state could not be read back.",
                evidence={"automation_branch": branch, "error": str(exc)},
            )
        if readback_head is None:
            return _result_from(
                plan,
                Status.RETRYABLE_PARTIAL_WRITE,
                "branch_push_readback_unavailable",
                "The branch write may have succeeded, but the remote branch was not observable.",
                evidence={"automation_branch": branch},
            )
        readback_tree = _tree_for_commit(
            context.repo,
            readback_head,
            environment=self.git_environment,
        )
        if readback_tree != expected_tree:
            return _result_from(
                plan,
                Status.BLOCKED_EVIDENCE,
                "branch_push_readback_mismatch",
                "The remote automation branch does not match the materialized tree.",
                evidence={
                    "automation_branch": branch,
                    "readback_head": readback_head,
                    "expected_tree": expected_tree,
                    "readback_tree": readback_tree,
                },
            )
        return _VerifiedBranch(head=readback_head, reused=publication.reused)

    @staticmethod
    def _pull_body(
        context: _WritePlan,
        mainline: int | None,
        expected_tree: str,
    ) -> str:
        """Render the immutable audit body, including the exact Git commands."""

        plan = context.plan
        return render_pull_body(
            marker=context.identity.marker,
            source_url=plan.source_pr,
            source_repository=plan.source_repository,
            source_sha=str(plan.evidence["source_merge_commit"]),
            source_head=str(plan.evidence["source_head"]),
            train_id=plan.train_id,
            destination_branch=context.destination_branch,
            destination_head=context.expected_destination,
            changeset_kind=str(plan.evidence["changeset_kind"]),
            ordered_commits=context.ordered_commits,
            mainline=mainline,
            dependencies=tuple(
                item
                for item in plan.evidence.get("dependencies", [])
                if isinstance(item, str)
            ),
            dependency_status=str(plan.evidence.get("dependency_status", "unknown")),
            proof_method=str(plan.evidence["proof_method"]),
            expected_tree=expected_tree,
            source_body=str(plan.evidence["source_body"]),
        )

    def _create_and_verify_draft(
        self,
        context: _WritePlan,
        body: str,
        branch: _VerifiedBranch,
        materialized: _MaterializedChange,
    ) -> Result:
        """Create only a draft and prove its complete identity by API readback."""

        plan = context.plan
        try:
            url = self.github.create_pull(
                context.owner,
                context.repo_name,
                title=context.identity.title,
                body=body,
                head=context.identity.branch,
                base=context.destination_branch,
                draft=True,
            )
        except ApiError as exc:
            return _result_from(
                plan,
                Status.RETRYABLE_PARTIAL_WRITE,
                "branch_pushed_pull_missing",
                "The branch exists, but draft creation must be retried.",
                evidence={
                    "automation_branch": context.identity.branch,
                    "automation_head": branch.head or materialized.head,
                    "api_status": exc.status,
                },
            )
        try:
            created_pull = self.github.pull_for_head(
                context.owner,
                context.repo_name,
                head=context.identity.branch,
                base=context.destination_branch,
            )
        except ApiError as exc:
            return _result_from(
                plan,
                Status.RETRYABLE_PARTIAL_WRITE,
                "draft_created_readback_unavailable",
                "The draft was created, but its state could not be read back.",
                evidence={
                    "automation_branch": context.identity.branch,
                    "automation_head": branch.head or materialized.head,
                    "api_status": exc.status,
                },
                pull_request_url=url,
            )
        if not _created_draft_matches(
            created_pull,
            url=url,
            repository=str(plan.source_repository),
            branch=context.identity.branch,
            destination_branch=context.destination_branch,
            body=body,
        ):
            return _result_from(
                plan,
                Status.RETRYABLE_PARTIAL_WRITE,
                "draft_created_readback_mismatch",
                "The created draft could not be verified as the exact requested transaction.",
                evidence={
                    "automation_branch": context.identity.branch,
                    "automation_head": branch.head or materialized.head,
                },
                pull_request_url=url,
            )
        return _result_from(
            plan,
            Status.DRAFT_CREATED,
            "draft_pull_created",
            "A draft cherry-pick pull request was created for review.",
            evidence={
                "automation_branch": context.identity.branch,
                "automation_head": branch.head or materialized.head,
                "reused_existing_branch": branch.reused,
                "ordered_commits": list(context.ordered_commits),
                "draft_readback_verified": True,
            },
            pull_request_url=url,
        )

    def _finish_draft(
        self,
        context: _WritePlan,
        pull_state: _ExistingPullState,
        remote_state: _RemoteState,
        materialized: _MaterializedChange,
        branch: _VerifiedBranch,
    ) -> Result:
        """Return an exact prior draft or create and verify a new draft."""

        existing_pull = pull_state.pull
        if pull_state.active and existing_pull is not None:
            existing_url = existing_pull.get("html_url")
            if isinstance(existing_url, str):
                return _result_from(
                    context.plan,
                    Status.DRAFT_EXISTS,
                    "draft_pull_exists",
                    "The expected destination draft and branch tree already exist.",
                    evidence={
                        "automation_branch": context.identity.branch,
                        "automation_head": branch.head or materialized.head,
                        "reused_existing_branch": True,
                    },
                    pull_request_url=existing_url,
                )
        body = self._pull_body(context, remote_state.mainline, materialized.tree)
        return self._create_and_verify_draft(context, body, branch, materialized)

    def create(
        self,
        repo_dir: str | Path,
        train: TrainConfig,
        plan: Result,
    ) -> Result:
        """Execute one fail-closed, idempotent, draft-only write transaction."""

        prepared = self._prepare_write_plan(repo_dir, train, plan)
        if isinstance(prepared, Result):
            return prepared
        context = prepared
        branch = context.identity.branch
        expected_destination = context.expected_destination
        pull_state = self._existing_pull_state(context)
        if isinstance(pull_state, Result):
            return pull_state
        remote_state = self._initial_remote_state(context)
        if isinstance(remote_state, Result):
            return remote_state
        existing_pull = pull_state.pull
        active_existing_pull = pull_state.active
        existing_branch = remote_state.existing_branch
        mainline = remote_state.mainline
        if self.scratch_root is not None:
            self.scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="cherry-pick-write-",
            dir=self.scratch_root,
        ) as temp_root:
            worktree = Path(temp_root) / "worktree"
            add = self._git(
                context.repo,
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
                materialized = self._materialize(worktree, context, mainline)
                if isinstance(materialized, Result):
                    return materialized
                new_tree = materialized.tree
                drift = self._revalidate_write(context)
                if drift is not None:
                    return drift
                publication = self._publish_or_reuse_branch(
                    worktree,
                    context,
                    existing_branch,
                    active_existing_pull=active_existing_pull,
                    expected_tree=new_tree,
                )
                if isinstance(publication, Result):
                    return publication
                verified_branch = self._readback_branch(context, publication, new_tree)
                if isinstance(verified_branch, Result):
                    return verified_branch
                return self._finish_draft(
                    context,
                    pull_state,
                    remote_state,
                    materialized,
                    verified_branch,
                )
            finally:
                self._git(context.repo, "worktree", "remove", "--force", str(worktree))
