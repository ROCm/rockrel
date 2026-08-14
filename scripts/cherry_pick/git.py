"""Side-effect-contained Git planning for Cherry-pick requests."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .models import Result, Status


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


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool | None:
    result = _run(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _git_result(
    status: Status,
    reason_code: str,
    message: str,
    *,
    source: str | None = None,
    target: str | None = None,
    extra: dict[str, object] | None = None,
) -> Result:
    evidence: dict[str, object] = {
        "source_commit": source,
        "destination_head": target,
    }
    evidence.update(extra or {})
    return Result(
        status=status,
        reason_code=reason_code,
        message=message,
        evidence=evidence,
    )


def evaluate_cherry_pick(
    repo: str | Path,
    source_revision: str,
    target_revision: str,
) -> Result:
    """Plan an aggregate cherry-pick without changing the caller's checkout."""

    repo_path = Path(repo)
    source = _resolve(repo_path, source_revision)
    if source is None:
        return _git_result(
            Status.BLOCKED,
            "source_commit_missing",
            "The source commit is not available in the repository.",
        )
    target = _resolve(repo_path, target_revision)
    if target is None:
        return _git_result(
            Status.BLOCKED,
            "target_ref_missing",
            "The target ref is not available in the repository.",
            source=source,
        )
    contained = _is_ancestor(repo_path, source, target)
    if contained is None:
        return _git_result(
            Status.BLOCKED,
            "ancestry_check_failed",
            "Git could not establish source-to-target ancestry.",
            source=source,
            target=target,
        )
    if contained:
        return _git_result(
            Status.ALREADY_CONTAINED,
            "source_ancestor_of_target",
            "The exact source commit is reachable from the target.",
            source=source,
            target=target,
        )

    parents = _run(repo_path, "rev-list", "--parents", "-n", "1", source)
    if parents.returncode != 0:
        return _git_result(
            Status.BLOCKED,
            "source_parent_read_failed",
            "Git could not read the source commit parents.",
            source=source,
            target=target,
        )
    parent_count = max(0, len(parents.stdout.split()) - 1)
    mainline = 1 if parent_count > 1 else None

    with tempfile.TemporaryDirectory(prefix="cherry-pick-plan-") as temp_root:
        worktree = Path(temp_root) / "worktree"
        add = _run(repo_path, "worktree", "add", "--detach", str(worktree), target)
        if add.returncode != 0:
            return _git_result(
                Status.BLOCKED,
                "worktree_creation_failed",
                "Git could not create a disposable target worktree.",
                source=source,
                target=target,
                extra={"stderr": add.stderr.strip()},
            )
        try:
            args = ["cherry-pick", "--no-commit"]
            if mainline is not None:
                args.extend(["-m", str(mainline)])
            args.append(source)
            trial = _run(worktree, *args)
            unmerged = _run(worktree, "ls-files", "-u").stdout.strip()
            status = _run(worktree, "status", "--porcelain").stdout.strip()
            if trial.returncode != 0 and unmerged:
                return _git_result(
                    Status.MANUAL_RESOLUTION_REQUIRED,
                    "cherry_pick_conflict",
                    "The aggregate cherry-pick conflicts with the target.",
                    source=source,
                    target=target,
                    extra={"mainline": mainline, "conflicted": True},
                )
            if not status:
                return _git_result(
                    Status.ALREADY_CONTAINED,
                    "empty_trial_application",
                    "Applying the aggregate source change produces an empty tree change.",
                    source=source,
                    target=target,
                    extra={"mainline": mainline, "patch_equivalent": True},
                )
            if trial.returncode != 0:
                return _git_result(
                    Status.BLOCKED,
                    "trial_application_failed",
                    "The trial cherry-pick failed without a classifiable conflict.",
                    source=source,
                    target=target,
                    extra={
                        "mainline": mainline,
                        "stderr": trial.stderr.strip(),
                    },
                )
            return _git_result(
                Status.CHERRY_PICK_REQUIRED,
                "clean_trial_application",
                "The aggregate source change applies cleanly and is non-empty.",
                source=source,
                target=target,
                extra={"mainline": mainline, "patch_equivalent": False},
            )
        finally:
            _run(repo_path, "worktree", "remove", "--force", str(worktree))


def classify_gitlink(
    component_repo: str | Path,
    desired_revision: str,
    target_revision: str,
) -> Result:
    """Classify the directional relationship between desired and target pins."""

    repo_path = Path(component_repo)
    desired = _resolve(repo_path, desired_revision)
    target = _resolve(repo_path, target_revision)
    evidence = {
        "source_desired_pin": desired,
        "target_current_pin": target,
    }
    if desired is None or target is None:
        return Result(
            status=Status.BLOCKED,
            reason_code="gitlink_object_missing",
            message="One or both gitlink commits are unavailable.",
            evidence=evidence,
        )
    if desired == target:
        return Result(
            status=Status.ALREADY_CONTAINED,
            reason_code="gitlink_pins_equal",
            message="The target already uses the desired component pin.",
            evidence=evidence,
        )
    target_contains_desired = _is_ancestor(repo_path, desired, target)
    desired_contains_target = _is_ancestor(repo_path, target, desired)
    if target_contains_desired is None or desired_contains_target is None:
        return Result(
            status=Status.BLOCKED,
            reason_code="gitlink_ancestry_unknown",
            message="Git could not establish the gitlink ancestry direction.",
            evidence=evidence,
        )
    if target_contains_desired:
        return Result(
            status=Status.ALREADY_CONTAINED,
            reason_code="target_pin_contains_desired",
            message="The target component pin descends from the desired pin.",
            evidence=evidence,
        )
    if desired_contains_target:
        return Result(
            status=Status.CHERRY_PICK_REQUIRED,
            reason_code="desired_pin_ahead_of_target",
            message="The desired component pin descends from the target pin.",
            evidence=evidence,
        )
    return Result(
        status=Status.MANUAL_RESOLUTION_REQUIRED,
        reason_code="gitlink_histories_diverged",
        message="Desired and target component pins have diverged.",
        evidence=evidence,
    )
