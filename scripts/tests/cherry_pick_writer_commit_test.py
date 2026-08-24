# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from scripts.cherry_pick import writer as writer_module
from scripts.cherry_pick.core import CoreRequest
from scripts.cherry_pick.managed_stack import build_frontier_results
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.orchestrator import coverage_snapshot_sha256
from scripts.tests.cherry_pick_writer_readback_test import _post_create_pull
from scripts.tests.cherry_pick_writer_test import (
    PLAN_FINGERPRINT,
    draft_writer,
    github_client,
    git,
    repositories,
    train,
)


def standalone_commit_plan(base, commit, repo):
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        source_pr=f"https://github.com/ROCm/TheRock/commit/{commit}",
        source_repository="ROCm/TheRock",
        train_id="10.1-20260811",
        destination_branch="release/test",
        evidence={
            "source_kind": "commit",
            "source_commit": commit,
            "source_title": f"Cherry-pick {commit[:12]}",
            "source_body": "",
            "source_head": commit,
            "source_merge_commit": commit,
            "destination_head": base,
            "changeset_kind": "standalone_commit",
            "ordered_commits": [commit],
            "mainline": None,
            "proof_method": "exact_commit_identity",
            "dependencies": [],
            "dependency_status": "frontier",
            "plan_fingerprint": PLAN_FINGERPRINT,
            "planned_tree": git(repo, "rev-parse", f"{commit}^{{tree}}"),
            "coverage_snapshot_sha256": coverage_snapshot_sha256([]),
        },
    )


def test_writer_materializes_a_standalone_commit_frontier_as_a_draft(repositories):
    repo, remote, base, commit = repositories
    branch = "shared/cherry-pick/10.1-20260811/commit-" + commit[:12]
    created = {}
    github = github_client()

    def create_pull(*_args, **kwargs):
        created.update(kwargs)
        return "https://github.com/ROCm/TheRock/pull/9001"

    def pull_for_head(*_args, **_kwargs):
        if not created:
            return None
        return _post_create_pull(
            branch=branch,
            body=created["body"],
        ) | {"html_url": "https://github.com/ROCm/TheRock/pull/9001"}

    github.create_pull.side_effect = create_pull
    github.pull_for_head.side_effect = pull_for_head

    result = draft_writer(github).create(
        repo,
        train(),
        standalone_commit_plan(base, commit, repo),
    )

    assert result.status is Status.DRAFT_CREATED
    assert git(remote, "show-ref", "--verify", f"refs/heads/{branch}")
    kwargs = github.create_pull.call_args.kwargs
    assert kwargs["head"] == branch
    assert kwargs["base"] == "release/test"
    assert kwargs["draft"] is True
    assert kwargs["title"] == f"Cherry-pick {commit[:12]}"
    assert f"git -c core.hooksPath=/dev/null cherry-pick -x {commit}" in kwargs["body"]
    assert f"ROCm/TheRock@{commit}" in kwargs["body"]
    assert result.evidence["draft_readback_verified"] is True


def test_managed_standalone_commit_ignores_unrelated_open_pull_snapshot(repositories):
    repo, remote, base, commit = repositories
    commit_url = f"https://github.com/ROCm/TheRock/commit/{commit}"
    unrelated_pull = {
        "url": "https://github.com/ROCm/TheRock/pull/8000",
        "repository": "ROCm/TheRock",
        "number": 8000,
        "state": "open",
        "draft": False,
        "base_branch": "release/test",
        "base_sha": base,
        "head_repository": "ROCm/TheRock",
        "head_sha": "8" * 40,
    }
    request = CoreRequest.from_dict(
        {
            "schema_version": 3,
            "train_id": "10.1-20260811",
            "dependency_mode": "managed_stack",
            "source": {
                "kind": "pull_request",
                "url": "https://github.com/ROCm/TheRock/pull/7000",
                "repository": "ROCm/TheRock",
                "number": 7000,
                "base_branch": "main",
                "head_sha": "7" * 40,
                "merge_sha": "6" * 40,
                "ordered_commits": ["6" * 40],
                "body_sha256": "5" * 64,
                "destination": {
                    "repository": "ROCm/TheRock",
                    "branch": "release/test",
                    "head_sha": base,
                },
            },
            "prerequisites": [
                {
                    "kind": "commit",
                    "url": commit_url,
                    "repository": "ROCm/TheRock",
                    "commit_sha": commit,
                    "destination": {
                        "repository": "ROCm/TheRock",
                        "branch": "release/test",
                        "head_sha": base,
                    },
                }
            ],
            "prerequisite_edges": [
                {
                    "from": "https://github.com/ROCm/TheRock/pull/7000",
                    "to": commit_url,
                }
            ],
            "coverage_candidates": [unrelated_pull],
        }
    )
    core_result = Result(
        status=Status.AWAITING_DEPENDENCIES,
        reason_code="managed_dependency_frontier",
        message="one dependency wave is ready",
        evidence={
            "dependency_frontier": [
                {
                    "kind": "commit",
                    "url": commit_url,
                    "repository": "ROCm/TheRock",
                    "destination_branch": "release/test",
                    "destination_head": base,
                    "status": "draft_planned",
                    "reason_code": "clean_trial_application",
                    "evidence": {
                        "changeset_kind": "standalone_commit",
                        "ordered_commits": [commit],
                        "mainline": None,
                        "proof_method": "standalone_commit_single_parent",
                        "planned_tree": git(repo, "rev-parse", f"{commit}^{{tree}}"),
                    },
                }
            ]
        },
    )
    (plan,) = build_frontier_results(
        request=request,
        core_result=core_result,
        records={},
        authorization={"fingerprint": "4" * 64},
        execution_context="github-app",
        train_mode="create-draft",
        root_plan_fingerprint="3" * 64,
        core_request_fingerprint=request.fingerprint(),
    )
    assert plan.evidence["coverage_snapshot_sha256"] == coverage_snapshot_sha256([])
    github = github_client()
    github.pulls.return_value = [
        {
            "number": 8000,
            "html_url": unrelated_pull["url"],
            "state": "open",
            "draft": False,
            "base": {"ref": "release/test", "sha": base},
            "head": {
                "sha": unrelated_pull["head_sha"],
                "ref": "unrelated/change",
                "repo": {"full_name": "ROCm/TheRock"},
            },
        }
    ]
    writer = writer_module.DraftWriter(
        github,
        capability=writer_module.test_draft_write_authority(
            plan.evidence["plan_fingerprint"]
        ),
    )

    result = writer.create(repo, train(), plan)

    assert result.status is Status.DRAFT_CREATED
    branch = "shared/cherry-pick/10.1-20260811/commit-" + commit[:12]
    assert git(remote, "show-ref", "--verify", f"refs/heads/{branch}")
    github.pulls.assert_not_called()


def test_standalone_commit_rejects_a_nonempty_coverage_snapshot(repositories):
    repo, remote, base, commit = repositories
    request = standalone_commit_plan(base, commit, repo)
    request.evidence["coverage_snapshot_sha256"] = "0" * 64
    github = github_client()

    result = draft_writer(github).create(repo, train(), request)

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "coverage_snapshot_moved_during_write"
    assert result.evidence[
        "actual_coverage_snapshot_sha256"
    ] == coverage_snapshot_sha256([])
    assert result.evidence["expected_coverage_snapshot_sha256"] == "0" * 64
    branch = "shared/cherry-pick/10.1-20260811/commit-" + commit[:12]
    assert (
        git(remote, "show-ref", "--verify", f"refs/heads/{branch}", check=False) == ""
    )
    github.pulls.assert_not_called()
    github.create_pull.assert_not_called()
