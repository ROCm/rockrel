# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Side-effect-contained Git proof and planning for cherry-pick requests."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path

from .compat import StrEnum
from .models import Result, Status


class ChangesetKind(StrEnum):
    """Classify each Git representation supported by the proof engine."""

    SINGLE = "single"
    SQUASH = "squash"
    MERGE_COMMIT = "merge_commit"
    REBASE_RANGE = "rebase_range"


class ChangesetError(RuntimeError):
    """The complete merged PR changeset could not be proven."""


class WorktreeStateError(RuntimeError):
    """A replay-owned worktree could not be proven clean and reusable."""

    def __init__(self, reason_code: str, message: str, stderr: str = "") -> None:
        """Record the stable reason and Git diagnostic for replay failure."""

        super().__init__(message)
        self.reason_code = reason_code
        self.stderr = stderr


@dataclass(frozen=True)
class ChangesetProof:
    """Describe immutable evidence proving the complete source changeset."""

    method: str
    source_head: str
    source_merge_commit: str
    original_commits: tuple[str, ...]
    aggregate_patch_id: str | None

    def as_dict(self) -> dict[str, object]:
        """Serialize proof evidence into its stable JSON-compatible form."""

        return asdict(self)


@dataclass(frozen=True)
class Changeset:
    """Bind ordered application units to their aggregate Git proof."""

    kind: ChangesetKind
    commits: tuple[str, ...]
    aggregate_base: str
    aggregate_head: str
    mainline: int | None
    proof: ChangesetProof


@dataclass(frozen=True)
class SourceIdentity:
    """Canonical identity used to prove a prior destination application."""

    repository: str
    pull_number: int
    merge_commit: str

    def __post_init__(self) -> None:
        """Reject source pull identities that are not canonical and immutable."""

        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is None:
            raise ValueError("source repository must be an OWNER/REPO slug")
        if self.pull_number < 1:
            raise ValueError("source pull number must be positive")
        if re.fullmatch(r"[0-9a-f]{40}", self.merge_commit) is None:
            raise ValueError("source merge commit must be a full lowercase SHA")


@dataclass(frozen=True)
class CommitIdentity:
    """Canonical identity for a reviewed standalone prerequisite commit."""

    repository: str
    commit_sha: str

    def __post_init__(self) -> None:
        """Reject standalone commit identities that are not canonical and immutable."""

        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is None:
            raise ValueError("source repository must be an OWNER/REPO slug")
        if re.fullmatch(r"[0-9a-f]{40}", self.commit_sha) is None:
            raise ValueError("source commit must be a full lowercase SHA")


@dataclass(frozen=True)
class _ResolvedEvaluation:
    """Hold immutable Git objects resolved before an evaluation trial."""

    target: str
    destination_tree: str
    commits: tuple[str, ...]


@dataclass(frozen=True)
class _TrialOutcome:
    """Capture the complete observable state of a changeset trial."""

    failure: subprocess.CompletedProcess[str] | None
    empty_units: int
    applied_units: int
    unmerged: str
    status: str


def cherry_pick_command(
    commit: str,
    mainline: int | None,
    *,
    commit_result: bool,
) -> tuple[str, ...]:
    """Build the one canonical application command used by plan and write."""

    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("cherry-pick commit must be a full lowercase SHA")
    if mainline is not None and (
        isinstance(mainline, bool) or not isinstance(mainline, int) or mainline < 1
    ):
        raise ValueError("cherry-pick mainline must be a positive integer")
    args = ["cherry-pick", "-x" if commit_result else "--no-commit"]
    if mainline is not None:
        args.extend(["-m", str(mainline)])
    args.append(commit)
    return tuple(args)


def _run(
    repo: Path,
    *args: str,
    check: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one noninteractive Git command with hooks disabled."""

    environment = dict(os.environ)
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    environment.update(extra_env or {})
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        env=environment,
    )


def _resolve(repo: Path, revision: str) -> str | None:
    """Resolve a revision to one full commit SHA or return no result."""

    result = _run(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def _parents(repo: Path, commit: str) -> tuple[str, ...]:
    """Return the ordered parents recorded on one commit."""

    result = _run(repo, "rev-list", "--parents", "-n", "1", commit)
    fields = result.stdout.split()
    if result.returncode != 0 or not fields:
        raise ChangesetError(f"could not prove parents for {commit}")
    return tuple(fields[1:])


def _tree(repo: Path, revision: str) -> str:
    """Resolve the tree object associated with one commit."""

    result = _run(repo, "rev-parse", f"{revision}^{{tree}}")
    if result.returncode != 0:
        raise ChangesetError(f"could not prove tree for {revision}")
    return result.stdout.strip()


def _common_dir(repo: Path) -> Path:
    """Return the repository common directory shared by all worktrees."""

    result = _run(
        repo,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise WorktreeStateError(
            "worktree_identity_unavailable",
            "Git could not establish the replay worktree owner.",
            result.stderr.strip(),
        )
    return Path(result.stdout.strip()).resolve()


def _worktree_index(worktree: Path) -> Path:
    """Resolve the index file owned by a reusable worktree."""

    result = _run(
        worktree,
        "rev-parse",
        "--path-format=absolute",
        "--git-path",
        "index",
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise WorktreeStateError(
            "worktree_index_unavailable",
            "Git could not locate the reusable worktree index.",
            result.stderr.strip(),
        )
    return Path(result.stdout.strip())


def _index_snapshot(worktree: Path) -> Path:
    """Return the persistent rollback snapshot path for one worktree index."""

    return worktree.parent / ".index-snapshots" / f"{worktree.name}.index"


def _valid_index(path: Path) -> bool:
    """Prove that an index file is readable by Git."""

    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"DIRC"
    except OSError:
        return False


def _atomic_index_copy(source: Path, destination: Path) -> None:
    """Replace an index snapshot atomically without exposing partial bytes."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_or_rebuild_index(worktree: Path) -> None:
    """Restore the fast rollback index or rebuild it from the target tree."""

    index = _worktree_index(worktree)
    if _valid_index(index):
        return
    snapshot = _index_snapshot(worktree)
    if _valid_index(snapshot):
        _atomic_index_copy(snapshot, index)
        return

    head = _resolve(worktree, "HEAD")
    if head is None:
        raise WorktreeStateError(
            "worktree_index_repair_failed",
            "The corrupt replay index has no recoverable HEAD baseline.",
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".replay-index-rebuild.",
        dir=index.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        rebuild = _run(
            worktree,
            "read-tree",
            head,
            extra_env={"GIT_INDEX_FILE": str(temporary)},
        )
        if rebuild.returncode != 0 or not _valid_index(temporary):
            raise WorktreeStateError(
                "worktree_index_repair_failed",
                "Git could not rebuild the corrupt replay index from HEAD.",
                rebuild.stderr.strip(),
            )
        os.replace(temporary, index)
    finally:
        temporary.unlink(missing_ok=True)


def rollback_replay_worktree(
    repo: str | Path,
    worktree: str | Path,
    target_revision: str | None = None,
) -> str:
    """Reset one validated replay-owned worktree to a clean detached baseline."""

    repo_path = Path(repo)
    worktree_path = Path(worktree)
    if not worktree_path.exists() or _common_dir(worktree_path) != _common_dir(
        repo_path
    ):
        raise WorktreeStateError(
            "worktree_identity_mismatch",
            "The reusable path is not a worktree owned by the expected repository.",
        )
    target = (
        _resolve(repo_path, target_revision)
        if target_revision is not None
        else _resolve(worktree_path, "HEAD")
    )
    if target is None:
        raise WorktreeStateError(
            "worktree_target_missing",
            "The reusable worktree rollback target is unavailable.",
        )

    _restore_or_rebuild_index(worktree_path)
    _run(worktree_path, "cherry-pick", "--abort")
    _run(worktree_path, "cherry-pick", "--quit")
    reset = _run(worktree_path, "reset", "--hard", target)
    clean = _run(worktree_path, "clean", "-ffd")
    head = _resolve(worktree_path, "HEAD")
    status = _run(worktree_path, "status", "--porcelain")
    tree = _run(worktree_path, "write-tree")
    expected_tree = _tree(repo_path, target)
    if (
        reset.returncode != 0
        or clean.returncode != 0
        or head != target
        or status.returncode != 0
        or status.stdout.strip()
        or tree.returncode != 0
        or tree.stdout.strip() != expected_tree
    ):
        stderr = "\n".join(
            value
            for value in (
                reset.stderr.strip(),
                clean.stderr.strip(),
                status.stderr.strip(),
            )
            if value
        )
        raise WorktreeStateError(
            "worktree_rollback_failed",
            "The reusable worktree could not be proven clean after rollback.",
            stderr,
        )
    _atomic_index_copy(_worktree_index(worktree_path), _index_snapshot(worktree_path))
    return target


def _prepare_replay_worktree(repo: Path, worktree: Path, target: str) -> None:
    """Reset a replay-owned worktree to its exact clean target state."""

    worktree.parent.mkdir(parents=True, exist_ok=True)
    if not worktree.exists():
        add = _run(repo, "worktree", "add", "--detach", str(worktree), target)
        if add.returncode != 0:
            raise WorktreeStateError(
                "worktree_creation_failed",
                "Git could not create the reusable destination worktree.",
                add.stderr.strip(),
            )
    rollback_replay_worktree(repo, worktree, target)


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool | None:
    """Return Git ancestry truth, preserving an indeterminate command failure."""

    result = _run(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _conflict_evidence(worktree: Path) -> dict[str, object]:
    """Collect stable conflict paths without modifying the trial worktree."""

    result = _run(worktree, "ls-files", "-u", "-z")
    stages: dict[str, set[int]] = {}
    if result.returncode == 0:
        for record in result.stdout.split("\0"):
            metadata, separator, path = record.partition("\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                continue
            try:
                stage = int(fields[2])
            except ValueError:
                continue
            stages.setdefault(path, set()).add(stage)
    return {
        "conflicted": True,
        "conflict_paths": sorted(stages),
        "conflict_stages": {
            path: sorted(values) for path, values in sorted(stages.items())
        },
    }


def conflict_evidence(worktree: str | Path) -> dict[str, object]:
    """Return sorted path/stage evidence from an in-progress Git conflict."""

    return _conflict_evidence(Path(worktree))


def _identity_candidates(
    repo: Path,
    target: str,
    identity: SourceIdentity | CommitIdentity,
) -> tuple[str, ...]:
    """Find destination commits carrying exact cherry-pick provenance."""

    if isinstance(identity, SourceIdentity):
        patterns = (
            identity.merge_commit,
            f"https://github.com/{identity.repository}/pull/{identity.pull_number}",
            f"cherry-pick #{identity.pull_number}",
            f"cherry pick #{identity.pull_number}",
        )
    else:
        patterns = (
            f"(cherry picked from commit {identity.commit_sha})",
            identity.commit_sha,
        )
    candidates: list[str] = []
    for pattern in patterns:
        result = _run(
            repo,
            "log",
            "--first-parent",
            "--format=%H",
            "--fixed-strings",
            "--regexp-ignore-case",
            f"--grep={pattern}",
            target,
        )
        if result.returncode != 0:
            continue
        candidates.extend(result.stdout.splitlines())
    return tuple(dict.fromkeys(item for item in candidates if item))


def _explicit_revert_after(
    repo: Path,
    application: str,
    target: str,
) -> str | None:
    """Detect an explicit later revert of a proven destination application."""

    if application == target:
        return None
    ancestry = _is_ancestor(repo, application, target)
    if ancestry is not True:
        return None
    result = _run(
        repo,
        "log",
        "--first-parent",
        "--format=%H",
        "--fixed-strings",
        f"--grep=This reverts commit {application}",
        f"{application}..{target}",
    )
    return (
        result.stdout.splitlines()[0]
        if result.returncode == 0 and result.stdout
        else None
    )


def _proven_destination_application(
    repo: Path,
    changeset: Changeset,
    target: str,
    identity: SourceIdentity | CommitIdentity,
    worktree_path: str | Path | None,
    scratch_root: str | Path | None = None,
) -> Result | None:
    """Prove an attributed prior application or its explicit later revert."""

    for candidate in _identity_candidates(repo, target, identity):
        try:
            parents = _parents(repo, candidate)
            candidate_tree = _tree(repo, candidate)
        except ChangesetError:
            continue
        if len(parents) != 1:
            continue
        trial = evaluate_changeset(
            repo,
            changeset,
            parents[0],
            worktree_path=worktree_path,
            scratch_root=scratch_root,
        )
        if (
            trial.status is not Status.DRAFT_PLANNED
            or trial.evidence.get("planned_tree") != candidate_tree
        ):
            continue
        evidence = {
            "application_commit": candidate,
            "application_parent": parents[0],
            "application_tree": candidate_tree,
            "application_proof": "source_identity_and_exact_tree",
        }
        revert = _explicit_revert_after(repo, candidate, target)
        if revert is not None:
            return _git_result(
                Status.BLOCKED_AMBIGUOUS_CHANGESET,
                "proven_application_later_reverted",
                "The proven destination application was later explicitly reverted.",
                changeset=changeset,
                target=target,
                extra={**evidence, "revert_commit": revert},
            )
        return _git_result(
            Status.ALREADY_CONTAINED,
            "complete_changeset_application_ancestor",
            "A reachable destination commit exactly applies the complete changeset.",
            changeset=changeset,
            target=target,
            extra=evidence,
        )
    return None


def _patch_id(repo: Path, patch: str) -> str | None:
    """Compute the stable patch identity of supplied diff bytes."""

    environment = dict(os.environ)
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=repo,
        check=False,
        text=True,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if result.returncode != 0:
        raise ChangesetError("could not prove normalized patch identity")
    fields = result.stdout.split()
    return fields[0] if fields else None


def _diff_patch_id(repo: Path, base: str, head: str) -> str | None:
    """Compute one aggregate patch identity between two revisions."""

    result = _run(repo, "diff", "--binary", "--full-index", base, head)
    if result.returncode != 0:
        raise ChangesetError("could not prove aggregate tree delta")
    return _patch_id(repo, result.stdout)


def _commit_patch_id(repo: Path, commit: str) -> str | None:
    """Compute the stable patch identity introduced by one commit."""

    parents = _parents(repo, commit)
    if len(parents) != 1:
        raise ChangesetError("could not prove a non-linear rebase commit")
    return _diff_patch_id(repo, parents[0], commit)


def _merge_base(repo: Path, first: str, second: str) -> str:
    """Resolve the unique merge base required by source-range proof."""

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


def prove_commit_changeset(repo: str | Path, revision: str) -> Changeset:
    """Prove one immutable standalone commit as a linear changeset."""

    repo_path = Path(repo)
    commit = _resolve(repo_path, revision)
    if commit is None:
        raise ChangesetError("could not prove standalone commit object")
    parents = _parents(repo_path, commit)
    if len(parents) != 1:
        raise ChangesetError("standalone prerequisite must be a single-parent commit")
    parent = parents[0]
    return Changeset(
        kind=ChangesetKind.SINGLE,
        commits=(commit,),
        aggregate_base=parent,
        aggregate_head=commit,
        mainline=None,
        proof=ChangesetProof(
            method="standalone_commit_single_parent",
            source_head=commit,
            source_merge_commit=commit,
            original_commits=(commit,),
            aggregate_patch_id=_diff_patch_id(repo_path, parent, commit),
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
    """Build a result with the common immutable Git evidence envelope."""

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


def _resolve_evaluation(
    repo_path: Path,
    changeset: Changeset,
    target_revision: str,
) -> _ResolvedEvaluation | Result:
    """Resolve all immutable objects before any disposable worktree is created."""

    target = _resolve(repo_path, target_revision)
    if target is None:
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "target_ref_missing",
            "The destination ref is unavailable.",
            changeset=changeset,
        )
    destination_tree = _tree(repo_path, target)
    candidates = tuple(_resolve(repo_path, item) for item in changeset.commits)
    if any(item is None for item in candidates):
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "source_commit_missing",
            "One or more proven changeset commits are unavailable.",
            changeset=changeset,
            target=target,
        )
    return _ResolvedEvaluation(
        target=target,
        destination_tree=destination_tree,
        commits=tuple(str(item) for item in candidates),
    )


def _identity_or_ancestry_result(
    repo_path: Path,
    changeset: Changeset,
    resolved: _ResolvedEvaluation,
    source_identity: SourceIdentity | CommitIdentity | None,
    worktree_path: str | Path | None,
    scratch_root: str | Path | None,
) -> Result | None:
    """Return a terminal identity or ancestry decision before trial application."""

    if source_identity is not None:
        identity_commit = (
            source_identity.merge_commit
            if isinstance(source_identity, SourceIdentity)
            else source_identity.commit_sha
        )
        if identity_commit != changeset.proof.source_merge_commit:
            return _git_result(
                Status.BLOCKED_EVIDENCE,
                "source_identity_mismatch",
                "Source identity does not match the proven changeset.",
                changeset=changeset,
                target=resolved.target,
            )
        prior_application = _proven_destination_application(
            repo_path,
            changeset,
            resolved.target,
            source_identity,
            worktree_path,
            scratch_root,
        )
        if prior_application is not None:
            return prior_application

    ancestry = tuple(
        _is_ancestor(repo_path, commit, resolved.target) for commit in resolved.commits
    )
    if any(item is None for item in ancestry):
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "ancestry_check_failed",
            "Git could not establish changeset-to-destination ancestry.",
            changeset=changeset,
            target=resolved.target,
        )
    if all(ancestry):
        return _git_result(
            Status.ALREADY_CONTAINED,
            "complete_changeset_ancestor",
            "Every changeset application unit is reachable from the destination.",
            changeset=changeset,
            target=resolved.target,
        )
    if any(ancestry):
        return _git_result(
            Status.BLOCKED_AMBIGUOUS_CHANGESET,
            "partial_changeset_containment",
            "Only part of the complete changeset is reachable from the destination.",
            changeset=changeset,
            target=resolved.target,
        )
    return None


def _prepare_trial_worktree(
    stack: ExitStack,
    repo_path: Path,
    target: str,
    changeset: Changeset,
    worktree_path: str | Path | None,
    scratch_root: str | Path | None,
) -> Path | Result:
    """Create or reset the isolated worktree owned by one evaluation transaction."""

    if worktree_path is not None:
        worktree = Path(worktree_path)
        _prepare_replay_worktree(repo_path, worktree, target)
        stack.callback(rollback_replay_worktree, repo_path, worktree, target)
        return worktree

    temporary_parent = repo_path.parent
    if scratch_root is not None:
        temporary_parent = Path(scratch_root)
        temporary_parent.mkdir(parents=True, exist_ok=True)
    temp_root = stack.enter_context(
        tempfile.TemporaryDirectory(
            prefix="cherry-pick-plan-",
            dir=temporary_parent,
        )
    )
    worktree = Path(temp_root) / "worktree"
    add = _run(
        repo_path,
        "worktree",
        "add",
        "--detach",
        str(worktree),
        target,
    )
    if add.returncode != 0:
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "worktree_creation_failed",
            "Git could not create a disposable destination worktree.",
            changeset=changeset,
            target=target,
            extra={"stderr": add.stderr.strip()},
        )
    stack.callback(
        _run,
        repo_path,
        "worktree",
        "remove",
        "--force",
        str(worktree),
    )
    return worktree


def _apply_trial(worktree: Path, changeset: Changeset) -> _TrialOutcome:
    """Apply every ordered unit and retain evidence for deterministic classification."""

    failure: subprocess.CompletedProcess[str] | None = None
    empty_units = 0
    applied_units = 0
    for commit in changeset.commits:
        before_tree = _run(worktree, "write-tree").stdout.strip()
        command = cherry_pick_command(commit, changeset.mainline, commit_result=False)
        trial = _run(worktree, *command)
        current_unmerged = _run(worktree, "ls-files", "-u").stdout.strip()
        if current_unmerged:
            failure = trial
            break
        after_tree = _run(worktree, "write-tree").stdout.strip()
        if before_tree == after_tree:
            empty_units += 1
            # Clear only sequencer metadata; previously staged units must survive.
            _run(worktree, "cherry-pick", "--quit")
            continue
        applied_units += 1
        if trial.returncode != 0:
            failure = trial
            break
    return _TrialOutcome(
        failure=failure,
        empty_units=empty_units,
        applied_units=applied_units,
        unmerged=_run(worktree, "ls-files", "-u").stdout.strip(),
        status=_run(worktree, "status", "--porcelain").stdout.strip(),
    )


def _classify_trial(
    worktree: Path,
    changeset: Changeset,
    resolved: _ResolvedEvaluation,
    outcome: _TrialOutcome,
) -> Result:
    """Classify a completed trial in the established fail-closed precedence order."""

    if outcome.unmerged:
        return _git_result(
            Status.BLOCKED_CONFLICT,
            "cherry_pick_conflict",
            "The complete proven changeset conflicts with the destination.",
            changeset=changeset,
            target=resolved.target,
            extra=_conflict_evidence(worktree),
        )
    if outcome.empty_units == len(changeset.commits):
        return _git_result(
            Status.ALREADY_CONTAINED,
            "complete_changeset_already_applied",
            "Applying the complete proven changeset produces no tree change.",
            changeset=changeset,
            target=resolved.target,
            extra={"patch_equivalent": True},
        )
    if outcome.empty_units and outcome.applied_units:
        return _git_result(
            Status.BLOCKED_AMBIGUOUS_CHANGESET,
            "partial_changeset_containment",
            "Only part of the complete changeset is patch-equivalent to the destination.",
            changeset=changeset,
            target=resolved.target,
            extra={
                "empty_application_units": outcome.empty_units,
                "applied_application_units": outcome.applied_units,
            },
        )
    if outcome.failure is not None:
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "trial_application_failed",
            "The trial failed without a classifiable conflict.",
            changeset=changeset,
            target=resolved.target,
            extra={"stderr": outcome.failure.stderr.strip()},
        )
    if not outcome.status:
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "trial_tree_state_unexpected",
            "The trial completed without a classifiable tree state.",
            changeset=changeset,
            target=resolved.target,
        )
    planned_tree = _run(worktree, "write-tree")
    if planned_tree.returncode != 0 or not planned_tree.stdout.strip():
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            "planned_tree_unavailable",
            "Git could not record the planned destination tree.",
            changeset=changeset,
            target=resolved.target,
            extra={"destination_tree": resolved.destination_tree},
        )
    return _git_result(
        Status.DRAFT_PLANNED,
        "clean_trial_application",
        "The complete proven changeset applies cleanly and is non-empty.",
        changeset=changeset,
        target=resolved.target,
        extra={
            "destination_tree": resolved.destination_tree,
            "planned_tree": planned_tree.stdout.strip(),
            "patch_equivalent": False,
        },
    )


def evaluate_changeset(
    repo: str | Path,
    changeset: Changeset,
    target_revision: str,
    *,
    worktree_path: str | Path | None = None,
    scratch_root: str | Path | None = None,
    source_identity: SourceIdentity | CommitIdentity | None = None,
) -> Result:
    """Apply a proven changeset in a disposable or reusable worktree."""

    repo_path = Path(repo)
    resolved = _resolve_evaluation(repo_path, changeset, target_revision)
    if isinstance(resolved, Result):
        return resolved
    early = _identity_or_ancestry_result(
        repo_path,
        changeset,
        resolved,
        source_identity,
        worktree_path,
        scratch_root,
    )
    if early is not None:
        return early
    try:
        with ExitStack() as stack:
            prepared = _prepare_trial_worktree(
                stack,
                repo_path,
                resolved.target,
                changeset,
                worktree_path,
                scratch_root,
            )
            if isinstance(prepared, Result):
                return prepared
            outcome = _apply_trial(prepared, changeset)
            return _classify_trial(prepared, changeset, resolved, outcome)
    except WorktreeStateError as exc:
        return _git_result(
            Status.BLOCKED_EVIDENCE,
            exc.reason_code,
            str(exc),
            changeset=changeset,
            target=resolved.target,
            extra={"stderr": exc.stderr},
        )


def evaluate_existing_pull_coverage(
    repo: str | Path,
    changeset: Changeset,
    destination_revision: str,
    candidate_revision: str,
    *,
    source_identity: SourceIdentity,
    planned_tree: str,
    scratch_root: str | Path | None = None,
) -> Result | None:
    """Prove whether one open PR exactly covers a planned source application.

    ``None`` means the PR is unrelated. Exact-tree equivalence alone is not
    trusted: a covering PR must also carry source-attributed Git provenance.
    """

    repo_path = Path(repo)
    destination = _resolve(repo_path, destination_revision)
    candidate = _resolve(repo_path, candidate_revision)
    if destination is None or candidate is None:
        return Result(
            status=Status.BLOCKED_EVIDENCE,
            reason_code="existing_pull_git_object_missing",
            message="An open pull request Git object is unavailable.",
            evidence={
                "destination_head": destination,
                "candidate_head": candidate,
            },
        )
    ancestry = _is_ancestor(repo_path, destination, candidate)
    if ancestry is False:
        return None
    if ancestry is None:
        return Result(
            status=Status.BLOCKED_EVIDENCE,
            reason_code="existing_pull_ancestry_unavailable",
            message="Git could not prove open pull request destination ancestry.",
            evidence={
                "destination_head": destination,
                "candidate_head": candidate,
            },
        )
    candidate_tree = _tree(repo_path, candidate)
    evaluated = evaluate_changeset(
        repo_path,
        changeset,
        candidate,
        source_identity=source_identity,
        scratch_root=scratch_root,
    )
    attributed = (
        evaluated.status is Status.ALREADY_CONTAINED
        and evaluated.reason_code
        in {
            "complete_changeset_ancestor",
            "complete_changeset_application_ancestor",
        }
    )
    evidence = {
        "destination_head": destination,
        "candidate_head": candidate,
        "candidate_tree": candidate_tree,
        "planned_tree": planned_tree,
        "source_attribution": attributed,
        "source_evaluation_status": evaluated.status.value,
        "source_evaluation_reason": evaluated.reason_code,
    }
    if attributed and candidate_tree == planned_tree:
        return Result(
            status=Status.COVERED_BY_EXISTING_PR,
            reason_code="exact_existing_pull_coverage",
            message="An open pull request has exact source provenance and planned tree.",
            evidence=evidence,
        )
    if attributed:
        return Result(
            status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
            reason_code="existing_pull_attributed_tree_mismatch",
            message="An open pull request contains the source but has a different final tree.",
            evidence=evidence,
        )
    if candidate_tree == planned_tree:
        return Result(
            status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
            reason_code="existing_pull_exact_tree_without_attribution",
            message="An open pull request has the planned tree without provable source attribution.",
            evidence=evidence,
        )
    if evaluated.status in {
        Status.BLOCKED_EVIDENCE,
        Status.BLOCKED_AMBIGUOUS_CHANGESET,
    }:
        return Result(
            status=evaluated.status,
            reason_code=evaluated.reason_code,
            message=evaluated.message,
            evidence=evidence,
        )
    return None


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
