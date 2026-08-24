# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import pytest

from scripts.cherry_pick.models import Result, Status

EXPECTED_STATUSES = {
    "awaiting_merge",
    "awaiting_dependencies",
    "ineligible_source",
    "blocked_evidence",
    "blocked_authorization",
    "blocked_dependency",
    "blocked_ambiguous_changeset",
    "blocked_conflict",
    "already_contained",
    "covered_by_existing_pr",
    "draft_planned",
    "draft_created",
    "draft_exists",
    "retryable_partial_write",
    "cancelled",
}


def result():
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        source_pr="https://github.com/ROCm/TheRock/pull/1",
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
        evidence={"ordered_commits": ["a" * 40], "mainline": None},
    )


def test_status_contract_contains_only_documented_outcomes():
    assert {status.value for status in Status} == EXPECTED_STATUSES


def test_result_round_trip_preserves_required_identity():
    expected = result()
    actual = Result.from_dict(expected.as_dict())
    assert actual == expected
    assert actual.source_repository == "ROCm/TheRock"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_pr", None),
        ("source_repository", None),
        ("train_id", None),
        ("destination_branch", None),
        ("evidence", []),
        ("reason_code", 1),
    ],
)
def test_result_rejects_missing_or_malformed_required_fields(field, value):
    payload = result().as_dict()
    payload[field] = value
    with pytest.raises(ValueError, match=field):
        Result.from_dict(payload)


def test_result_rejects_non_object_unknown_status_and_invalid_optional_url():
    with pytest.raises(ValueError, match="result must be an object"):
        Result.from_dict([])

    payload = result().as_dict()
    payload["status"] = "not-a-status"
    with pytest.raises(ValueError, match="status is unknown"):
        Result.from_dict(payload)

    payload = result().as_dict()
    payload["pull_request_url"] = 7
    with pytest.raises(ValueError, match="pull_request_url"):
        Result.from_dict(payload)

    payload = {**result().as_dict(), "unexpected": True}
    with pytest.raises(ValueError, match="unsupported"):
        Result.from_dict(payload)
