# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import subprocess

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
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == merged


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

    result = evaluate(repo, changeset, target)
    assert result.status is Status.BLOCKED_CONFLICT
    assert result.reason_code == "cherry_pick_conflict"


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
