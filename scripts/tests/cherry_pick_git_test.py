# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.cherry_pick import git as git_module
from scripts.cherry_pick.models import Status


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def commit_file(repo, path, content, message):
    (repo / path).write_text(content)
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def prove(repo, merged_sha, pr_head, pr_commits):
    function = getattr(git_module, "prove_changeset", None)
    assert function is not None, "complete changeset proof must be implemented"
    return function(repo, merged_sha, pr_head, tuple(pr_commits))


def evaluate(repo, changeset, target):
    function = getattr(git_module, "evaluate_changeset", None)
    assert function is not None, "changeset preflight must be implemented"
    return function(repo, changeset, target)


def source_identity(repository, pull_number, merge_commit):
    identity_type = getattr(git_module, "SourceIdentity", None)
    assert identity_type is not None, "containment requires typed source identity"
    return identity_type(
        repository=repository,
        pull_number=pull_number,
        merge_commit=merge_commit,
    )


def commit_identity(repository, commit_sha):
    identity_type = getattr(git_module, "CommitIdentity", None)
    assert identity_type is not None, "standalone commits require typed source identity"
    return identity_type(repository=repository, commit_sha=commit_sha)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Cherry-pick Test")
    git(path, "config", "user.email", "cherry-pick@example.com")
    commit_file(path, "value.txt", "base\n", "base")
    return path


def topic_with_two_commits(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    first = commit_file(repo, "first.txt", "first\n", "first")
    second = commit_file(repo, "second.txt", "second\n", "second")
    return base, first, second


def test_proves_and_applies_complete_squash_changeset(repo):
    base, first, second = topic_with_two_commits(repo)
    pr_head = second
    git(repo, "checkout", "main")
    destination = commit_file(repo, "main.txt", "main\n", "main advanced")
    git(repo, "merge", "--squash", "topic")
    git(repo, "commit", "-m", "squash source")
    merged = git(repo, "rev-parse", "HEAD")

    changeset = prove(repo, merged, pr_head, (first, second))
    assert changeset.kind.value == "squash"
    assert changeset.commits == (merged,)
    assert changeset.mainline is None
    result = evaluate(repo, changeset, destination)
    assert result.status is Status.DRAFT_PLANNED
    assert result.evidence["changeset_kind"] == "squash"
    assert result.evidence["destination_tree"] == git(
        repo, "rev-parse", f"{destination}^{{tree}}"
    )
    assert result.evidence["planned_tree"] == git(
        repo, "rev-parse", f"{merged}^{{tree}}"
    )
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == merged


def test_git_subprocesses_disable_lazy_fetch_and_terminal_prompts(repo, monkeypatch):
    original = subprocess.run
    environments = []

    def capture(*args, **kwargs):
        environments.append(kwargs.get("env", {}))
        return original(*args, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "run", capture)
    assert git_module._resolve(repo, "HEAD")
    assert environments
    assert environments[-1]["GIT_NO_LAZY_FETCH"] == "1"
    assert environments[-1]["GIT_TERMINAL_PROMPT"] == "0"


def test_disposable_worktree_uses_repository_filesystem(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))

    temporary_directories = []
    original_temporary_directory = git_module.tempfile.TemporaryDirectory

    def capture_temporary_directory(*args, **kwargs):
        temporary_directories.append(kwargs)
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        git_module.tempfile,
        "TemporaryDirectory",
        capture_temporary_directory,
    )

    result = evaluate(repo, changeset, base)

    assert result.status is Status.DRAFT_PLANNED
    assert temporary_directories == [
        {"prefix": "cherry-pick-plan-", "dir": repo.parent}
    ]


def test_disposable_worktree_honors_explicit_disk_scratch_root(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))
    scratch_root = repo.parent / "disk-scratch"

    temporary_directories = []
    original_temporary_directory = git_module.tempfile.TemporaryDirectory

    def capture_temporary_directory(*args, **kwargs):
        temporary_directories.append(kwargs)
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        git_module.tempfile,
        "TemporaryDirectory",
        capture_temporary_directory,
    )

    result = git_module.evaluate_changeset(
        repo,
        changeset,
        base,
        scratch_root=scratch_root,
    )

    assert result.status is Status.DRAFT_PLANNED
    assert scratch_root.is_dir()
    assert temporary_directories == [
        {"prefix": "cherry-pick-plan-", "dir": scratch_root}
    ]


def test_reusable_worktree_rolls_back_without_recreating_index(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))
    worktree = repo.parent / "replay-cache" / "worker"
    worktree_adds = 0
    original_run = git_module._run

    def capture_run(run_repo, *args, **kwargs):
        nonlocal worktree_adds
        if args[:2] == ("worktree", "add"):
            worktree_adds += 1
        return original_run(run_repo, *args, **kwargs)

    monkeypatch.setattr(git_module, "_run", capture_run)

    first = git_module.evaluate_changeset(
        repo,
        changeset,
        base,
        worktree_path=worktree,
    )
    (worktree / "contamination.txt").write_text("must be removed\n")
    second = git_module.evaluate_changeset(
        repo,
        changeset,
        base,
        worktree_path=worktree,
    )

    assert first.status is Status.DRAFT_PLANNED
    assert second.status is Status.DRAFT_PLANNED
    assert worktree_adds == 1
    assert worktree.exists()
    assert not (worktree / "contamination.txt").exists()
    assert git(worktree, "rev-parse", "HEAD") == base
    assert git(worktree, "status", "--porcelain") == ""
    assert git(worktree, "rev-parse", "--verify", "CHERRY_PICK_HEAD", check=False) == ""


def test_reusable_worktree_restores_corrupt_index_from_atomic_snapshot(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))
    worktree = repo.parent / "corrupt-index-cache" / "worker"

    first = git_module.evaluate_changeset(
        repo,
        changeset,
        base,
        worktree_path=worktree,
    )
    index = Path(
        git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index")
    )
    snapshot = worktree.parent / ".index-snapshots" / "worker.index"
    original_size = index.stat().st_size
    index.write_bytes(b"\0" * original_size)

    second = git_module.evaluate_changeset(
        repo,
        changeset,
        base,
        worktree_path=worktree,
    )

    assert first.status is Status.DRAFT_PLANNED
    assert second.status is Status.DRAFT_PLANNED
    assert snapshot.read_bytes()[:4] == b"DIRC"
    assert index.read_bytes()[:4] == b"DIRC"
    assert git(worktree, "rev-parse", "HEAD") == base
    assert git(worktree, "status", "--porcelain") == ""


def test_proves_two_parent_merge_relative_to_parent_one(repo):
    _base, first, second = topic_with_two_commits(repo)
    pr_head = second
    git(repo, "checkout", "main")
    destination = commit_file(repo, "main.txt", "main\n", "main advanced")
    git(repo, "merge", "--no-ff", "topic", "-m", "merge source")
    merged = git(repo, "rev-parse", "HEAD")

    changeset = prove(repo, merged, pr_head, (first, second))
    assert changeset.kind.value == "merge_commit"
    assert changeset.commits == (merged,)
    assert changeset.mainline == 1
    assert evaluate(repo, changeset, destination).status is Status.DRAFT_PLANNED


def test_proves_complete_rebase_range_in_application_order(repo):
    _base, first, second = topic_with_two_commits(repo)
    original_head = second
    git(repo, "checkout", "main")
    destination = commit_file(repo, "main.txt", "main\n", "main advanced")
    git(repo, "checkout", "topic")
    git(repo, "rebase", "main")
    merged = git(repo, "rev-parse", "HEAD")
    rebased_commits = tuple(
        reversed(git(repo, "rev-list", "--max-count=2", merged).splitlines())
    )

    changeset = prove(repo, merged, original_head, (first, second))
    assert changeset.kind.value == "rebase_range"
    assert changeset.commits == rebased_commits
    assert changeset.mainline is None
    assert evaluate(repo, changeset, destination).status is Status.DRAFT_PLANNED


def test_single_commit_merge_is_proven_without_guessing(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")
    destination = commit_file(repo, "main.txt", "main\n", "advance")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")

    changeset = prove(repo, merged, original, (original,))
    assert changeset.kind.value == "single"
    assert changeset.commits == (merged,)
    assert changeset.aggregate_base != base
    assert evaluate(repo, changeset, destination).status is Status.DRAFT_PLANNED


def test_proves_standalone_single_parent_commit_without_pr_metadata(repo):
    parent = git(repo, "rev-parse", "HEAD")
    commit = commit_file(repo, "standalone.txt", "change\n", "standalone")

    changeset = git_module.prove_commit_changeset(repo, commit)

    assert changeset.kind is git_module.ChangesetKind.SINGLE
    assert changeset.commits == (commit,)
    assert changeset.aggregate_base == parent
    assert changeset.aggregate_head == commit
    assert changeset.proof.method == "standalone_commit_single_parent"
    result = git_module.evaluate_changeset(
        repo,
        changeset,
        parent,
        source_identity=commit_identity("ROCm/rocm-systems", commit),
    )
    assert result.status is Status.DRAFT_PLANNED


def test_standalone_commit_proof_rejects_root_and_merge_commits(repo):
    root = git(repo, "rev-list", "--max-parents=0", "HEAD")
    with pytest.raises(git_module.ChangesetError, match="single-parent"):
        git_module.prove_commit_changeset(repo, root)

    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "standalone-topic")
    commit_file(repo, "topic.txt", "topic\n", "topic")
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "main\n", "main")
    git(repo, "merge", "--no-ff", "standalone-topic", "-m", "merge")
    merged = git(repo, "rev-parse", "HEAD")
    assert base != merged
    with pytest.raises(git_module.ChangesetError, match="single-parent"):
        git_module.prove_commit_changeset(repo, merged)


def test_ambiguous_or_incomplete_rebase_range_fails_closed(repo):
    _base, first, second = topic_with_two_commits(repo)
    original_head = second
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "main\n", "advance")
    git(repo, "checkout", "topic")
    git(repo, "rebase", "main")
    commit_file(repo, "unrelated.txt", "unexpected\n", "unrelated")
    merged = git(repo, "rev-parse", "HEAD")

    error_type = getattr(git_module, "ChangesetError", None)
    assert error_type is not None
    with pytest.raises(error_type, match="prove"):
        prove(repo, merged, original_head, (first, second))


def test_complete_containment_is_positive_but_partial_range_is_ambiguous(repo):
    _base, first, second = topic_with_two_commits(repo)
    original_head = second
    git(repo, "checkout", "main")
    destination = commit_file(repo, "main.txt", "main\n", "advance")
    git(repo, "checkout", "topic")
    git(repo, "rebase", "main")
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original_head, (first, second))

    assert evaluate(repo, changeset, merged).status is Status.ALREADY_CONTAINED
    partial = changeset.commits[0]
    result = evaluate(repo, changeset, partial)
    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert result.reason_code == "partial_changeset_containment"


def test_patch_equivalent_subset_of_rebase_range_is_not_full_containment(repo):
    _base, first, second = topic_with_two_commits(repo)
    original_head = second
    git(repo, "checkout", "main")
    destination = commit_file(repo, "main.txt", "main\n", "advance")
    git(repo, "checkout", "topic")
    git(repo, "rebase", "main")
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original_head, (first, second))

    git(repo, "checkout", "--detach", destination)
    git(repo, "cherry-pick", first)
    git(repo, "commit", "--amend", "-m", "equivalent first")
    partial_target = git(repo, "rev-parse", "HEAD")

    result = evaluate(repo, changeset, partial_target)
    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert result.reason_code == "partial_changeset_containment"


def test_patch_equivalent_full_changeset_is_contained(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "same\n", "source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))
    git(repo, "checkout", "--detach", base)
    equivalent = commit_file(repo, "source.txt", "same\n", "independent")

    result = evaluate(repo, changeset, equivalent)
    assert result.status is Status.ALREADY_CONTAINED
    assert result.reason_code == "complete_changeset_already_applied"


def test_conflict_is_never_classified_as_containment(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "value.txt", "source\n", "source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))
    git(repo, "checkout", "--detach", base)
    target = commit_file(repo, "value.txt", "target\n", "target")
    worktree = repo.parent / "conflict-replay-cache"

    result = git_module.evaluate_changeset(
        repo,
        changeset,
        target,
        worktree_path=worktree,
    )
    assert result.status is Status.BLOCKED_CONFLICT
    assert result.reason_code == "cherry_pick_conflict"
    assert git(worktree, "rev-parse", "HEAD") == target
    assert git(worktree, "status", "--porcelain") == ""
    assert git(worktree, "rev-parse", "--verify", "CHERRY_PICK_HEAD", check=False) == ""


def historical_application_fixture(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "value.txt", "source\n", "source change")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    git(repo, "commit", "--amend", "-m", "source change (#100)")
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))

    git(repo, "checkout", "--detach", base)
    git(repo, "cherry-pick", merged)
    git(
        repo,
        "commit",
        "--amend",
        "-m",
        "Cherry-pick #100 (#200)",
        "-m",
        f"Cherry-picks commit {merged}.",
    )
    applied = git(repo, "rev-parse", "HEAD")
    descendant = commit_file(
        repo,
        "value.txt",
        "source with downstream evolution\n",
        "downstream evolution",
    )
    return changeset, merged, applied, descendant


def test_reachable_exact_historical_application_proves_containment(repo):
    changeset, merged, applied, descendant = historical_application_fixture(repo)

    result = git_module.evaluate_changeset(
        repo,
        changeset,
        descendant,
        source_identity=source_identity("ROCm/TheRock", 100, merged),
    )

    assert result.status is Status.ALREADY_CONTAINED
    assert result.reason_code == "complete_changeset_application_ancestor"
    assert result.evidence["application_commit"] == applied
    assert result.evidence["application_tree"] == git(
        repo, "rev-parse", f"{applied}^{{tree}}"
    )


def existing_pull_coverage_fixture(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "coverage-source")
    original = commit_file(repo, "coverage.txt", "source\n", "coverage source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))
    planned = evaluate(repo, changeset, base)
    assert planned.status is Status.DRAFT_PLANNED
    return base, merged, changeset, str(planned.evidence["planned_tree"])


def test_existing_pull_coverage_requires_destination_ancestry_source_attribution_and_tree(
    repo,
):
    base, merged, changeset, planned_tree = existing_pull_coverage_fixture(repo)
    identity = source_identity("ROCm/rocm-systems", 9716, merged)
    git(repo, "checkout", "--detach", base)
    git(repo, "cherry-pick", "-x", merged)
    candidate = git(repo, "rev-parse", "HEAD")

    result = git_module.evaluate_existing_pull_coverage(
        repo,
        changeset,
        base,
        candidate,
        source_identity=identity,
        planned_tree=planned_tree,
    )

    assert result is not None
    assert result.status is Status.COVERED_BY_EXISTING_PR
    assert result.reason_code == "exact_existing_pull_coverage"
    assert result.evidence["candidate_tree"] == planned_tree
    assert result.evidence["source_attribution"] is True


def test_existing_pull_exact_tree_without_source_attribution_is_ambiguous(repo):
    base, merged, changeset, planned_tree = existing_pull_coverage_fixture(repo)
    identity = source_identity("ROCm/rocm-systems", 9716, merged)
    git(repo, "checkout", "--detach", base)
    (repo / "coverage.txt").write_text("source\n")
    git(repo, "add", "coverage.txt")
    git(repo, "commit", "-m", "manual equivalent without source identity")
    candidate = git(repo, "rev-parse", "HEAD")

    result = git_module.evaluate_existing_pull_coverage(
        repo,
        changeset,
        base,
        candidate,
        source_identity=identity,
        planned_tree=planned_tree,
    )

    assert result is not None
    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert result.reason_code == "existing_pull_exact_tree_without_attribution"


def test_existing_pull_attributed_but_with_extra_delta_is_ambiguous(repo):
    base, merged, changeset, planned_tree = existing_pull_coverage_fixture(repo)
    identity = source_identity("ROCm/rocm-systems", 9716, merged)
    git(repo, "checkout", "--detach", base)
    git(repo, "cherry-pick", "-x", merged)
    candidate = commit_file(repo, "extra.txt", "extra\n", "unreviewed extra delta")

    result = git_module.evaluate_existing_pull_coverage(
        repo,
        changeset,
        base,
        candidate,
        source_identity=identity,
        planned_tree=planned_tree,
    )

    assert result is not None
    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert result.reason_code == "existing_pull_attributed_tree_mismatch"


def test_unrelated_or_non_descendant_open_pull_is_not_coverage(repo):
    base, merged, changeset, planned_tree = existing_pull_coverage_fixture(repo)
    identity = source_identity("ROCm/rocm-systems", 9716, merged)
    git(repo, "checkout", "--detach", base)
    unrelated = commit_file(repo, "unrelated.txt", "other\n", "other")
    assert (
        git_module.evaluate_existing_pull_coverage(
            repo,
            changeset,
            base,
            unrelated,
            source_identity=identity,
            planned_tree=planned_tree,
        )
        is None
    )

    git(repo, "checkout", "--orphan", "unrelated-history")
    git(repo, "rm", "-rf", ".")
    non_descendant = commit_file(repo, "orphan.txt", "orphan\n", "orphan")
    assert (
        git_module.evaluate_existing_pull_coverage(
            repo,
            changeset,
            base,
            non_descendant,
            source_identity=identity,
            planned_tree=planned_tree,
        )
        is None
    )


def test_source_text_without_exact_application_is_not_containment(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "value.txt", "source\n", "source change")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", original)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, original, (original,))

    git(repo, "checkout", "--detach", base)
    claim = commit_file(
        repo,
        "other.txt",
        "unrelated\n",
        f"Claims Cherry-pick #100 from {merged}",
    )
    target = commit_file(repo, "value.txt", "target\n", "conflicting target")

    result = git_module.evaluate_changeset(
        repo,
        changeset,
        target,
        source_identity=source_identity("ROCm/TheRock", 100, merged),
    )

    assert result.status is Status.BLOCKED_CONFLICT
    assert result.reason_code == "cherry_pick_conflict"
    assert "application_commit" not in result.evidence
    assert claim != target


def test_explicit_revert_of_proven_application_blocks_for_review(repo):
    changeset, merged, applied, _descendant = historical_application_fixture(repo)
    git(repo, "checkout", "--detach", applied)
    git(repo, "revert", "--no-edit", applied)
    reverted = git(repo, "rev-parse", "HEAD")

    result = git_module.evaluate_changeset(
        repo,
        changeset,
        reverted,
        source_identity=source_identity("ROCm/TheRock", 100, merged),
    )

    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert result.reason_code == "proven_application_later_reverted"
    assert result.evidence["application_commit"] == applied
    assert result.evidence["revert_commit"] == reverted


def test_conflict_evidence_names_paths_and_index_stages(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    source = commit_file(repo, "value.txt", "source\n", "source conflict")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", source)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, source, (source,))
    git(repo, "checkout", "--detach", base)
    target = commit_file(repo, "value.txt", "target\n", "target conflict")

    result = evaluate(repo, changeset, target)

    assert result.status is Status.BLOCKED_CONFLICT
    assert result.evidence["conflict_paths"] == ["value.txt"]
    assert result.evidence["conflict_stages"] == {"value.txt": [1, 2, 3]}


@pytest.mark.parametrize("shape", ["delete", "rename", "mode", "symlink", "binary"])
def test_complete_changeset_preserves_git_file_shapes(repo, shape):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    if shape == "delete":
        git(repo, "rm", "value.txt")
    elif shape == "rename":
        git(repo, "mv", "value.txt", "renamed.txt")
    elif shape == "mode":
        (repo / "value.txt").chmod(0o755)
        git(repo, "add", "value.txt")
    elif shape == "symlink":
        (repo / "value.txt").unlink()
        (repo / "value.txt").symlink_to("relative-target")
        git(repo, "add", "value.txt")
    else:
        (repo / "binary.dat").write_bytes(b"\x00\xff\x10\x80")
        git(repo, "add", "binary.dat")
    git(repo, "commit", "-m", f"{shape} source")
    source = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", source)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, source, (source,))

    result = evaluate(repo, changeset, base)

    assert result.status is Status.DRAFT_PLANNED
    assert result.evidence["planned_tree"] == git(
        repo, "rev-parse", f"{merged}^{{tree}}"
    )


@pytest.mark.parametrize("shape", ["add_add", "delete_modify", "rename_rename"])
def test_conflict_evidence_covers_distinct_index_shapes(repo, shape):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    if shape == "add_add":
        source = commit_file(repo, "new.txt", "source\n", "source add")
    elif shape == "delete_modify":
        git(repo, "rm", "value.txt")
        git(repo, "commit", "-m", "source delete")
        source = git(repo, "rev-parse", "HEAD")
    else:
        git(repo, "mv", "value.txt", "renamed.txt")
        git(repo, "commit", "-m", "source rename")
        source = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", source)
    merged = git(repo, "rev-parse", "HEAD")
    changeset = prove(repo, merged, source, (source,))
    git(repo, "checkout", "--detach", base)
    if shape == "add_add":
        target = commit_file(repo, "new.txt", "target\n", "target add")
    elif shape == "delete_modify":
        target = commit_file(repo, "value.txt", "target\n", "target modify")
    else:
        git(repo, "mv", "value.txt", "other-name.txt")
        git(repo, "commit", "-m", "target rename")
        target = git(repo, "rev-parse", "HEAD")

    result = evaluate(repo, changeset, target)

    assert result.status is Status.BLOCKED_CONFLICT
    assert result.evidence["conflict_paths"]
    assert all(
        set(stages) <= {1, 2, 3}
        for stages in result.evidence["conflict_stages"].values()
    )


def test_gitlink_directional_decisions_use_new_status_contract(repo):
    desired = commit_file(repo, "one.txt", "one\n", "desired")
    target = commit_file(repo, "two.txt", "two\n", "target descendant")
    classify = git_module.classify_gitlink

    assert classify(repo, desired, target).status is Status.ALREADY_CONTAINED
    assert classify(repo, target, desired).status is Status.DRAFT_PLANNED

    base = desired
    git(repo, "checkout", "--detach", base)
    diverged = commit_file(repo, "other.txt", "other\n", "diverged")
    result = classify(repo, target, diverged)
    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET


def completed(*, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def dummy_changeset(*, commits=None, merge_commit=None):
    commits = commits or ("a" * 40,)
    merge_commit = merge_commit or "b" * 40
    return git_module.Changeset(
        kind=git_module.ChangesetKind.SINGLE,
        commits=commits,
        aggregate_base="c" * 40,
        aggregate_head=merge_commit,
        mainline=None,
        proof=git_module.ChangesetProof(
            method="test",
            source_head="d" * 40,
            source_merge_commit=merge_commit,
            original_commits=commits,
            aggregate_patch_id=None,
        ),
    )


@pytest.mark.parametrize(
    "kwargs,message",
    [
        (
            {
                "repository": "not-a-slug",
                "pull_number": 1,
                "merge_commit": "a" * 40,
            },
            "OWNER/REPO",
        ),
        (
            {
                "repository": "ROCm/TheRock",
                "pull_number": 0,
                "merge_commit": "a" * 40,
            },
            "positive",
        ),
        (
            {
                "repository": "ROCm/TheRock",
                "pull_number": 1,
                "merge_commit": "short",
            },
            "full lowercase SHA",
        ),
    ],
)
def test_source_identity_rejects_untrusted_fields(kwargs, message):
    with pytest.raises(ValueError, match=message):
        git_module.SourceIdentity(**kwargs)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"repository": "not-a-slug", "commit_sha": "a" * 40}, "OWNER/REPO"),
        (
            {"repository": "ROCm/rocm-systems", "commit_sha": "short"},
            "full lowercase SHA",
        ),
    ],
)
def test_commit_identity_rejects_untrusted_fields(kwargs, message):
    with pytest.raises(ValueError, match=message):
        git_module.CommitIdentity(**kwargs)


def test_git_object_and_worktree_path_helpers_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git_module, "_run", Mock(return_value=completed(returncode=1, stderr="bad"))
    )

    with pytest.raises(git_module.ChangesetError, match="parents"):
        git_module._parents(tmp_path, "a" * 40)
    with pytest.raises(git_module.ChangesetError, match="tree"):
        git_module._tree(tmp_path, "a" * 40)
    with pytest.raises(
        git_module.WorktreeStateError, match="worktree owner"
    ) as common_error:
        git_module._common_dir(tmp_path)
    assert common_error.value.reason_code == "worktree_identity_unavailable"
    with pytest.raises(
        git_module.WorktreeStateError, match="worktree index"
    ) as index_error:
        git_module._worktree_index(tmp_path)
    assert index_error.value.reason_code == "worktree_index_unavailable"
    assert git_module._valid_index(tmp_path / "missing-index") is False


def test_corrupt_index_can_rebuild_from_head_without_snapshot(monkeypatch, tmp_path):
    index = tmp_path / "index"
    index.write_bytes(b"bad")
    snapshot = tmp_path / "missing-snapshot"
    monkeypatch.setattr(git_module, "_worktree_index", lambda _worktree: index)
    monkeypatch.setattr(git_module, "_index_snapshot", lambda _worktree: snapshot)
    monkeypatch.setattr(git_module, "_resolve", lambda *_args: "a" * 40)

    def rebuild(_repo, *args, **kwargs):
        assert args[0] == "read-tree"
        Path(kwargs["extra_env"]["GIT_INDEX_FILE"]).write_bytes(b"DIRC rebuilt")
        return completed()

    monkeypatch.setattr(git_module, "_run", rebuild)
    git_module._restore_or_rebuild_index(tmp_path)
    assert index.read_bytes().startswith(b"DIRC")


def test_corrupt_index_rebuild_requires_head_and_valid_git_output(
    monkeypatch, tmp_path
):
    index = tmp_path / "index"
    index.write_bytes(b"bad")
    monkeypatch.setattr(git_module, "_worktree_index", lambda _worktree: index)
    monkeypatch.setattr(
        git_module, "_index_snapshot", lambda _worktree: tmp_path / "missing"
    )
    monkeypatch.setattr(git_module, "_resolve", lambda *_args: None)
    with pytest.raises(git_module.WorktreeStateError, match="no recoverable HEAD"):
        git_module._restore_or_rebuild_index(tmp_path)

    monkeypatch.setattr(git_module, "_resolve", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        git_module, "_run", Mock(return_value=completed(returncode=1, stderr="bad"))
    )
    with pytest.raises(git_module.WorktreeStateError, match="could not rebuild"):
        git_module._restore_or_rebuild_index(tmp_path)


def test_replay_rollback_rejects_wrong_owner_missing_target_and_failed_reset(
    monkeypatch, tmp_path
):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    missing_worktree = tmp_path / "missing"
    with pytest.raises(git_module.WorktreeStateError, match="not a worktree"):
        git_module.rollback_replay_worktree(repo_path, missing_worktree)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(git_module, "_common_dir", lambda _path: tmp_path / "common")
    monkeypatch.setattr(git_module, "_resolve", lambda *_args: None)
    with pytest.raises(git_module.WorktreeStateError, match="target is unavailable"):
        git_module.rollback_replay_worktree(repo_path, worktree, "missing")

    monkeypatch.setattr(git_module, "_resolve", lambda *_args: "a" * 40)
    monkeypatch.setattr(git_module, "_restore_or_rebuild_index", lambda _path: None)
    monkeypatch.setattr(git_module, "_tree", lambda *_args: "tree")

    def failed_reset(_repo, *args, **_kwargs):
        if args[:2] == ("reset", "--hard"):
            return completed(returncode=1, stderr="reset failed")
        if args[0] == "write-tree":
            return completed(stdout="tree\n")
        return completed()

    monkeypatch.setattr(git_module, "_run", failed_reset)
    with pytest.raises(git_module.WorktreeStateError, match="proven clean") as raised:
        git_module.rollback_replay_worktree(repo_path, worktree, "target")
    assert "reset failed" in raised.value.stderr


def test_prepare_replay_worktree_reports_creation_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git_module,
        "_run",
        Mock(return_value=completed(returncode=1, stderr="cannot add")),
    )

    with pytest.raises(git_module.WorktreeStateError, match="could not create"):
        git_module._prepare_replay_worktree(
            tmp_path, tmp_path / "new" / "worktree", "a" * 40
        )


def test_ancestry_and_conflict_parsers_fail_closed_on_unexpected_git_output(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(git_module, "_run", Mock(return_value=completed(returncode=2)))
    assert git_module._is_ancestor(tmp_path, "a", "b") is None

    malformed = (
        "bad-record\0"
        "100644 blob 2\tvalid-stage\0"
        "100644 blob not-an-int\tinvalid-stage\0"
    )
    monkeypatch.setattr(
        git_module, "_run", Mock(return_value=completed(stdout=malformed))
    )
    assert git_module._conflict_evidence(tmp_path)["conflict_paths"] == ["valid-stage"]


def test_identity_search_and_revert_checks_handle_negative_git_evidence(
    monkeypatch, tmp_path
):
    identity = git_module.SourceIdentity("ROCm/TheRock", 1, "a" * 40)
    monkeypatch.setattr(git_module, "_run", Mock(return_value=completed(returncode=1)))
    assert git_module._identity_candidates(tmp_path, "target", identity) == ()
    assert git_module._explicit_revert_after(tmp_path, "same", "same") is None

    monkeypatch.setattr(git_module, "_is_ancestor", lambda *_args: False)
    assert git_module._explicit_revert_after(tmp_path, "app", "target") is None


def test_destination_application_skips_unprovable_and_non_linear_candidates(
    monkeypatch, tmp_path
):
    identity = git_module.SourceIdentity("ROCm/TheRock", 1, "a" * 40)
    monkeypatch.setattr(
        git_module, "_identity_candidates", lambda *_args: ("broken", "merge")
    )
    calls = iter((git_module.ChangesetError("bad"), ("one", "two")))

    def parents(*_args):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(git_module, "_parents", parents)
    monkeypatch.setattr(git_module, "_tree", lambda *_args: "tree")

    assert (
        git_module._proven_destination_application(
            tmp_path, dummy_changeset(merge_commit="a" * 40), "target", identity, None
        )
        is None
    )


def test_patch_and_merge_base_proof_helpers_reject_git_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git_module.subprocess,
        "run",
        Mock(return_value=completed(returncode=1)),
    )
    with pytest.raises(git_module.ChangesetError, match="patch identity"):
        git_module._patch_id(tmp_path, "patch")

    monkeypatch.setattr(git_module, "_run", Mock(return_value=completed(returncode=1)))
    with pytest.raises(git_module.ChangesetError, match="tree delta"):
        git_module._diff_patch_id(tmp_path, "base", "head")
    with pytest.raises(git_module.ChangesetError, match="merge base"):
        git_module._merge_base(tmp_path, "base", "head")

    monkeypatch.setattr(git_module, "_parents", lambda *_args: ("one", "two"))
    with pytest.raises(git_module.ChangesetError, match="non-linear"):
        git_module._commit_patch_id(tmp_path, "merge")


def test_diff_patch_id_reports_missing_promisor_objects_as_local_evidence(
    monkeypatch, tmp_path
):
    error_type = getattr(git_module, "GitEvidenceError", None)
    assert error_type is not None, (
        "missing promisor objects require a typed local Git evidence failure"
    )
    diagnostic = (
        "warning: lazy fetching disabled; some objects may not be available\n"
        "fatal: could not fetch " + "a" * 40 + " from promisor remote\n"
    )
    monkeypatch.setattr(
        git_module,
        "_run",
        Mock(return_value=completed(returncode=128, stderr=diagnostic)),
    )

    with pytest.raises(error_type) as caught:
        git_module._diff_patch_id(tmp_path, "base", "head")

    assert caught.value.reason_code == "local_objects_incomplete"
    assert str(caught.value) == "Required Git objects are missing locally."
    assert caught.value.stderr == diagnostic.strip()


def test_prove_changeset_rejects_missing_objects_and_mismatched_source_head(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(git_module, "_resolve", lambda *_args: None)
    with pytest.raises(git_module.ChangesetError, match="required source objects"):
        git_module.prove_changeset(tmp_path, "merged", "head", ("commit",))

    monkeypatch.setattr(
        git_module,
        "_resolve",
        lambda _repo, revision: None if revision == "missing" else revision,
    )
    with pytest.raises(git_module.ChangesetError, match="every original"):
        git_module.prove_changeset(tmp_path, "merged", "head", ("missing",))

    monkeypatch.setattr(git_module, "_resolve", lambda _repo, revision: revision)
    with pytest.raises(git_module.ChangesetError, match="final PR commit"):
        git_module.prove_changeset(tmp_path, "merged", "head", ("other",))


def test_prove_changeset_rejects_merge_root_and_incomplete_range_shapes(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(git_module, "_resolve", lambda _repo, revision: revision)
    monkeypatch.setattr(git_module, "_parents", lambda *_args: ("one", "two"))
    monkeypatch.setattr(git_module, "_tree", lambda _repo, revision: f"tree-{revision}")
    with pytest.raises(git_module.ChangesetError, match="second parent"):
        git_module.prove_changeset(tmp_path, "merged", "head", ("head",))

    monkeypatch.setattr(git_module, "_parents", lambda *_args: ())
    with pytest.raises(git_module.ChangesetError, match="octopus or root"):
        git_module.prove_changeset(tmp_path, "merged", "head", ("head",))

    monkeypatch.setattr(git_module, "_parents", lambda *_args: ("parent",))
    monkeypatch.setattr(git_module, "_merge_base", lambda *_args: "base")
    monkeypatch.setattr(
        git_module, "_diff_patch_id", Mock(side_effect=("source", "merged"))
    )
    monkeypatch.setattr(git_module, "_run", Mock(return_value=completed(returncode=1)))
    with pytest.raises(git_module.ChangesetError, match="complete rebase range"):
        git_module.prove_changeset(tmp_path, "merged", "head", ("head",))


def test_git_result_without_changeset_keeps_only_destination_context():
    result = git_module._git_result(
        Status.BLOCKED_EVIDENCE,
        "reason",
        "message",
        target="target",
    )
    assert result.evidence == {"destination_head": "target"}


def test_evaluate_changeset_rejects_missing_target_source_identity_and_ancestry(
    monkeypatch, tmp_path
):
    changeset = dummy_changeset()
    monkeypatch.setattr(git_module, "_resolve", lambda *_args: None)
    assert (
        git_module.evaluate_changeset(tmp_path, changeset, "target").reason_code
        == "target_ref_missing"
    )

    monkeypatch.setattr(
        git_module,
        "_resolve",
        lambda _repo, revision: "target" if revision == "target" else None,
    )
    monkeypatch.setattr(git_module, "_tree", lambda *_args: "tree")
    assert (
        git_module.evaluate_changeset(tmp_path, changeset, "target").reason_code
        == "source_commit_missing"
    )

    monkeypatch.setattr(git_module, "_resolve", lambda _repo, revision: revision)
    mismatched = git_module.SourceIdentity("ROCm/TheRock", 1, "f" * 40)
    assert (
        git_module.evaluate_changeset(
            tmp_path, changeset, "target", source_identity=mismatched
        ).reason_code
        == "source_identity_mismatch"
    )

    monkeypatch.setattr(git_module, "_is_ancestor", lambda *_args: None)
    assert (
        git_module.evaluate_changeset(tmp_path, changeset, "target").reason_code
        == "ancestry_check_failed"
    )


@pytest.mark.parametrize(
    "mode,expected_reason",
    [
        ("failure", "trial_application_failed"),
        ("empty_status", "trial_tree_state_unexpected"),
        ("planned_unavailable", "planned_tree_unavailable"),
    ],
)
def test_evaluate_changeset_classifies_non_conflict_trial_failures(
    monkeypatch, tmp_path, mode, expected_reason
):
    changeset = dummy_changeset()
    monkeypatch.setattr(git_module, "_resolve", lambda _repo, revision: revision)
    monkeypatch.setattr(git_module, "_tree", lambda *_args: "destination-tree")
    monkeypatch.setattr(git_module, "_is_ancestor", lambda *_args: False)
    write_trees = 0

    def fake_run(_repo, *args, **_kwargs):
        nonlocal write_trees
        if args[:2] == ("worktree", "add") or args[:2] == (
            "worktree",
            "remove",
        ):
            return completed()
        if args[0] == "write-tree":
            write_trees += 1
            if write_trees == 1:
                return completed(stdout="before\n")
            if write_trees == 2:
                return completed(stdout="after\n")
            if mode == "planned_unavailable":
                return completed(returncode=1)
            return completed(stdout="planned\n")
        if args[0] == "cherry-pick":
            return completed(returncode=1 if mode == "failure" else 0, stderr="bad")
        if args[0] == "ls-files":
            return completed()
        if args[0] == "status":
            return completed(stdout="" if mode == "empty_status" else "M file\n")
        return completed()

    monkeypatch.setattr(git_module, "_run", fake_run)
    result = git_module.evaluate_changeset(tmp_path, changeset, "target")
    assert result.reason_code == expected_reason


def test_evaluate_changeset_structures_reusable_worktree_state_error(
    monkeypatch, tmp_path
):
    changeset = dummy_changeset()
    monkeypatch.setattr(git_module, "_resolve", lambda _repo, revision: revision)
    monkeypatch.setattr(git_module, "_tree", lambda *_args: "tree")
    monkeypatch.setattr(git_module, "_is_ancestor", lambda *_args: False)

    def fail(*_args):
        raise git_module.WorktreeStateError("unsafe_worktree", "unsafe", "detail")

    monkeypatch.setattr(git_module, "_prepare_replay_worktree", fail)
    result = git_module.evaluate_changeset(
        tmp_path, changeset, "target", worktree_path=tmp_path / "worktree"
    )
    assert result.reason_code == "unsafe_worktree"
    assert result.evidence["stderr"] == "detail"


def test_evaluate_cherry_pick_covers_missing_root_and_single_commit(repo):
    base = git(repo, "rev-parse", "HEAD")
    assert (
        git_module.evaluate_cherry_pick(repo, "missing", base).reason_code
        == "source_commit_missing"
    )
    assert (
        git_module.evaluate_cherry_pick(repo, base, base).reason_code
        == "unsupported_parent_count"
    )

    child = commit_file(repo, "child.txt", "child\n", "child")
    result = git_module.evaluate_cherry_pick(repo, child, base)
    assert result.status is Status.DRAFT_PLANNED


def test_gitlink_classifier_covers_missing_equal_and_unknown_ancestry(
    repo, monkeypatch
):
    head = git(repo, "rev-parse", "HEAD")
    assert git_module.classify_gitlink(repo, "missing", head).reason_code == (
        "gitlink_object_missing"
    )
    assert git_module.classify_gitlink(repo, head, head).reason_code == (
        "gitlink_pins_equal"
    )

    child = commit_file(repo, "child.txt", "child\n", "child")
    monkeypatch.setattr(git_module, "_is_ancestor", lambda *_args: None)
    assert git_module.classify_gitlink(repo, head, child).reason_code == (
        "gitlink_ancestry_unknown"
    )


def test_planning_and_write_materialization_share_one_command_builder():
    commit = "a" * 40
    assert git_module.cherry_pick_command(commit, None, commit_result=False) == (
        "cherry-pick",
        "--no-commit",
        commit,
    )
    assert git_module.cherry_pick_command(commit, 1, commit_result=True) == (
        "cherry-pick",
        "-x",
        "-m",
        "1",
        commit,
    )

    for bad_commit, mainline in (("short", None), (commit, True), (commit, 0)):
        with pytest.raises(ValueError):
            git_module.cherry_pick_command(bad_commit, mainline, commit_result=True)
