# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from unittest.mock import Mock

import pytest

from scripts.cherry_pick.authorization import LabelTransition, authorize_label
from scripts.cherry_pick.feedback import check_conclusion, publish_result_feedback
from scripts.cherry_pick.models import Result, Status


def result(status=Status.DRAFT_PLANNED, *, authorized=True):
    evidence = {"source_head": "a" * 40}
    if authorized:
        evidence["authorization"] = authorize_label(
            train_id="train",
            label="cherry-pick:train",
            current_labels=("cherry-pick:train",),
            transitions=(
                LabelTransition(
                    event_id=7,
                    node_id="LE_7",
                    label="cherry-pick:train",
                    action="labeled",
                    created_at="2026-08-16T10:00:00Z",
                    actor_id=9,
                    actor_login="operator",
                ),
            ),
            actor_permissions={9: "write"},
            minimum_human_permission="write",
            trusted_app_ids=(),
            source_head_sha="a" * 40,
            source_body="body",
            dependency_snapshot_sha256="b" * 64,
            config_revision="c" * 40,
        ).as_dict()
    return Result(
        status=status,
        reason_code="reason",
        message="message",
        evidence=evidence,
        source_pr="https://github.com/ROCm/TheRock/pull/1",
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        (Status.ALREADY_CONTAINED, "success"),
        (Status.COVERED_BY_EXISTING_PR, "success"),
        (Status.DRAFT_CREATED, "success"),
        (Status.DRAFT_EXISTS, "success"),
        (Status.AWAITING_MERGE, "neutral"),
        (Status.AWAITING_DEPENDENCIES, "neutral"),
        (Status.DRAFT_PLANNED, "neutral"),
        (Status.CANCELLED, "cancelled"),
        (Status.BLOCKED_CONFLICT, "action_required"),
        (Status.BLOCKED_AUTHORIZATION, "action_required"),
        (Status.RETRYABLE_PARTIAL_WRITE, "action_required"),
    ],
)
def test_check_conclusion_is_total_for_operational_result_families(status, expected):
    assert check_conclusion(status) == expected


def test_feedback_upserts_authorization_bound_check_and_one_sticky_comment():
    github = Mock()
    github.upsert_check_run.return_value = "https://github.com/checks/1"
    planned = result()

    check_url = publish_result_feedback(github, planned)

    assert check_url == "https://github.com/checks/1"
    check = github.upsert_check_run.call_args.kwargs
    assert check["head_sha"] == "a" * 40
    assert check["name"] == "ROCm Cherry-Pick / train"
    assert check["external_id"].startswith("cherrypick:v2:train:7:")
    assert check["conclusion"] == "neutral"
    github.upsert_comment.assert_called_once()
    assert github.upsert_comment.call_args.kwargs["marker"] == (
        "<!-- cherry-pick-status:train -->"
    )


def test_feedback_without_authorization_never_invents_a_check_identity():
    github = Mock()

    assert publish_result_feedback(github, result(authorized=False)) is None

    github.upsert_check_run.assert_not_called()
    github.upsert_comment.assert_called_once()


def test_feedback_rejects_tampered_authorization_or_source_identity():
    github = Mock()
    tampered = result()
    tampered.evidence["authorization"]["actor_login"] = "tampered"
    with pytest.raises(ValueError, match="fingerprint"):
        publish_result_feedback(github, tampered)

    mismatched = result()
    mismatched.evidence["source_head"] = "f" * 40
    with pytest.raises(ValueError, match="source head"):
        publish_result_feedback(github, mismatched)


def test_feedback_rejects_incomplete_or_url_mismatched_result_identity():
    github = Mock()
    incomplete = result(authorized=False)
    incomplete = Result(**{**incomplete.__dict__, "train_id": None})
    with pytest.raises(ValueError, match="incomplete"):
        publish_result_feedback(github, incomplete)

    mismatched = result(authorized=False)
    mismatched = Result(
        **{**mismatched.__dict__, "source_repository": "ROCm/rocm-systems"}
    )
    with pytest.raises(ValueError, match="does not match"):
        publish_result_feedback(github, mismatched)

    github.upsert_check_run.assert_not_called()
    github.upsert_comment.assert_not_called()
