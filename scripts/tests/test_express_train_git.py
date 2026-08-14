import subprocess

import pytest

from scripts.express_train.git import classify_gitlink, evaluate_cherry_pick
from scripts.express_train.models import Status


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


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Express Train Test")
    git(path, "config", "user.email", "express-train@example.com")
    commit_file(path, "value.txt", "base\n", "base")
    return path


def test_clean_change_is_required_without_mutating_checkout(repo):
    base = git(repo, "rev-parse", "HEAD")
    source = commit_file(repo, "source.txt", "change\n", "source")
    git(repo, "branch", "release/test", base)
    original_head = git(repo, "rev-parse", "HEAD")

    result = evaluate_cherry_pick(repo, source, "release/test")

    assert result.status is Status.CHERRY_PICK_REQUIRED
    assert result.evidence["target_head"] == base
    assert git(repo, "rev-parse", "HEAD") == original_head
    assert git(repo, "status", "--porcelain") == ""


def test_reachable_source_is_already_contained(repo):
    source = commit_file(repo, "source.txt", "change\n", "source")
    target = commit_file(repo, "later.txt", "later\n", "target descendant")

    result = evaluate_cherry_pick(repo, source, target)

    assert result.status is Status.ALREADY_CONTAINED
    assert result.reason_code == "source_ancestor_of_target"


def test_patch_equivalent_target_is_already_contained(repo):
    base = git(repo, "rev-parse", "HEAD")
    source = commit_file(repo, "equivalent.txt", "same\n", "source")
    git(repo, "checkout", "--detach", base)
    equivalent = commit_file(repo, "equivalent.txt", "same\n", "independent target")

    result = evaluate_cherry_pick(repo, source, equivalent)

    assert result.status is Status.ALREADY_CONTAINED
    assert result.reason_code == "empty_trial_application"


def test_conflict_requires_manual_resolution(repo):
    base = git(repo, "rev-parse", "HEAD")
    source = commit_file(repo, "value.txt", "source\n", "source")
    git(repo, "checkout", "--detach", base)
    target = commit_file(repo, "value.txt", "target\n", "target")

    result = evaluate_cherry_pick(repo, source, target)

    assert result.status is Status.MANUAL_RESOLUTION_REQUIRED
    assert result.reason_code == "cherry_pick_conflict"


def test_missing_source_blocks(repo):
    result = evaluate_cherry_pick(repo, "f" * 40, "main")
    assert result.status is Status.BLOCKED
    assert result.reason_code == "source_commit_missing"


def test_two_parent_merge_uses_first_parent(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    commit_file(repo, "topic.txt", "topic\n", "topic")
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "main\n", "main")
    git(repo, "merge", "--no-ff", "topic", "-m", "merge topic")
    merge_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "release/test", base)

    result = evaluate_cherry_pick(repo, merge_commit, "release/test")

    assert result.status is Status.CHERRY_PICK_REQUIRED
    assert result.evidence["mainline"] == 1


def test_gitlink_equal_and_target_descendant_are_contained(repo):
    desired = commit_file(repo, "one.txt", "one\n", "desired")
    target = commit_file(repo, "two.txt", "two\n", "target descendant")

    assert classify_gitlink(repo, desired, desired).status is Status.ALREADY_CONTAINED
    descendant = classify_gitlink(repo, desired, target)
    assert descendant.status is Status.ALREADY_CONTAINED
    assert descendant.reason_code == "target_pin_contains_desired"


def test_gitlink_target_ancestor_requires_update(repo):
    target = git(repo, "rev-parse", "HEAD")
    desired = commit_file(repo, "new.txt", "new\n", "desired descendant")

    result = classify_gitlink(repo, desired, target)

    assert result.status is Status.CHERRY_PICK_REQUIRED
    assert result.reason_code == "desired_pin_ahead_of_target"


def test_gitlink_divergence_requires_manual_resolution(repo):
    base = git(repo, "rev-parse", "HEAD")
    desired = commit_file(repo, "desired.txt", "desired\n", "desired")
    git(repo, "checkout", "--detach", base)
    target = commit_file(repo, "target.txt", "target\n", "target")

    result = classify_gitlink(repo, desired, target)

    assert result.status is Status.MANUAL_RESOLUTION_REQUIRED
    assert result.reason_code == "gitlink_histories_diverged"
