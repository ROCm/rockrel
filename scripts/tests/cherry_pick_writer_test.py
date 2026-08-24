# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.cherry_pick import writer as writer_module
from scripts.cherry_pick.clients import ApiError
from scripts.cherry_pick.config import RepositoryConfig, TrainConfig
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.orchestrator import coverage_snapshot_sha256

PLAN_FINGERPRINT = "f" * 64
IDENTITY_MARKER = (
    "<!-- cherry-pick:v2:ROCm/TheRock#7282:10.1-20260811:" + PLAN_FINGERPRINT + " -->"
)


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
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branches=("main",), destination_branch="release/test"
            )
        },
    )


def plan(base, source, *, repo=None, planned_tree=None):
    if planned_tree is None:
        planned_tree = (
            git(repo, "rev-parse", f"{source}^{{tree}}") if repo else "f" * 40
        )
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
            "source_title": "Compiler update",
            "source_body": "Source description",
            "source_head": source,
            "source_merge_commit": source,
            "destination_head": base,
            "changeset_kind": "single",
            "ordered_commits": [source],
            "mainline": None,
            "proof_method": "normalized_patch_identity",
            "dependencies": [],
            "dependency_status": "contained",
            "plan_fingerprint": PLAN_FINGERPRINT,
            "planned_tree": planned_tree,
            "coverage_snapshot_sha256": coverage_snapshot_sha256([]),
        },
    )


def github_client(*, existing=None, create_effect=None):
    github = Mock()
    github.pulls.return_value = []
    created = {}

    def pull_for_head(*_args, **_kwargs):
        if not created:
            return existing
        return {
            "html_url": created["url"],
            "state": "open",
            "merged_at": None,
            "draft": True,
            "body": created["body"],
            "head": {
                "ref": created["head"],
                "repo": {"full_name": "ROCm/TheRock"},
            },
            "base": {"ref": created["base"]},
        }

    github.pull_for_head.side_effect = pull_for_head
    if create_effect is None:

        def create_pull(*_args, **kwargs):
            created.update(kwargs)
            created["url"] = "https://github.com/ROCm/TheRock/pull/9000"
            return created["url"]

        github.create_pull.side_effect = create_pull
    else:
        github.create_pull.side_effect = create_effect
    return github


def draft_writer(github):
    capability_factory = getattr(writer_module, "test_draft_write_authority", None)
    assert capability_factory is not None, "writer requires an explicit test capability"
    return writer_module.DraftWriter(github, capability=capability_factory())


def test_writer_cannot_be_constructed_without_explicit_capability():
    with pytest.raises(PermissionError, match="capability"):
        writer_module.DraftWriter(github_client())


def test_writer_binds_authority_to_exact_plan_fingerprint_before_io(tmp_path):
    request = plan("a" * 40, "b" * 40)
    authority = writer_module.test_draft_write_authority("e" * 64)
    github = github_client()
    writer = writer_module.DraftWriter(github, capability=authority)

    result = writer.create(tmp_path, train(), request)

    assert result.status is Status.BLOCKED_AUTHORIZATION
    assert result.reason_code == "write_authority_mismatch"
    github.pull_for_head.assert_not_called()


def test_creates_deterministic_branch_and_draft_with_explicit_bot_identity(
    repositories,
):
    repo, remote, base, source = repositories
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

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
    assert "Jira" not in kwargs["body"]
    assert "Dependencies" in kwargs["body"]
    assert "Operator review required" in kwargs["body"]
    executed = f"git -c core.hooksPath=/dev/null cherry-pick -x {source}"
    assert executed in kwargs["body"]
    assert (
        "cherry-pick:v2:ROCm/TheRock#7282:10.1-20260811:" + PLAN_FINGERPRINT
        in kwargs["body"]
    )


def test_refuses_write_outside_create_draft_mode(repositories):
    repo, _remote, base, source = repositories
    github = github_client()

    result = draft_writer(github).create(repo, train("shadow"), plan(base, source))

    assert result.status is Status.BLOCKED_AUTHORIZATION
    assert result.reason_code == "write_mode_disabled"
    github.create_pull.assert_not_called()


def test_destination_head_movement_blocks_before_push(repositories):
    repo, _remote, base, source = repositories
    git(repo, "checkout", "release/test")
    moved = commit_file(repo, "moved.txt", "moved\n", "move target")
    git(repo, "push", "origin", "release/test")
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "destination_head_moved"
    assert result.evidence["actual_destination_head"] == moved
    github.create_pull.assert_not_called()


@pytest.mark.parametrize(
    "current_destination,reason",
    [
        ("c" * 40, "destination_head_moved_during_write"),
        (
            RuntimeError("destination unavailable"),
            "destination_head_unavailable_during_write",
        ),
    ],
)
def test_destination_change_during_materialization_blocks_before_push(
    monkeypatch, tmp_path, current_destination, reason
):
    base = "a" * 40
    source = "b" * 40
    monkeypatch.setattr(
        writer_module,
        "_remote_head",
        Mock(side_effect=(base, None, current_destination)),
    )
    commands = []

    def fake_run(_repo, *args):
        commands.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return completed(stdout="f" * 40 + "\n")
        if args[:2] == ("rev-parse", "HEAD^{tree}"):
            return completed(stdout="d" * 40 + "\n")
        return completed()

    monkeypatch.setattr(writer_module, "_run", fake_run)
    github = github_client()

    result = draft_writer(github).create(
        tmp_path,
        train(),
        plan(base, source, planned_tree="d" * 40),
    )

    assert result.reason_code == reason
    assert all(command[0] != "push" for command in commands)
    github.create_pull.assert_not_called()


def test_materialized_tree_must_match_core_planned_tree(repositories):
    repo, remote, base, source = repositories
    request = plan(base, source)
    request.evidence["planned_tree"] = "0" * 40
    github = github_client()

    result = draft_writer(github).create(repo, train(), request)

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "planned_tree_mismatch"
    branch = "refs/heads/shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", branch, check=False) == ""
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
    assert result.evidence["conflict_paths"] == ["value.txt"]
    assert result.evidence["conflict_stages"] == {"value.txt": [1, 2, 3]}
    branch = "refs/heads/shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", branch, check=False) == ""
    github.create_pull.assert_not_called()


def test_post_push_pull_api_failure_becomes_retryable_partial_write(repositories):
    repo, remote, base, source = repositories
    github = github_client(create_effect=ApiError(503, "unavailable"))

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

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
        draft_writer(first).create(repo, train(), plan(base, source, repo=repo)).status
        is Status.RETRYABLE_PARTIAL_WRITE
    )

    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", str(remote), str(fresh))
    second = github_client()
    result = draft_writer(second).create(fresh, train(), plan(base, source, repo=fresh))

    assert result.status is Status.DRAFT_CREATED
    assert result.evidence["reused_existing_branch"] is True
    second.create_pull.assert_called_once()


def test_existing_expected_draft_is_idempotent(repositories):
    repo, _remote, base, source = repositories
    branch = "shared/cherry-pick/10.1-20260811/7282"
    git(repo, "checkout", "--detach", base)
    git(repo, "cherry-pick", source)
    git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "open",
        "draft": True,
        "body": IDENTITY_MARKER,
        "head": {"ref": branch},
        "base": {"ref": "release/test"},
    }
    github = github_client(existing=existing)

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.DRAFT_EXISTS
    assert result.pull_request_url.endswith("/9000")
    github.create_pull.assert_not_called()


def test_closed_unmerged_pull_does_not_suppress_recovery(repositories):
    repo, _remote, base, source = repositories
    abandoned = {
        "html_url": "https://github.com/ROCm/TheRock/pull/8999",
        "state": "closed",
        "merged_at": None,
        "head": {"ref": "shared/cherry-pick/10.1-20260811/7282"},
        "base": {"ref": "release/test"},
    }
    github = github_client(existing=abandoned)

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.DRAFT_CREATED
    assert result.pull_request_url.endswith("/9000")
    github.create_pull.assert_called_once()


def test_existing_pull_never_hides_a_mismatched_branch_tree(repositories):
    repo, remote, base, source = repositories
    git(repo, "checkout", "--detach", base)
    commit_file(repo, "operator.txt", "operator\n", "operator change")
    branch = "shared/cherry-pick/10.1-20260811/7282"
    git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "open",
        "draft": True,
        "body": IDENTITY_MARKER,
        "head": {"ref": branch},
        "base": {"ref": "release/test"},
    }
    github = github_client(existing=existing)

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "automation_branch_mismatch"
    github.create_pull.assert_not_called()


def test_pull_lookup_api_failure_is_structured_before_git_writes(repositories):
    repo, remote, base, source = repositories
    github = github_client()
    github.pull_for_head.side_effect = ApiError(503, "unavailable")

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "existing_pull_evidence_unavailable"
    branch = "refs/heads/shared/cherry-pick/10.1-20260811/7282"
    assert git(remote, "show-ref", "--verify", branch, check=False) == ""


def test_existing_branch_with_different_tree_is_never_overwritten(repositories):
    repo, remote, base, source = repositories
    git(repo, "checkout", "--detach", base)
    commit_file(repo, "operator.txt", "operator\n", "operator change")
    branch = "shared/cherry-pick/10.1-20260811/7282"
    git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
    before = git(remote, "rev-parse", branch)
    github = github_client()

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "automation_branch_mismatch"
    assert git(remote, "rev-parse", branch) == before
    github.create_pull.assert_not_called()


def test_rebase_range_writes_every_proven_commit_in_order(repositories):
    repo, remote, base, first = repositories
    git(repo, "checkout", "main")
    second = commit_file(repo, "second.txt", "second\n", "second")
    git(repo, "push", "origin", "main")
    request = plan(base, first, repo=repo)
    request.evidence["changeset_kind"] = "rebase_range"
    request.evidence["ordered_commits"] = [first, second]
    request.evidence["source_merge_commit"] = second
    request.evidence["planned_tree"] = git(repo, "rev-parse", f"{second}^{{tree}}")
    github = github_client()

    result = draft_writer(github).create(repo, train(), request)

    assert result.status is Status.DRAFT_CREATED
    branch = "shared/cherry-pick/10.1-20260811/7282"
    messages = git(remote, "log", "--format=%s", f"release/test..{branch}").splitlines()
    assert messages == ["second", "source"]
    assert result.evidence["ordered_commits"] == [first, second]


def completed(*, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_remote_head_and_tree_lookup_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer_module,
        "_run",
        Mock(return_value=completed(returncode=1, stderr="offline")),
    )
    with pytest.raises(RuntimeError, match="offline"):
        writer_module._remote_head(tmp_path, "release/test")
    assert writer_module._tree_for_commit(tmp_path, "a" * 40) is None


@pytest.mark.parametrize("value", [None, [], [""], ["short"], [3]])
def test_ordered_commit_parser_rejects_missing_or_non_string_values(value):
    assert writer_module._string_list(value) is None


def test_writer_rejects_non_planned_and_incomplete_results_before_git(tmp_path):
    source = "b" * 40
    base = "a" * 40
    github = github_client()

    non_planned = replace(plan(base, source), status=Status.BLOCKED_CONFLICT)
    result = draft_writer(github).create(tmp_path, train(), non_planned)
    assert result.reason_code == "plan_not_writable"

    incomplete = plan(base, source)
    incomplete.evidence.pop("proof_method")
    incomplete.evidence.pop("planned_tree")
    incomplete.evidence["ordered_commits"] = []
    result = draft_writer(github).create(tmp_path, train(), incomplete)
    assert result.reason_code == "incomplete_plan"
    assert "ordered_commits" in result.message
    assert "proof_method" in result.message
    assert "planned_tree" in result.message
    github.pull_for_head.assert_not_called()


def test_covered_by_existing_pull_is_never_writable(tmp_path):
    request = replace(
        plan("a" * 40, "b" * 40),
        status=Status.COVERED_BY_EXISTING_PR,
        pull_request_url="https://github.com/ROCm/TheRock/pull/9001",
    )
    github = github_client()

    result = draft_writer(github).create(tmp_path, train(), request)

    assert result.reason_code == "plan_not_writable"
    github.pull_for_head.assert_not_called()
    github.pulls.assert_not_called()
    github.create_pull.assert_not_called()


def test_open_pull_snapshot_drift_blocks_immediately_before_push(monkeypatch, tmp_path):
    base = "a" * 40
    source = "b" * 40
    monkeypatch.setattr(
        writer_module,
        "_remote_head",
        Mock(side_effect=(base, None, base)),
    )
    commands = []

    def fake_run(_repo, *args):
        commands.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return completed(stdout="f" * 40 + "\n")
        if args[:2] == ("rev-parse", "HEAD^{tree}"):
            return completed(stdout="d" * 40 + "\n")
        return completed()

    monkeypatch.setattr(writer_module, "_run", fake_run)
    github = github_client()
    github.pulls.return_value = [
        {
            "number": 9001,
            "html_url": "https://github.com/ROCm/TheRock/pull/9001",
            "state": "open",
            "draft": False,
            "base": {"ref": "release/test", "sha": base},
            "head": {
                "sha": "c" * 40,
                "ref": "manual/backport",
                "repo": {"full_name": "ROCm/TheRock"},
            },
        }
    ]

    result = draft_writer(github).create(
        tmp_path,
        train(),
        plan(base, source, planned_tree="d" * 40),
    )

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "coverage_snapshot_moved_during_write"
    assert all(command[0] != "push" for command in commands)
    github.create_pull.assert_not_called()


def test_open_pull_snapshot_api_failure_blocks_before_push(repositories):
    repo, remote, base, source = repositories
    github = github_client()
    github.pulls.side_effect = ApiError(503, "coverage unavailable")

    result = draft_writer(github).create(
        repo,
        train(),
        plan(base, source, repo=repo),
    )

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "coverage_snapshot_unavailable_during_write"
    assert result.evidence["error_type"] == "ApiError"
    branch = "shared/cherry-pick/10.1-20260811/7282"
    assert (
        git(remote, "show-ref", "--verify", f"refs/heads/{branch}", check=False) == ""
    )
    github.create_pull.assert_not_called()


def test_writer_structures_destination_and_branch_lookup_failures(
    monkeypatch, tmp_path
):
    source = "b" * 40
    base = "a" * 40

    destination_failure = Mock(side_effect=RuntimeError("destination offline"))
    monkeypatch.setattr(writer_module, "_remote_head", destination_failure)
    result = draft_writer(github_client()).create(tmp_path, train(), plan(base, source))
    assert result.reason_code == "destination_head_unavailable"

    branch_failure = Mock(side_effect=[base, RuntimeError("branch offline")])
    monkeypatch.setattr(writer_module, "_remote_head", branch_failure)
    result = draft_writer(github_client()).create(tmp_path, train(), plan(base, source))
    assert result.reason_code == "automation_branch_unavailable"


def test_writer_rejects_non_integer_merge_mainline_before_worktree(
    monkeypatch, tmp_path
):
    source = "b" * 40
    base = "a" * 40
    request = plan(base, source)
    request.evidence["mainline"] = "1"
    monkeypatch.setattr(writer_module, "_remote_head", Mock(side_effect=[base, None]))

    result = draft_writer(github_client()).create(tmp_path, train(), request)

    assert result.reason_code == "invalid_mainline"


def test_tree_lookup_fetches_missing_commit_then_resolves_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer_module,
        "_run",
        Mock(
            side_effect=(
                completed(returncode=1),
                completed(),
                completed(stdout="tree\n"),
            )
        ),
    )

    assert writer_module._tree_for_commit(tmp_path, "a" * 40) == "tree"


@pytest.mark.parametrize(
    "failure,expected_reason",
    [
        ("add", "worktree_creation_failed"),
        ("identity", "git_identity_configuration_failed"),
        ("cherry-pick", "cherry_pick_write_failed"),
    ],
)
def test_writer_structures_local_preflight_failures(
    monkeypatch, tmp_path, failure, expected_reason
):
    base = "a" * 40
    source = "b" * 40
    monkeypatch.setattr(writer_module, "_remote_head", Mock(side_effect=(base, None)))

    def fake_run(_repo, *args):
        if args[:2] == ("worktree", "add"):
            return completed(returncode=1) if failure == "add" else completed()
        if args[:2] == ("config", "user.name"):
            return completed(returncode=1) if failure == "identity" else completed()
        if args[0] == "config":
            return completed()
        if args[0] == "cherry-pick":
            return (
                completed(returncode=1, stderr="failed")
                if failure == "cherry-pick"
                else completed()
            )
        if args[:2] == ("ls-files", "-u"):
            return completed()
        return completed()

    monkeypatch.setattr(writer_module, "_run", fake_run)
    writer = writer_module.DraftWriter(
        github_client(),
        capability=writer_module.test_draft_write_authority(),
        scratch_root=tmp_path / "scratch",
    )

    result = writer.create(tmp_path, train(), plan(base, source))

    assert result.reason_code == expected_reason


def test_writer_adds_mainline_to_merge_cherry_pick_command(monkeypatch, tmp_path):
    base = "a" * 40
    source = "b" * 40
    request = plan(base, source)
    request.evidence["mainline"] = 1
    monkeypatch.setattr(writer_module, "_remote_head", Mock(side_effect=(base, None)))
    commands = []

    def fake_run(_repo, *args):
        commands.append(args)
        if args[0] == "cherry-pick":
            return completed(returncode=1)
        return completed()

    monkeypatch.setattr(writer_module, "_run", fake_run)
    writer = writer_module.DraftWriter(
        github_client(),
        capability=writer_module.test_draft_write_authority(),
        scratch_root=tmp_path / "scratch",
    )

    result = writer.create(tmp_path, train(), request)

    assert result.reason_code == "cherry_pick_write_failed"
    assert ("cherry-pick", "-x", "-m", "1", source) in commands


def test_active_identity_pull_without_remote_branch_blocks_recovery(repositories):
    repo, _remote, base, source = repositories
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "open",
        "merged_at": None,
        "draft": True,
        "body": IDENTITY_MARKER,
    }

    result = draft_writer(github_client(existing=existing)).create(
        repo, train(), plan(base, source, repo=repo)
    )

    assert result.reason_code == "existing_pull_branch_missing"


def test_active_pull_with_invalid_identity_or_url_blocks_automation(repositories):
    repo, _remote, base, source = repositories
    branch = "shared/cherry-pick/10.1-20260811/7282"
    git(repo, "checkout", "--detach", base)
    git(repo, "cherry-pick", source)
    git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
    existing = {
        "html_url": 9000,
        "state": "open",
        "merged_at": None,
    }
    github = github_client(existing=existing)

    result = draft_writer(github).create(repo, train(), plan(base, source, repo=repo))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "existing_pull_identity_mismatch"
    github.create_pull.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"body": "missing marker", "draft": True},
        {
            "body": IDENTITY_MARKER,
            "draft": False,
        },
    ],
)
def test_open_deterministic_branch_requires_exact_marker_and_draft(
    repositories, overrides
):
    repo, _remote, base, source = repositories
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "open",
        "merged_at": None,
        **overrides,
    }

    result = draft_writer(github_client(existing=existing)).create(
        repo, train(), plan(base, source, repo=repo)
    )

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "existing_pull_identity_mismatch"


@pytest.mark.parametrize(
    "raced_head,raced_tree,expected_reason",
    [
        ("c" * 40, "d" * 40, "draft_pull_created"),
        ("c" * 40, "e" * 40, "automation_branch_mismatch"),
        (None, None, "branch_push_failed"),
    ],
)
def test_push_race_is_reused_only_for_the_exact_tree(
    monkeypatch, tmp_path, raced_head, raced_tree, expected_reason
):
    base = "a" * 40
    source = "b" * 40
    monkeypatch.setattr(
        writer_module,
        "_remote_head",
        Mock(side_effect=(base, None, base, raced_head, raced_head)),
    )
    monkeypatch.setattr(
        writer_module, "_tree_for_commit", Mock(return_value=raced_tree)
    )

    def fake_run(_repo, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return completed(stdout="f" * 40 + "\n")
        if args[:2] == ("rev-parse", "HEAD^{tree}"):
            return completed(stdout="d" * 40 + "\n")
        if args[0] == "push":
            return completed(returncode=1, stderr="push failed")
        return completed()

    monkeypatch.setattr(writer_module, "_run", fake_run)
    writer = writer_module.DraftWriter(
        github_client(),
        capability=writer_module.test_draft_write_authority(),
        scratch_root=tmp_path / "scratch",
    )

    result = writer.create(tmp_path, train(), plan(base, source, planned_tree="d" * 40))

    assert result.reason_code == expected_reason


def test_writer_uses_creation_only_compare_and_swap_push():
    source = Path("scripts/cherry_pick/writer.py").read_text()
    assert "--force-with-lease=refs/heads/" in source
    assert "cherry_pick_command" in source
    assert "BLOCKED_POLICY" not in source
    assert "jira" not in source.lower()
    assert "RemoteWriteCapability" not in source


def test_action_git_auth_is_process_scoped_and_never_embedded_in_arguments(
    monkeypatch, tmp_path
):
    captured = {}

    def run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        return completed()

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "short-lived-write-token")
    monkeypatch.setattr(writer_module.subprocess, "run", run)

    writer_module._run(tmp_path, "status", "--porcelain")

    assert "short-lived-write-token" not in " ".join(captured["arguments"])
    environment = captured["environment"]
    assert environment["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert environment["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
    assert "short-lived-write-token" not in environment["GIT_CONFIG_VALUE_0"]
    assert environment["GIT_CONFIG_KEY_1"] == "credential.interactive"
    assert environment["GIT_CONFIG_VALUE_1"] == "never"


def test_writer_rejects_unknown_source_kind_before_github_or_git_io(tmp_path):
    request = plan("a" * 40, "b" * 40)
    request.evidence["source_kind"] = "tag"
    github = github_client()

    result = draft_writer(github).create(tmp_path, train(), request)

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "invalid_source_kind"
    github.pull_for_head.assert_not_called()
