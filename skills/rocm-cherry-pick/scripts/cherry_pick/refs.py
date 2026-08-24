# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Trusted-control-plane hydration of exact GitHub PR and branch refs."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .git_auth import action_git_environment

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class RefHydrationError(RuntimeError):
    """Report ref hydration validation or execution failures."""

    def __init__(self, reason_code: str, message: str) -> None:
        """Initialize a hydration failure without exposing repository credentials."""

        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class HydratedRefs:
    """Represent hydrated refs in the refs contract."""

    head_sha: str
    merge_sha: str
    ordered_commits: tuple[str, ...]
    destination_sha: str


@dataclass(frozen=True)
class HydratedCommitRef:
    """Represent hydrated commit ref in the refs contract."""

    commit_sha: str
    destination_sha: str


@dataclass(frozen=True)
class HydratedPullHeadRef:
    """Represent hydrated pull head ref in the refs contract."""

    pull_number: int
    head_sha: str


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command with deterministic captured output."""

    environment = action_git_environment(os.environ)
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _resolve(repo: Path, revision: str) -> str | None:
    """Resolve a revision to a commit SHA, returning None when Git rejects it."""

    result = _run(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def _require_sha(value: str, name: str) -> None:
    """Require a full lowercase Git commit SHA."""

    if SHA_RE.fullmatch(value) is None:
        raise RefHydrationError(f"{name}_invalid", f"{name} is not a full Git SHA")


def _valid_branch_name(repo: Path, branch: str) -> bool:
    """Return whether a candidate is a safe Git branch name."""

    if not branch or branch.startswith("-"):
        return False
    return _run(repo, "check-ref-format", "--branch", branch).returncode == 0


def hydrate_pull_refs(
    repository: str | Path,
    *,
    remote: str,
    pull_number: int,
    source_branch: str,
    merge_sha: str,
    head_sha: str,
    ordered_commits: tuple[str, ...],
    destination_branch: str,
    destination_sha: str,
) -> HydratedRefs:
    """Fetch exact pull and branch refs, rejecting Git failures or SHA mismatches."""

    repo = Path(repository)
    if pull_number < 1:
        raise RefHydrationError("pull_number_invalid", "pull number must be positive")
    if not _valid_branch_name(repo, source_branch) or not _valid_branch_name(
        repo, destination_branch
    ):
        raise RefHydrationError(
            "branch_invalid", "source or destination branch is invalid"
        )
    for value, name in (
        (merge_sha, "merge_sha"),
        (head_sha, "head_sha"),
        (destination_sha, "destination_sha"),
    ):
        _require_sha(value, name)
    if not ordered_commits:
        raise RefHydrationError("source_commit_missing", "ordered commits are empty")
    for commit in ordered_commits:
        _require_sha(commit, "source_commit")

    refspecs = (
        f"+refs/pull/{pull_number}/head:refs/cherry-pick/pull/{pull_number}/head",
        f"+refs/heads/{source_branch}:refs/cherry-pick/source",
        f"+refs/heads/{destination_branch}:refs/cherry-pick/destination",
    )
    fetch = _run(repo, "fetch", "--no-tags", "--force", remote, *refspecs)
    if fetch.returncode != 0:
        raise RefHydrationError(
            "ref_fetch_failed", fetch.stderr.strip() or "Git ref hydration failed"
        )
    actual_head = _resolve(repo, f"refs/cherry-pick/pull/{pull_number}/head")
    if actual_head != head_sha:
        raise RefHydrationError(
            "pull_head_mismatch",
            "The hydrated pull head does not match GitHub evidence",
        )
    actual_destination = _resolve(repo, "refs/cherry-pick/destination")
    if actual_destination != destination_sha:
        raise RefHydrationError(
            "destination_head_mismatch",
            "The hydrated destination does not match GitHub evidence",
        )
    if _resolve(repo, merge_sha) != merge_sha:
        raise RefHydrationError(
            "merge_commit_missing", "The exact source merge commit is unavailable"
        )
    for commit in ordered_commits:
        if _resolve(repo, commit) != commit:
            raise RefHydrationError(
                "source_commit_missing", "An original source commit is unavailable"
            )
    return HydratedRefs(
        head_sha=head_sha,
        merge_sha=merge_sha,
        ordered_commits=ordered_commits,
        destination_sha=destination_sha,
    )


def hydrate_commit_ref(
    repository: str | Path,
    *,
    remote: str,
    commit_sha: str,
    destination_branch: str,
    destination_sha: str,
) -> HydratedCommitRef:
    """Hydrate one immutable standalone commit and its exact destination."""

    repo = Path(repository)
    _require_sha(commit_sha, "commit_sha")
    _require_sha(destination_sha, "destination_sha")
    if not _valid_branch_name(repo, destination_branch):
        raise RefHydrationError("branch_invalid", "destination branch is invalid")
    fetch = _run(
        repo,
        "fetch",
        "--no-tags",
        "--force",
        remote,
        commit_sha,
        f"+refs/heads/{destination_branch}:refs/cherry-pick/destination",
    )
    if fetch.returncode != 0:
        raise RefHydrationError(
            "commit_fetch_failed",
            fetch.stderr.strip() or "Standalone commit hydration failed",
        )
    if _resolve(repo, commit_sha) != commit_sha:
        raise RefHydrationError(
            "commit_object_missing", "The exact standalone commit is unavailable"
        )
    actual_destination = _resolve(repo, "refs/cherry-pick/destination")
    if actual_destination != destination_sha:
        raise RefHydrationError(
            "destination_head_mismatch",
            "The hydrated destination does not match GitHub evidence",
        )
    return HydratedCommitRef(commit_sha, destination_sha)


def hydrate_pull_head_ref(
    repository: str | Path,
    *,
    remote: str,
    pull_number: int,
    head_sha: str,
) -> HydratedPullHeadRef:
    """Hydrate one open PR head used only as offline coverage evidence."""

    repo = Path(repository)
    if isinstance(pull_number, bool) or pull_number < 1:
        raise RefHydrationError("pull_number_invalid", "pull number must be positive")
    _require_sha(head_sha, "head_sha")
    target_ref = f"refs/cherry-pick/coverage/{pull_number}/head"
    fetch = _run(
        repo,
        "fetch",
        "--no-tags",
        "--force",
        remote,
        f"+refs/pull/{pull_number}/head:{target_ref}",
    )
    if fetch.returncode != 0:
        raise RefHydrationError(
            "coverage_pull_fetch_failed",
            fetch.stderr.strip() or "Coverage pull head hydration failed",
        )
    if _resolve(repo, target_ref) != head_sha:
        raise RefHydrationError(
            "coverage_pull_head_mismatch",
            "The hydrated coverage pull head does not match GitHub evidence",
        )
    return HydratedPullHeadRef(pull_number, head_sha)
