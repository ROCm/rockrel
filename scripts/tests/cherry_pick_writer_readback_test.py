# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from unittest.mock import Mock

from scripts.cherry_pick import writer as writer_module
from scripts.cherry_pick.clients import ApiError
from scripts.cherry_pick.config import RepositoryConfig
from scripts.cherry_pick.models import Status
from scripts.tests.cherry_pick_writer_test import (
    draft_writer,
    github_client,
    plan,
    repositories,
    train,
)


def _post_create_pull(*, branch, body, draft=True, base="release/test"):
    return {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "open",
        "merged_at": None,
        "draft": draft,
        "body": body,
        "head": {
            "ref": branch,
            "repo": {"full_name": "ROCm/TheRock"},
        },
        "base": {"ref": base},
    }


def test_successful_push_is_read_back_before_pull_creation(monkeypatch, repositories):
    repo, _remote, base, source = repositories
    real_remote_head = writer_module._remote_head
    calls = []

    def remote_head(path, branch, *, environment=None):
        calls.append(branch)
        if branch.startswith("shared/cherry-pick") and calls.count(branch) == 2:
            return None
        return real_remote_head(path, branch, environment=environment)

    monkeypatch.setattr(writer_module, "_remote_head", remote_head)
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.RETRYABLE_PARTIAL_WRITE
    assert result.reason_code == "branch_push_readback_unavailable"
    assert calls.count("shared/cherry-pick/10.1-20260811/7282") == 2
    github.create_pull.assert_not_called()


def test_successful_push_must_read_back_the_exact_materialized_tree(
    monkeypatch, repositories
):
    repo, _remote, base, source = repositories
    real_remote_head = writer_module._remote_head
    branch_reads = 0

    def remote_head(path, branch, *, environment=None):
        nonlocal branch_reads
        value = real_remote_head(path, branch, environment=environment)
        if branch.startswith("shared/cherry-pick"):
            branch_reads += 1
            if branch_reads == 2:
                return "c" * 40
        return value

    monkeypatch.setattr(writer_module, "_remote_head", remote_head)
    monkeypatch.setattr(writer_module, "_tree_for_commit", Mock(return_value="d" * 40))
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "branch_push_readback_mismatch"
    github.create_pull.assert_not_called()


def test_created_draft_is_read_back_and_verified_before_success(repositories):
    repo, _remote, base, source = repositories
    branch = "shared/cherry-pick/10.1-20260811/7282"
    created = {}
    github = github_client()

    def create_pull(*_args, **kwargs):
        created.update(kwargs)
        return "https://github.com/ROCm/TheRock/pull/9000"

    def pull_for_head(*_args, **_kwargs):
        if not created:
            return None
        return _post_create_pull(branch=branch, body=created["body"])

    github.create_pull.side_effect = create_pull
    github.pull_for_head.side_effect = pull_for_head

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.DRAFT_CREATED
    assert result.reason_code == "draft_pull_created"
    assert github.pull_for_head.call_count == 2
    assert result.evidence["draft_readback_verified"] is True


def test_created_pull_readback_failure_is_a_recoverable_partial_transaction(
    repositories,
):
    repo, _remote, base, source = repositories
    github = github_client()
    github.pull_for_head.side_effect = [None, ApiError(503, "unavailable")]

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.RETRYABLE_PARTIAL_WRITE
    assert result.reason_code == "draft_created_readback_unavailable"
    assert result.pull_request_url == "https://github.com/ROCm/TheRock/pull/9000"


def test_created_pull_readback_identity_mismatch_never_reports_success(repositories):
    repo, _remote, base, source = repositories
    branch = "shared/cherry-pick/10.1-20260811/7282"
    github = github_client()
    github.pull_for_head.side_effect = [
        None,
        _post_create_pull(branch=branch, body="wrong body", draft=False),
    ]

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.RETRYABLE_PARTIAL_WRITE
    assert result.reason_code == "draft_created_readback_mismatch"
    assert result.pull_request_url == "https://github.com/ROCm/TheRock/pull/9000"


def test_writer_revalidates_the_reviewed_repository_destination_before_io(repositories):
    repo, _remote, base, source = repositories
    configured = train()
    configured.repositories["ROCm/TheRock"] = RepositoryConfig(
        source_branches=("main",),
        destination_branch="release/other",
    )
    github = github_client()

    result = draft_writer(github).create(
        repo, configured, plan(base, source, repo=repo)
    )

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "configured_destination_mismatch"
    github.pull_for_head.assert_not_called()
