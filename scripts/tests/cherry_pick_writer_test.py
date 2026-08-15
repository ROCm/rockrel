# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import subprocess
from unittest.mock import Mock

import pytest

from scripts.cherry_pick import writer as writer_module
from scripts.cherry_pick.clients import ApiError
from scripts.cherry_pick.config import RepositoryConfig, TrainConfig, TrainRequirements
from scripts.cherry_pick.models import Result, Status


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
    git(repo, "config", "user.name", "Fixture Author")
    git(repo, "config", "user.email", "fixture@example.com")
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
        requirements=TrainRequirements(
            jira_fix_version="10.1.0a20260811",
            block_on_dependencies=True,
        ),
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branches=("main",), destination_branch="release/test"
            )
        },
    )


def plan(base, source):
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        source_pr="https://github.com/ROCm/TheRock/pull/7282",
        source_repository="ROCm/TheRock",
        train_id="10.1-20260811",
        destination_branch="release/test",
        evidence={
            "source_number": 7282,
            "source_title": "Compiler update ROCM-29371",
            "source_body": "Take ROCM-29371",
            "source_head": source,
            "source_merge_commit": source,
            "destination_head": base,
            "changeset_kind": "single",
            "ordered_commits": [source],
            "mainline": None,
            "proof_method": "normalized_patch_identity",
            "jira_keys": ["ROCM-29371"],
            "jira_fix_versions": ["10.1.0a20260811"],
            "unresolved_dependencies": [],
        },
    )


def github_client(*, existing=None, create_effect=None):
    github = Mock()
    github.pull_for_head.return_value = existing
    if create_effect is None:
        github.create_pull.return_value = "https://github.com/ROCm/TheRock/pull/9000"
    else:
        github.create_pull.side_effect = create_effect
    return github


def draft_writer(github):
    capability_factory = getattr(writer_module, "test_write_capability", None)
    assert capability_factory is not None, "writer requires an explicit test capability"
    return writer_module.DraftWriter(github, capability=capability_factory())


def test_writer_cannot_be_constructed_without_explicit_capability():
    with pytest.raises(PermissionError, match="capability"):
        writer_module.DraftWriter(github_client())


def test_creates_deterministic_branch_and_draft_with_explicit_bot_identity(
    repositories,
):
    repo, remote, base, source = repositories
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source))

    assert result.status is Status.DRAFT_CREATED
    branch = "shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", f"refs/heads/{branch}")
    committer = git(remote, "show", "-s", "--format=%cn%n%ce", branch).splitlines()
    assert committer == [
        "ROCm Cherry-Pick Automation",
        "cherry-pick-automation@users.noreply.github.com",
    ]
    kwargs = github.create_pull.call_args.kwargs
    assert kwargs["head"] == branch
    assert kwargs["base"] == "release/test"
    assert kwargs["draft"] is True
    assert "ROCM-29371" in kwargs["body"]
    assert "Operator review required" in kwargs["body"]
    assert "cherry-pick:ROCm/TheRock#7282:10.1-20260811" in kwargs["body"]


def test_refuses_write_outside_create_draft_mode(repositories):
    repo, _remote, base, source = repositories
    github = github_client()

    result = draft_writer(github).create(repo, train("shadow"), plan(base, source))

    assert result.status is Status.BLOCKED_POLICY
    assert result.reason_code == "write_mode_disabled"
    github.create_pull.assert_not_called()


def test_destination_head_movement_blocks_before_push(repositories):
    repo, _remote, base, source = repositories
    git(repo, "checkout", "release/test")
    moved = commit_file(repo, "moved.txt", "moved\n", "move target")
    git(repo, "push", "origin", "release/test")
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source))

    assert result.status is Status.BLOCKED_POLICY
    assert result.reason_code == "destination_head_moved"
    assert result.evidence["actual_destination_head"] == moved
    github.create_pull.assert_not_called()


def test_conflict_pushes_nothing(repositories):
    repo, remote, _base, _source = repositories
    git(repo, "checkout", "main")
    source = commit_file(repo, "value.txt", "source\n", "source conflict")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "release/test")
    target = commit_file(repo, "value.txt", "target\n", "target conflict")
    git(repo, "push", "origin", "release/test")
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(target, source))

    assert result.status is Status.BLOCKED_CONFLICT
    assert result.reason_code == "cherry_pick_conflict"
    branch = "refs/heads/shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", branch, check=False) == ""
    github.create_pull.assert_not_called()


def test_post_push_pull_api_failure_becomes_retryable_partial_write(repositories):
    repo, remote, base, source = repositories
    github = github_client(create_effect=ApiError(503, "unavailable"))

    result = draft_writer(github).create(repo, train(), plan(base, source))

    branch = "shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", f"refs/heads/{branch}")
    assert result.status is Status.RETRYABLE_PARTIAL_WRITE
    assert result.reason_code == "branch_pushed_pull_missing"
    assert result.evidence["automation_branch"] == branch


def test_fresh_clone_recovers_existing_branch_and_creates_missing_draft(
    repositories, tmp_path
):
    repo, remote, base, source = repositories
    first = github_client(create_effect=ApiError(503, "unavailable"))
    assert (
        draft_writer(first).create(repo, train(), plan(base, source)).status
        is Status.RETRYABLE_PARTIAL_WRITE
    )

    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", str(remote), str(fresh))
    second = github_client()
    result = draft_writer(second).create(fresh, train(), plan(base, source))

    assert result.status is Status.DRAFT_CREATED
    assert result.evidence["reused_existing_branch"] is True
    second.create_pull.assert_called_once()


def test_existing_expected_draft_is_idempotent(repositories):
    repo, _remote, base, source = repositories
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "open",
        "draft": True,
        "head": {"ref": "shared/cherry-pick/10.1-20260811/7282"},
        "base": {"ref": "release/test"},
    }
    github = github_client(existing=existing)

    result = draft_writer(github).create(repo, train(), plan(base, source))

    assert result.status is Status.DRAFT_EXISTS
    assert result.pull_request_url.endswith("/9000")
    github.create_pull.assert_not_called()


def test_existing_branch_with_different_tree_is_never_overwritten(repositories):
    repo, remote, base, source = repositories
    git(repo, "checkout", "--detach", base)
    commit_file(repo, "operator.txt", "operator\n", "operator change")
    branch = "shared/cherry-pick/10.1-20260811/7282"
    git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
    before = git(remote, "rev-parse", branch)
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source))

    assert result.status is Status.BLOCKED_POLICY
    assert result.reason_code == "automation_branch_mismatch"
    assert git(remote, "rev-parse", branch) == before
    github.create_pull.assert_not_called()


def test_rebase_range_writes_every_proven_commit_in_order(repositories):
    repo, remote, base, first = repositories
    git(repo, "checkout", "main")
    second = commit_file(repo, "second.txt", "second\n", "second")
    git(repo, "push", "origin", "main")
    request = plan(base, first)
    request.evidence["changeset_kind"] = "rebase_range"
    request.evidence["ordered_commits"] = [first, second]
    request.evidence["source_merge_commit"] = second
    github = github_client()

    result = draft_writer(github).create(repo, train(), request)

    assert result.status is Status.DRAFT_CREATED
    branch = "shared/cherry-pick/10.1-20260811/7282"
    messages = git(remote, "log", "--format=%s", f"release/test..{branch}").splitlines()
    assert messages == ["second", "source"]
    assert result.evidence["ordered_commits"] == [first, second]
