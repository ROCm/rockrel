import subprocess
from unittest.mock import Mock

import pytest

from scripts.cherry_pick.config import RepositoryConfig, TrainConfig, TrainRequirements
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.writer import DraftWriter


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
def repositories(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    repo = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(repo))
    git(repo, "config", "user.name", "Cherry-pick Test")
    git(repo, "config", "user.email", "cherry-pick@example.com")
    git(repo, "checkout", "-b", "main")
    base = commit_file(repo, "value.txt", "base\n", "base")
    git(repo, "push", "origin", "main")
    git(repo, "branch", "release/test", base)
    git(repo, "push", "origin", "release/test")
    source = commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "push", "origin", "main")
    return repo, remote, base, source


def train(mode="create-draft"):
    return TrainConfig(
        id="10.1-20260811",
        label="cherry-pick:10.1-20260811",
        state="active",
        mode=mode,
        requirements=TrainRequirements(jira_fix_version="10.1.0a20260811"),
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branch="main", destination_branch="release/test"
            )
        },
    )


def plan(base, source):
    return Result(
        status=Status.CHERRY_PICK_REQUIRED,
        reason_code="clean_trial_application",
        message="clean",
        source_pr="https://github.com/ROCm/TheRock/pull/7282",
        train_id="10.1-20260811",
        destination_branch="release/test",
        evidence={
            "source_repository": "ROCm/TheRock",
            "source_number": 7282,
            "source_title": "Compiler update ROCM-29371",
            "source_body": "Take ROCM-29371",
            "source_merge_commit": source,
            "destination_head": base,
        },
    )


def test_creates_deterministic_branch_and_draft_pull(repositories):
    repo, remote, base, source = repositories
    github = Mock()
    github.create_pull.return_value = "https://github.com/ROCm/TheRock/pull/9000"

    result = DraftWriter(github).create(repo, train(), plan(base, source))

    assert result.status is Status.DRAFT_CREATED
    branch = "shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", f"refs/heads/{branch}")
    kwargs = github.create_pull.call_args.kwargs
    assert kwargs["head"] == branch
    assert kwargs["base"] == "release/test"
    assert "ROCM-29371" in kwargs["body"]
    assert "cherry-pick:ROCm/TheRock#7282:10.1-20260811" in kwargs["body"]


def test_refuses_write_outside_create_draft_mode(repositories):
    repo, remote, base, source = repositories
    github = Mock()

    result = DraftWriter(github).create(repo, train("shadow"), plan(base, source))

    assert result.status is Status.BLOCKED
    assert result.reason_code == "write_mode_disabled"
    github.create_pull.assert_not_called()


def test_destination_head_movement_blocks_before_push(repositories):
    repo, remote, base, source = repositories
    git(repo, "checkout", "release/test")
    moved = commit_file(repo, "moved.txt", "moved\n", "move target")
    git(repo, "push", "origin", "release/test")
    github = Mock()

    result = DraftWriter(github).create(repo, train(), plan(base, source))

    assert result.status is Status.BLOCKED
    assert result.reason_code == "destination_head_moved"
    assert result.evidence["actual_destination_head"] == moved
    github.create_pull.assert_not_called()


def test_conflict_pushes_nothing(repositories):
    repo, remote, base, _ = repositories
    git(repo, "checkout", "main")
    source = commit_file(repo, "value.txt", "source\n", "source conflict")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "release/test")
    target = commit_file(repo, "value.txt", "target\n", "target conflict")
    git(repo, "push", "origin", "release/test")
    github = Mock()

    result = DraftWriter(github).create(repo, train(), plan(target, source))

    assert result.status is Status.MANUAL_RESOLUTION_REQUIRED
    assert result.reason_code == "cherry_pick_conflict"
    branch = "refs/heads/shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", branch, check=False) == ""
    github.create_pull.assert_not_called()
