# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from copy import deepcopy

import pytest

from scripts.cherry_pick import action_runtime
from scripts.cherry_pick.action_runtime import ActionRuntimeError
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.write_authority import is_valid_authority
from scripts.tests.cherry_pick_action_runtime_test import plan


ENVIRONMENT = {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"}


def root_with_frontier(*, frontier=None):
    item = plan(
        fingerprint="1" * 64,
        source_kind="pull_request",
        source_number=20,
        root_plan_fingerprint="a" * 64,
    )
    return Result(
        status=Status.AWAITING_DEPENDENCIES,
        reason_code="managed_dependency_frontier",
        message="frontier",
        evidence={
            "plan_fingerprint": "a" * 64,
            "core_request_fingerprint": "b" * 64,
            "authorization": {"fingerprint": "e" * 64},
            "managed_frontier_results": frontier or [item.as_dict()],
        },
        source_pr="https://github.com/ROCm/TheRock/pull/10",
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
    )


def revalidate(*args, **kwargs):
    function = getattr(
        action_runtime,
        "revalidate_action_frontier_authorities",
        None,
    )
    assert function is not None, "managed frontier authority is not implemented"
    return function(*args, **kwargs)


def test_action_runtime_mints_one_capability_per_exact_frontier_plan():
    expected = root_with_frontier()
    current = root_with_frontier()

    authorities = revalidate(
        ENVIRONMENT,
        train_mode="create-draft",
        expected=expected,
        current=current,
    )

    assert list(authorities) == ["https://github.com/ROCm/TheRock/pull/1"]
    assert is_valid_authority(
        authorities["https://github.com/ROCm/TheRock/pull/1"],
        "1" * 64,
    )
    assert "write-token" not in repr(authorities)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(status="draft_planned"),
        lambda value: value["evidence"].update(plan_fingerprint="f" * 64),
        lambda value: value["evidence"]["managed_frontier_results"][0][
            "evidence"
        ].update(planned_tree="f" * 40),
        lambda value: value["evidence"]["managed_frontier_results"][0].update(
            source_pr="https://github.com/ROCm/TheRock/pull/2"
        ),
        lambda value: value["evidence"].update(managed_frontier_results=[]),
    ],
)
def test_action_runtime_rejects_any_root_or_frontier_drift(mutate):
    expected = root_with_frontier()
    current_value = deepcopy(expected.as_dict())
    mutate(current_value)
    current = Result.from_dict(current_value)

    with pytest.raises(ActionRuntimeError, match="frontier|revalidation|drift"):
        revalidate(
            ENVIRONMENT,
            train_mode="create-draft",
            expected=expected,
            current=current,
        )


def test_action_runtime_rejects_duplicates_nonplanned_items_and_wrong_mode():
    duplicate = plan(
        fingerprint="2" * 64,
        source_kind="pull_request",
        source_number=1,
        root_plan_fingerprint="a" * 64,
    )
    expected = root_with_frontier(
        frontier=[
            plan(
                fingerprint="1" * 64,
                source_kind="pull_request",
                source_number=1,
                root_plan_fingerprint="a" * 64,
            ).as_dict(),
            duplicate.as_dict(),
        ]
    )
    with pytest.raises(ActionRuntimeError, match="duplicate"):
        revalidate(
            ENVIRONMENT,
            train_mode="create-draft",
            expected=expected,
            current=expected,
        )

    blocked = root_with_frontier(
        frontier=[plan(status=Status.BLOCKED_CONFLICT).as_dict()]
    )
    with pytest.raises(ActionRuntimeError, match="draft_planned"):
        revalidate(
            ENVIRONMENT,
            train_mode="create-draft",
            expected=blocked,
            current=blocked,
        )

    with pytest.raises(ActionRuntimeError, match="mode"):
        revalidate(
            ENVIRONMENT,
            train_mode="shadow",
            expected=root_with_frontier(),
            current=root_with_frontier(),
        )


def test_action_runtime_rejects_managed_frontier_root_identity_drift():
    expected = root_with_frontier()
    current_value = deepcopy(expected.as_dict())
    current_value["source_repository"] = "ROCm/rocm-systems"
    current = Result.from_dict(current_value)

    with pytest.raises(ActionRuntimeError, match="root identity"):
        revalidate(
            ENVIRONMENT,
            train_mode="create-draft",
            expected=expected,
            current=current,
        )


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (
            lambda value: value.evidence.update(plan_fingerprint=None),
            "root fingerprint",
        ),
        (
            lambda value: value.evidence.update(managed_frontier_results=[]),
            "result list",
        ),
        (
            lambda value: value.evidence.update(managed_frontier_results=[None]),
            "result 0 is invalid",
        ),
        (
            lambda value: value.evidence["managed_frontier_results"][0][
                "evidence"
            ].update(root_plan_fingerprint="c" * 64),
            "not bound",
        ),
        (
            lambda value: value.evidence["managed_frontier_results"][0][
                "evidence"
            ].update(plan_fingerprint=None),
            "fingerprint is unavailable",
        ),
        (
            lambda value: value.evidence["managed_frontier_results"][0][
                "evidence"
            ].update(plan_fingerprint="short"),
            "fingerprint",
        ),
    ],
)
def test_action_runtime_rejects_malformed_managed_frontier_authority_inputs(
    mutate, reason
):
    expected = root_with_frontier()
    mutate(expected)

    with pytest.raises(ActionRuntimeError, match=reason):
        revalidate(
            ENVIRONMENT,
            train_mode="create-draft",
            expected=expected,
            current=expected,
        )


def test_action_runtime_rejects_frontier_result_with_missing_source_identity():
    expected = root_with_frontier()
    expected.evidence["managed_frontier_results"][0]["source_pr"] = None

    with pytest.raises(ActionRuntimeError, match="result 0 is invalid"):
        revalidate(
            ENVIRONMENT,
            train_mode="create-draft",
            expected=expected,
            current=expected,
        )
