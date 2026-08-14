"""The single draft-only write transaction for Express Train automation."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .clients import GitHubClient, parse_pull_request_url
from .config import TrainConfig
from .models import Result, Status
from .orchestrator import automation_branch, identity_marker, render_pull_body


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


def _result_from(
    plan: Result,
    status: Status,
    reason_code: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
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
        train_id=plan.train_id,
        target_branch=plan.target_branch,
        pull_request_url=pull_request_url,
    )


class DraftWriter:
    """Create a deterministic branch and draft PR; never make it ready or merge."""

    def __init__(self, github: GitHubClient) -> None:
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
                Status.BLOCKED,
                "write_mode_disabled",
                f"Train mode {train.mode!r} does not permit draft creation.",
            )
        if plan.status is not Status.CHERRY_PICK_REQUIRED:
            return _result_from(
                plan,
                Status.BLOCKED,
                "plan_not_writable",
                f"Only cherry_pick_required plans are writable, got {plan.status.value}.",
            )

        required = (
            "source_repository",
            "source_number",
            "source_title",
            "source_body",
            "source_merge_commit",
            "target_head",
        )
        missing = [key for key in required if plan.evidence.get(key) is None]
        if missing or plan.target_branch is None or plan.train_id is None:
            return _result_from(
                plan,
                Status.BLOCKED,
                "incomplete_plan",
                f"The plan is missing required write evidence: {', '.join(missing)}",
            )

        source_sha = str(plan.evidence["source_merge_commit"])
        expected_target = str(plan.evidence["target_head"])
        actual_target = _remote_head(repo, plan.target_branch)
        if actual_target != expected_target:
            return _result_from(
                plan,
                Status.BLOCKED,
                "target_head_moved",
                "The target branch moved after planning; recompute before writing.",
                evidence={
                    "expected_target_head": expected_target,
                    "actual_target_head": actual_target,
                },
            )

        source_number = int(plan.evidence["source_number"])
        branch = automation_branch(plan.train_id, source_number)
        existing_branch = _remote_head(repo, branch)
        parent_result = _run(repo, "rev-list", "--parents", "-n", "1", source_sha)
        if parent_result.returncode != 0:
            return _result_from(
                plan,
                Status.BLOCKED,
                "source_parent_read_failed",
                "Git could not read the source commit before writing.",
            )
        mainline = 1 if len(parent_result.stdout.split()) - 1 > 1 else None

        with tempfile.TemporaryDirectory(prefix="express-train-write-") as temp_root:
            worktree = Path(temp_root) / "worktree"
            add = _run(repo, "worktree", "add", "--detach", str(worktree), expected_target)
            if add.returncode != 0:
                return _result_from(
                    plan,
                    Status.BLOCKED,
                    "worktree_creation_failed",
                    "Git could not create the disposable write worktree.",
                )
            try:
                switch = _run(worktree, "switch", "-c", branch)
                if switch.returncode != 0:
                    return _result_from(
                        plan,
                        Status.BLOCKED,
                        "local_branch_creation_failed",
                        "Git could not create the automation branch.",
                    )
                args = ["cherry-pick", "-x"]
                if mainline is not None:
                    args.extend(["-m", str(mainline)])
                args.append(source_sha)
                cherry_pick = _run(worktree, *args)
                if cherry_pick.returncode != 0:
                    unmerged = _run(worktree, "ls-files", "-u").stdout.strip()
                    status = (
                        Status.MANUAL_RESOLUTION_REQUIRED if unmerged else Status.BLOCKED
                    )
                    reason = (
                        "cherry_pick_conflict" if unmerged else "cherry_pick_write_failed"
                    )
                    return _result_from(
                        plan,
                        status,
                        reason,
                        "The write cherry-pick conflicted; no branch was pushed."
                        if unmerged
                        else "The write cherry-pick failed; no branch was pushed.",
                    )

                new_head = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                new_tree = _run(worktree, "rev-parse", "HEAD^{tree}").stdout.strip()
                reused_branch = False
                if existing_branch:
                    fetch = _run(repo, "fetch", "origin", existing_branch)
                    existing_tree = (
                        _run(repo, "rev-parse", f"{existing_branch}^{{tree}}").stdout.strip()
                        if fetch.returncode == 0
                        else ""
                    )
                    if existing_tree != new_tree:
                        return _result_from(
                            plan,
                            Status.BLOCKED,
                            "automation_branch_mismatch",
                            "The deterministic remote branch exists with a different tree.",
                            evidence={"existing_branch_head": existing_branch},
                        )
                    reused_branch = True
                else:
                    push = _run(
                        worktree,
                        "push",
                        "origin",
                        f"HEAD:refs/heads/{branch}",
                        f"--force-with-lease=refs/heads/{branch}:",
                    )
                    if push.returncode != 0:
                        return _result_from(
                            plan,
                            Status.BLOCKED,
                            "branch_push_failed",
                            "The automation branch push failed without creating a PR.",
                            evidence={"push_stderr": push.stderr.strip()},
                        )

                repository = str(plan.evidence["source_repository"])
                owner, repo_name = repository.split("/", 1)
                marker = identity_marker(repository, source_number, plan.train_id)
                body = render_pull_body(
                    marker=marker,
                    source_url=plan.source_pr or "",
                    source_sha=source_sha,
                    train_id=plan.train_id,
                    source_body=str(plan.evidence["source_body"]),
                )
                url = self.github.create_pull(
                    owner,
                    repo_name,
                    title=f"{plan.evidence['source_title']} (#{source_number})",
                    body=body,
                    head=branch,
                    base=plan.target_branch,
                )
                return _result_from(
                    plan,
                    Status.DRAFT_CREATED,
                    "draft_pull_created",
                    "A draft cherry-pick pull request was created for operator review.",
                    evidence={
                        "automation_branch": branch,
                        "automation_head": existing_branch or new_head,
                        "reused_existing_branch": reused_branch,
                    },
                    pull_request_url=url,
                )
            finally:
                _run(repo, "worktree", "remove", "--force", str(worktree))
