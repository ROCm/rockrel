# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from types import SimpleNamespace
from unittest.mock import Mock

from scripts.cherry_pick.config import (
    RepositoryConfig,
    TrainCatalog,
    TrainConfig,
    TrainRequirements,
)
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.orchestrator import (
    Planner,
    automation_branch,
    identity_marker,
    render_pull_body,
)


SOURCE_URL = "https://github.com/ROCm/TheRock/pull/7282"
SOURCE_SHA = "a" * 40
SOURCE_HEAD = "c" * 40
DESTINATION_SHA = "b" * 40


def config(
    mode="validate",
    *,
    state="active",
    jira_fix_version="10.1.0a20260811",
    block_on_dependencies=True,
):
    train = TrainConfig(
        id="10.1-20260811",
        label="cherry-pick:10.1-20260811",
        state=state,
        mode=mode,
        requirements=TrainRequirements(
            jira_fix_version=jira_fix_version,
            block_on_dependencies=block_on_dependencies,
        ),
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branches=("main", "integration/next"),
                destination_branch="release/bkc/therock-10.1-20260811",
            )
        },
    )
    return TrainCatalog(trains={train.id: train})


def github(pulls=None, merged=True):
    client = Mock()
    client.pull.return_value = {
        "number": 7282,
        "html_url": SOURCE_URL,
        "title": "chore(compiler): SMP for Compiler ww23.20 ROCM-29371",
        "body": "Take ROCM-29371",
        "state": "closed" if merged else "open",
        "merged": merged,
        "merge_commit_sha": SOURCE_SHA if merged else None,
        "head": {"sha": SOURCE_HEAD},
        "base": {"ref": "main"},
        "labels": [{"name": "cherry-pick:10.1-20260811"}],
    }
    client.pull_commits.return_value = ("d" * 40, "e" * 40)
    client.label_actor.return_value = "operator"
    client.permission.return_value = "write"
    client.branch.return_value = SimpleNamespace(exists=True, sha=DESTINATION_SHA)
    client.destination_policy.return_value = SimpleNamespace(
        pull_request_required=True,
        rule_ids=(20,),
        required_approvals=1,
        require_last_push_approval=True,
        allowed_merge_methods=("squash",),
    )
    client.pulls.return_value = pulls or []
    return client


def jira(*, dependencies=(), ordering_notes=()):
    client = Mock()
    client.issue_evidence.return_value = SimpleNamespace(
        fix_versions=frozenset({"10.1.0a20260811"}),
        dependencies=tuple(dependencies),
        ordering_notes=tuple(ordering_notes),
    )
    return client


def changeset():
    return SimpleNamespace(
        kind=SimpleNamespace(value="squash"),
        commits=(SOURCE_SHA,),
        aggregate_base="f" * 40,
        aggregate_head=SOURCE_SHA,
        mainline=None,
        proof=SimpleNamespace(method="normalized_patch_identity", as_dict=lambda: {}),
    )


def evaluator_result(status=None):
    status = status or Status.DRAFT_PLANNED
    return Result(
        status=status,
        reason_code="clean_trial_application",
        message="clean",
        evidence={
            "destination_head": DESTINATION_SHA,
            "changeset_kind": "squash",
            "ordered_commits": [SOURCE_SHA],
            "mainline": None,
            "proof_method": "normalized_patch_identity",
        },
    )


def planner(
    *,
    catalog=None,
    github_client=None,
    jira_client=None,
    proof=None,
    evaluator=None,
    coverage=None,
):
    proof = proof or Mock(return_value=changeset())
    evaluator = evaluator or Mock(return_value=evaluator_result())
    coverage = coverage or Mock(return_value=None)
    return (
        Planner(
            catalog or config(),
            github_client or github(),
            jira_client if jira_client is not None else jira(),
            changeset_builder=proof,
            evaluator=evaluator,
            coverage_evaluator=coverage,
        ),
        proof,
        evaluator,
    )


def test_validate_mode_is_manual_only_and_disabled_mode_does_no_api_io(tmp_path):
    validate_client = github()
    validate, _proof, _evaluator = planner(
        catalog=config(mode="validate"), github_client=validate_client
    )
    result = validate.plan(
        SOURCE_URL, "10.1-20260811", tmp_path, event_action="labeled"
    )
    assert result.status is Status.CANCELLED
    assert result.reason_code == "validate_mode_manual_only"
    validate_client.pull.assert_not_called()

    disabled_client = github()
    disabled, _proof, _evaluator = planner(
        catalog=config(mode="disabled"), github_client=disabled_client
    )
    result = disabled.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.CANCELLED
    assert result.reason_code == "train_disabled"
    disabled_client.pull.assert_not_called()


def test_shadow_event_plans_but_never_writes(tmp_path):
    controller, proof, evaluator = planner(catalog=config(mode="shadow"))
    result = controller.plan(
        SOURCE_URL, "10.1-20260811", tmp_path, event_action="labeled"
    )
    assert result.status is Status.DRAFT_PLANNED
    assert result.evidence["train_mode"] == "shadow"
    proof.assert_called_once()
    evaluator.assert_called_once()


def test_open_pr_awaits_merge_without_changeset_work(tmp_path):
    controller, proof, evaluator = planner(github_client=github(merged=False))
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.AWAITING_MERGE
    proof.assert_not_called()
    evaluator.assert_not_called()


def test_unlabeled_event_cancels_only_absent_train_label(tmp_path):
    client = github()
    client.pull.return_value["labels"] = []
    controller, proof, _evaluator = planner(github_client=client)
    result = controller.plan(
        SOURCE_URL, "10.1-20260811", tmp_path, event_action="unlabeled"
    )
    assert result.status is Status.CANCELLED
    assert result.reason_code == "train_label_removed"
    proof.assert_not_called()


def test_effective_pull_request_rule_is_required(tmp_path):
    client = github()
    client.destination_policy.return_value.pull_request_required = False
    controller, proof, _evaluator = planner(github_client=client)
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.BLOCKED_POLICY
    assert result.reason_code == "destination_pull_request_rule_missing"
    proof.assert_not_called()


def test_declared_pr_or_jira_dependencies_block_before_git(tmp_path):
    client = github()
    client.pull.return_value[
        "body"
    ] += "\nCherry-Pick-Depends-On: https://github.com/ROCm/TheRock/pull/7000"
    controller, proof, _evaluator = planner(
        github_client=client,
        jira_client=jira(dependencies=("ROCM-1",)),
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.BLOCKED_DEPENDENCY
    assert result.reason_code == "unresolved_dependencies"
    assert sorted(result.evidence["unresolved_dependencies"]) == [
        "ROCM-1",
        "https://github.com/ROCm/TheRock/pull/7000",
    ]
    proof.assert_not_called()


def test_optional_jira_policy_performs_no_jira_call(tmp_path):
    jira_client = Mock()
    controller, _proof, _evaluator = planner(
        catalog=config(jira_fix_version=None), jira_client=jira_client
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.DRAFT_PLANNED
    jira_client.issue_evidence.assert_not_called()


def test_changeset_builder_receives_canonical_head_merge_and_commits(tmp_path):
    controller, proof, evaluator = planner()
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.DRAFT_PLANNED
    proof.assert_called_once_with(
        tmp_path,
        SOURCE_SHA,
        SOURCE_HEAD,
        ("d" * 40, "e" * 40),
    )
    evaluator.assert_called_once()
    assert result.source_repository == "ROCm/TheRock"
    assert result.evidence["source_head"] == SOURCE_HEAD
    assert result.evidence["destination_rule_ids"] == [20]


def test_ambiguous_changeset_error_is_structured_and_fail_closed(tmp_path):
    from scripts.cherry_pick import git as git_module

    error_type = getattr(git_module, "ChangesetError", RuntimeError)
    proof = Mock(side_effect=error_type("could not prove complete changeset"))
    controller, _proof, evaluator = planner(proof=proof)
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert result.reason_code == "changeset_proof_failed"
    evaluator.assert_not_called()


def test_existing_identity_prevents_duplicate(tmp_path):
    marker = identity_marker("ROCm/TheRock", 7282, "10.1-20260811")
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/7357",
        "state": "open",
        "body": f"Existing coverage\n{marker}",
        "head": {"ref": "some-branch", "sha": "c" * 40},
    }
    controller, proof, evaluator = planner(github_client=github([existing]))
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.COVERED_BY_EXISTING_PR
    assert result.pull_request_url.endswith("/7357")
    proof.assert_not_called()
    evaluator.assert_not_called()


def test_closed_unmerged_identity_is_replanned_for_recovery(tmp_path):
    marker = identity_marker("ROCm/TheRock", 7282, "10.1-20260811")
    abandoned = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "state": "closed",
        "merged_at": None,
        "body": marker,
        "head": {
            "ref": "shared/cherry-pick/10.1-20260811/7282",
            "sha": "c" * 40,
        },
    }
    controller, proof, evaluator = planner(github_client=github([abandoned]))
    result = controller.plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.DRAFT_PLANNED
    proof.assert_called_once()
    evaluator.assert_called_once()


def test_rich_pull_body_matches_rocm_operator_review_contract():
    marker = identity_marker("ROCm/TheRock", 7282, "10.1-20260811")
    body = render_pull_body(
        marker=marker,
        source_url=SOURCE_URL,
        source_repository="ROCm/TheRock",
        source_sha=SOURCE_SHA,
        source_head=SOURCE_HEAD,
        train_id="10.1-20260811",
        destination_branch="release/bkc/therock-10.1-20260811",
        destination_head=DESTINATION_SHA,
        changeset_kind="squash",
        ordered_commits=(SOURCE_SHA,),
        mainline=None,
        jira_keys=("ROCM-29371",),
        jira_fix_versions=("10.1.0a20260811",),
        unresolved_dependencies=(),
        proof_method="normalized_patch_identity",
        source_body="Take ROCM-29371",
    )
    assert marker in body
    for heading in (
        "Operator review required",
        "Source and destination",
        "Application and provenance",
        "Jira and dependencies",
        "Test plan and result",
        "Submission checklist",
        "Original source pull request",
    ):
        assert heading in body
    assert SOURCE_HEAD in body
    assert DESTINATION_SHA in body
    assert "10.1.0a20260811" in body
    assert "The automation never marks this PR ready or merges it" in body


def test_identity_and_branch_names_are_stable():
    assert automation_branch("10.1-20260811", 7282) == (
        "shared/cherry-pick/10.1-20260811/7282"
    )
    assert identity_marker("ROCm/TheRock", 7282, "10.1-20260811") == (
        "<!-- cherry-pick:ROCm/TheRock#7282:10.1-20260811 -->"
    )
