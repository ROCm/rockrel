# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from scripts.cherry_pick.config import RepositoryConfig, TrainConfig, TrainRequirements
from scripts.cherry_pick.models import Status
from scripts.cherry_pick.policy import QualificationFacts, qualify_request


def train(**overrides):
    value = TrainConfig(
        id="10.1-20260811",
        label="cherry-pick:10.1-20260811",
        state="active",
        mode="validate",
        requirements=TrainRequirements(
            jira_fix_version="10.1.0a20260811",
            block_on_dependencies=True,
        ),
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branches=("main", "integration/next"),
                destination_branch="release/bkc/therock-10.1-20260811",
            )
        },
    )
    return TrainConfig(**({**value.__dict__, **overrides}))


def facts(**overrides):
    value = {
        "source_pr": "https://github.com/ROCm/TheRock/pull/123",
        "repository": "ROCm/TheRock",
        "base_branch": "main",
        "merged": True,
        "closed": True,
        "label_actor_permission": "write",
        "jira_fix_versions": frozenset({"10.1.0a20260811"}),
        "destination_exists": True,
        "destination_pr_required": True,
        "unresolved_dependencies": (),
        "evidence_errors": (),
    }
    value.update(overrides)
    return QualificationFacts(**value)


def test_qualified_merged_request_advances_to_draft_plan():
    result = qualify_request(train(), facts())
    assert result.status is Status.DRAFT_PLANNED
    assert result.reason_code == "qualified_for_planning"


def test_open_request_awaits_merge():
    result = qualify_request(train(), facts(merged=False, closed=False))
    assert result.status is Status.AWAITING_MERGE


def test_closed_unmerged_request_is_cancelled():
    result = qualify_request(train(), facts(merged=False, closed=True))
    assert result.status is Status.CANCELLED


def test_transient_evidence_failure_blocks_as_evidence():
    result = qualify_request(train(), facts(evidence_errors=("jira_timeout",)))
    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "evidence_unavailable"


def test_inactive_and_disabled_trains_have_distinct_results():
    inactive = qualify_request(train(state="inactive"), facts())
    assert inactive.status is Status.INELIGIBLE_SOURCE
    assert inactive.reason_code == "inactive_train"

    disabled = qualify_request(train(mode="disabled"), facts())
    assert disabled.status is Status.CANCELLED
    assert disabled.reason_code == "train_disabled"


def test_unconfigured_repository_and_wrong_source_base_are_ineligible():
    unconfigured = qualify_request(train(), facts(repository="ROCm/other"))
    assert unconfigured.status is Status.INELIGIBLE_SOURCE
    assert unconfigured.reason_code == "repository_not_configured"

    wrong_base = qualify_request(train(), facts(base_branch="release/old"))
    assert wrong_base.status is Status.INELIGIBLE_SOURCE
    assert wrong_base.reason_code == "source_branch_mismatch"


def test_any_configured_source_branch_is_eligible():
    result = qualify_request(train(), facts(base_branch="integration/next"))
    assert result.status is Status.DRAFT_PLANNED


def test_unauthorized_labeler_is_blocked_by_policy():
    result = qualify_request(train(), facts(label_actor_permission="read"))
    assert result.status is Status.BLOCKED_POLICY
    assert result.reason_code == "label_actor_not_authorized"


def test_missing_fix_version_is_ineligible():
    result = qualify_request(train(), facts(jira_fix_versions=frozenset({"10.2"})))
    assert result.status is Status.INELIGIBLE_SOURCE
    assert result.reason_code == "jira_fix_version_mismatch"


def test_train_without_jira_requirement_does_not_require_fix_version():
    result = qualify_request(
        train(requirements=TrainRequirements(block_on_dependencies=True)),
        facts(jira_fix_versions=frozenset()),
    )
    assert result.status is Status.DRAFT_PLANNED


def test_missing_destination_or_missing_pr_rule_blocks_policy():
    missing = qualify_request(train(), facts(destination_exists=False))
    assert missing.status is Status.BLOCKED_POLICY
    assert missing.reason_code == "destination_branch_missing"

    no_pr_rule = qualify_request(train(), facts(destination_pr_required=False))
    assert no_pr_rule.status is Status.BLOCKED_POLICY
    assert no_pr_rule.reason_code == "destination_pull_request_rule_missing"


def test_unresolved_dependencies_block_before_git_planning():
    result = qualify_request(
        train(),
        facts(unresolved_dependencies=("ROCM-30000 blocks this change",)),
    )
    assert result.status is Status.BLOCKED_DEPENDENCY
    assert result.reason_code == "unresolved_dependencies"
    assert result.evidence["unresolved_dependencies"] == [
        "ROCM-30000 blocks this change"
    ]


def test_dependency_gate_can_be_disabled_per_train():
    result = qualify_request(
        train(
            requirements=TrainRequirements(
                jira_fix_version="10.1.0a20260811",
                block_on_dependencies=False,
            )
        ),
        facts(unresolved_dependencies=("ROCM-30000",)),
    )
    assert result.status is Status.DRAFT_PLANNED


def test_write_maintain_and_admin_permissions_are_authorized():
    for permission in ("write", "maintain", "admin"):
        assert (
            qualify_request(train(), facts(label_actor_permission=permission)).status
            is Status.DRAFT_PLANNED
        )
