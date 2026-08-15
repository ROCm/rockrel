# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Side-effect-contained Git proof and planning for cherry-pick requests."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .models import Result, Status


class ChangesetKind(StrEnum):
    SINGLE = "single"
    SQUASH = "squash"
    MERGE_COMMIT = "merge_commit"
    REBASE_RANGE = "rebase_range"


class ChangesetError(RuntimeError):
    """The complete merged PR changeset could not be proven."""


@dataclass(frozen=True)
class ChangesetProof:
    method: str
    source_head: str
    source_merge_commit: str
    original_commits: tuple[str, ...]
    aggregate_patch_id: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Changeset:
    kind: ChangesetKind
    commits: tuple[str, ...]
    aggregate_base: str
    aggregate_head: str
    mainline: int | None
    proof: ChangesetProof


def _run(
    repo: Path,
    *args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def _resolve(repo: Path, revision: str) -> str | None:
    result = _run(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def _parents(repo: Path, commit: str) -> tuple[str, ...]:
    result = _run(repo, "rev-list", "--parents", "-n", "1", commit)
    fields = result.stdout.split()
    if result.returncode != 0 or not fields:
        raise ChangesetError(f"could not prove parents for {commit}")
    return tuple(fields[1:])


def _tree(repo: Path, revision: str) -> str:
    result = _run(repo, "rev-parse", f"{revision}^{{tree}}")
    if result.returncode != 0:
        raise ChangesetError(f"could not prove tree for {revision}")
    return result.stdout.strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool | None:
    result = _run(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _patch_id(repo: Path, patch: str) -> str | None:
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=repo,
        check=False,
        text=True,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ChangesetError("could not prove normalized patch identity")
    fields = result.stdout.split()
    return fields[0] if fields else None


def _diff_patch_id(repo: Path, base: str, head: str) -> str | None:
    result = _run(repo, "diff", "--binary", "--full-index", base, head)
    if result.returncode != 0:
        raise ChangesetError("could not prove aggregate tree delta")
    return _patch_id(repo, result.stdout)


def _commit_patch_id(repo: Path, commit: str) -> str | None:
    parents = _parents(repo, commit)
    if len(parents) != 1:
        raise ChangesetError("could not prove a non-linear rebase commit")
    return _diff_patch_id(repo, parents[0], commit)


def _merge_base(repo: Path, first: str, second: str) -> str:
    result = _run(repo, "merge-base", first, second)
    if result.returncode != 0 or not result.stdout.strip():
        raise ChangesetError("could not prove aggregate merge base")
    return result.stdout.strip()


def prove_changeset(
    repo: str | Path,
    merged_revision: str,
    source_head_revision: str,
    source_commits: tuple[str, ...],
) -> Changeset:
    """Prove the full merged representation of one canonical source PR."""

    repo_path = Path(repo)
    merged = _resolve(repo_path, merged_revision)
    source_head = _resolve(repo_path, source_head_revision)
    originals = tuple(_resolve(repo_path, item) for item in source_commits)
    if merged is None or source_head is None or not source_commits:
        raise ChangesetError("could not prove required source objects")
    if any(item is None for item in originals):
        raise ChangesetError("could not prove every original source commit")
    original_commits = tuple(str(item) for item in originals)
    if original_commits[-1] != source_head:
        raise ChangesetError("could not prove source head is the final PR commit")

    merged_parents = _parents(repo_path, merged)
    if len(merged_parents) == 2:
        if merged_parents[1] != source_head and _tree(
            repo_path, merged_parents[1]
        ) != _tree(repo_path, source_head):
            raise ChangesetError(
                "could not prove merge second parent matches source head"
            )
        aggregate_patch = _diff_patch_id(repo_path, merged_parents[0], merged)
        return Changeset(
            kind=ChangesetKind.MERGE_COMMIT,
            commits=(merged,),
            aggregate_base=merged_parents[0],
            aggregate_head=merged,
            mainline=1,
            proof=ChangesetProof(
                method="merge_second_parent",
                source_head=source_head,
                source_merge_commit=merged,
                original_commits=original_commits,
                aggregate_patch_id=aggregate_patch,
            ),
        )
    if len(merged_parents) != 1:
        raise ChangesetError("could not prove an octopus or root merge representation")

    merged_parent = merged_parents[0]
    source_base = _merge_base(repo_path, source_head, merged_parent)
    source_aggregate = _diff_patch_id(repo_path, source_base, source_head)
    merged_aggregate = _diff_patch_id(repo_path, merged_parent, merged)
    if source_aggregate == merged_aggregate:
        kind = (
            ChangesetKind.SINGLE if len(original_commits) == 1 else ChangesetKind.SQUASH
        )
        return Changeset(
            kind=kind,
            commits=(merged,),
            aggregate_base=merged_parent,
            aggregate_head=merged,
            mainline=None,
            proof=ChangesetProof(
                method="normalized_patch_identity",
                source_head=source_head,
                source_merge_commit=merged,
                original_commits=original_commits,
                aggregate_patch_id=merged_aggregate,
            ),
        )

    count = len(original_commits)
    range_result = _run(repo_path, "rev-list", f"--max-count={count}", merged)
    candidates = tuple(reversed(range_result.stdout.splitlines()))
    base_result = _run(repo_path, "rev-parse", f"{merged}~{count}")
    if (
        range_result.returncode != 0
        or len(candidates) != count
        or base_result.returncode != 0
    ):
        raise ChangesetError("could not prove complete rebase range")
    candidate_base = base_result.stdout.strip()
    original_patch_ids = tuple(
        _commit_patch_id(repo_path, commit) for commit in original_commits
    )
    candidate_patch_ids = tuple(
        _commit_patch_id(repo_path, commit) for commit in candidates
    )
    rebase_source_base = _merge_base(repo_path, source_head, candidate_base)
    source_range_patch = _diff_patch_id(repo_path, rebase_source_base, source_head)
    candidate_range_patch = _diff_patch_id(repo_path, candidate_base, merged)
    if (
        original_patch_ids != candidate_patch_ids
        or source_range_patch != candidate_range_patch
    ):
        raise ChangesetError("could not prove complete rebased changeset")
    return Changeset(
        kind=ChangesetKind.REBASE_RANGE,
        commits=candidates,
        aggregate_base=candidate_base,
        aggregate_head=merged,
        mainline=None,
        proof=ChangesetProof(
            method="ordered_patch_identity",
            source_head=source_head,
            source_merge_commit=merged,
            original_commits=original_commits,
            aggregate_patch_id=candidate_range_patch,
        ),
    )


def _git_result(
    status: Status,
    reason_code: str,
    message: str,
    *,
    changeset: Changeset | None = None,
    target: str | None = None,
    extra: dict[str, object] | None = None,
) -> Result:
    evidence: dict[str, object] = {"destination_head": target}
    if changeset is not None:
        evidence.update(
            {
                "source_commit": changeset.aggregate_head,
                "changeset_kind": changeset.kind.value,
                "ordered_commits": list(changeset.commits),
                "aggregate_base": changeset.aggregate_base,
                "aggregate_head": changeset.aggregate_head,
                "mainline": changeset.mainline,
                "proof_method": changeset.proof.method,
                "changeset_proof": changeset.proof.as_dict(),
            }
        )
    evidence.update(extra or {})
    return Result(
        status=status,
        reason_code=reason_code,
        message=message,
        evidence=evidence,
    )


def evaluate_changeset(
    repo: str | Path,
    changeset: Changeset,
    target_revision: str,
) -> Result:
    """Apply a proven changeset in a disposable worktree."""

    repo_path = Path(repo)
    target = _resolve(repo_path, target_revision)
    if target is None:
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "target_ref_missing",
            "The destination ref is unavailable.",
            changeset=changeset,
        )
    resolved_commits = tuple(_resolve(repo_path, item) for item in changeset.commits)
    if any(item is None for item in resolved_commits):
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "source_commit_missing",
            "One or more proven changeset commits are unavailable.",
            changeset=changeset,
            target=target,
        )
    ancestry = tuple(
        _is_ancestor(repo_path, str(commit), target) for commit in resolved_commits
    )
    if any(item is None for item in ancestry):
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "ancestry_check_failed",
            "Git could not establish changeset-to-destination ancestry.",
            changeset=changeset,
            target=target,
        )
    if all(ancestry):
        return _git_result(
            Status.ALREADY_CONTAINED,
            "complete_changeset_ancestor",
            "Every changeset application unit is reachable from the destination.",
            changeset=changeset,
            target=target,
        )
    if any(ancestry):
        return _git_result(
            Status.BLOCKED_AMBIGUOUS_CHANGESET,
            "partial_changeset_containment",
            "Only part of the complete changeset is reachable from the destination.",
            changeset=changeset,
            target=target,
        )

    with tempfile.TemporaryDirectory(prefix="cherry-pick-plan-") as temp_root:
        worktree = Path(temp_root) / "worktree"
        add = _run(repo_path, "worktree", "add", "--detach", str(worktree), target)
        if add.returncode != 0:
            return _git_result(
                Status.BLOCKED_EVIDENCE,
                "worktree_creation_failed",
                "Git could not create a disposable destination worktree.",
                changeset=changeset,
                target=target,
                extra={"stderr": add.stderr.strip()},
            )
        try:
            failure: subprocess.CompletedProcess[str] | None = None
            empty_units = 0
            applied_units = 0
            for commit in changeset.commits:
                before_tree = _run(worktree, "write-tree").stdout.strip()
                args = ["cherry-pick", "--no-commit"]
                if changeset.mainline is not None:
                    args.extend(["-m", str(changeset.mainline)])
                args.append(commit)
                trial = _run(worktree, *args)
                current_unmerged = _run(worktree, "ls-files", "-u").stdout.strip()
                if current_unmerged:
                    failure = trial
                    break
                after_tree = _run(worktree, "write-tree").stdout.strip()
                if before_tree == after_tree:
                    empty_units += 1
                    # An empty single-commit invocation may leave sequencer
                    # metadata. Quit clears only that metadata and preserves
                    # previously staged changes for the remaining units.
                    _run(worktree, "cherry-pick", "--quit")
                    continue
                applied_units += 1
                if trial.returncode != 0:
                    failure = trial
                    break
            unmerged = _run(worktree, "ls-files", "-u").stdout.strip()
            status = _run(worktree, "status", "--porcelain").stdout.strip()
            if unmerged:
                return _git_result(
                    Status.BLOCKED_CONFLICT,
                    "cherry_pick_conflict",
                    "The complete proven changeset conflicts with the destination.",
                    changeset=changeset,
                    target=target,
                    extra={"conflicted": True},
                )
            if empty_units == len(changeset.commits):
                return _git_result(
                    Status.ALREADY_CONTAINED,
                    "complete_changeset_already_applied",
                    "Applying the complete proven changeset produces no tree change.",
                    changeset=changeset,
                    target=target,
                    extra={"patch_equivalent": True},
                )
            if empty_units and applied_units:
                return _git_result(
                    Status.BLOCKED_AMBIGUOUS_CHANGESET,
                    "partial_changeset_containment",
                    "Only part of the complete changeset is patch-equivalent to the destination.",
                    changeset=changeset,
                    target=target,
                    extra={
                        "empty_application_units": empty_units,
                        "applied_application_units": applied_units,
                    },
                )
            if failure is not None:
                return _git_result(
                    Status.BLOCKED_EVIDENCE,
                    "trial_application_failed",
                    "The trial failed without a classifiable conflict.",
                    changeset=changeset,
                    target=target,
                    extra={"stderr": failure.stderr.strip()},
                )
            if not status:
                return _git_result(
                    Status.BLOCKED_EVIDENCE,
                    "trial_tree_state_unexpected",
                    "The trial completed without a classifiable tree state.",
                    changeset=changeset,
                    target=target,
                )
            return _git_result(
                Status.DRAFT_PLANNED,
                "clean_trial_application",
                "The complete proven changeset applies cleanly and is non-empty.",
                changeset=changeset,
                target=target,
                extra={"patch_equivalent": False},
            )
        finally:
            _run(repo_path, "worktree", "remove", "--force", str(worktree))


def evaluate_cherry_pick(
    repo: str | Path,
    source_revision: str,
    target_revision: str,
) -> Result:
    """Compatibility wrapper for an already selected aggregate commit."""

    repo_path = Path(repo)
    source = _resolve(repo_path, source_revision)
    if source is None:
        return Result(
            status=Status.BLOCKED_EVIDENCE,
            reason_code="source_commit_missing",
            message="The source commit is unavailable.",
        )
    parents = _parents(repo_path, source)
    mainline = 1 if len(parents) == 2 else None
    if len(parents) not in (1, 2):
        return Result(
            status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
            reason_code="unsupported_parent_count",
            message="The aggregate commit parent count is unsupported.",
        )
    changeset = Changeset(
        kind=(
            ChangesetKind.MERGE_COMMIT if mainline is not None else ChangesetKind.SINGLE
        ),
        commits=(source,),
        aggregate_base=parents[0],
        aggregate_head=source,
        mainline=mainline,
        proof=ChangesetProof(
            method="preselected_aggregate_commit",
            source_head=source,
            source_merge_commit=source,
            original_commits=(source,),
            aggregate_patch_id=_diff_patch_id(repo_path, parents[0], source),
        ),
    )
    return evaluate_changeset(repo_path, changeset, target_revision)


def classify_gitlink(
    component_repo: str | Path,
    desired_revision: str,
    target_revision: str,
) -> Result:
    """Classify the directional relationship between desired and target pins."""

    repo_path = Path(component_repo)
    desired = _resolve(repo_path, desired_revision)
    target = _resolve(repo_path, target_revision)
    evidence = {"source_desired_pin": desired, "target_current_pin": target}
    if desired is None or target is None:
        return Result(
            status=Status.BLOCKED_EVIDENCE,
            reason_code="gitlink_object_missing",
            message="One or both gitlink commits are unavailable.",
            evidence=evidence,
        )
    if desired == target:
        return Result(
            status=Status.ALREADY_CONTAINED,
            reason_code="gitlink_pins_equal",
            message="The destination already uses the desired component pin.",
            evidence=evidence,
        )
    target_contains_desired = _is_ancestor(repo_path, desired, target)
    desired_contains_target = _is_ancestor(repo_path, target, desired)
    if target_contains_desired is None or desired_contains_target is None:
        return Result(
            status=Status.BLOCKED_EVIDENCE,
            reason_code="gitlink_ancestry_unknown",
            message="Git could not establish gitlink ancestry direction.",
            evidence=evidence,
        )
    if target_contains_desired:
        return Result(
            status=Status.ALREADY_CONTAINED,
            reason_code="target_pin_contains_desired",
            message="The destination component pin descends from the desired pin.",
            evidence=evidence,
        )
    if desired_contains_target:
        return Result(
            status=Status.DRAFT_PLANNED,
            reason_code="desired_pin_ahead_of_target",
            message="The desired component pin descends from the destination pin.",
            evidence=evidence,
        )
    return Result(
        status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
        reason_code="gitlink_histories_diverged",
        message="Desired and destination component pins have diverged.",
        evidence=evidence,
    )
