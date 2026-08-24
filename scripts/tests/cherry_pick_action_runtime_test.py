# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.cherry_pick.action_runtime import (
    ActionRuntimeError,
    action_github_client,
    require_action_runtime,
    revalidate_action_write_authority,
)
from scripts.cherry_pick.clients import UrlLibTransport
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.write_authority import DraftWriteAuthority, is_valid_authority


def test_production_github_transport_is_constructed_only_by_action_runtime():
    client = action_github_client(
        {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "short-lived-token"}
    )
    assert isinstance(client.transport, UrlLibTransport)
    assert client.token == "short-lived-token"

    for environment in ({}, {"GITHUB_TOKEN": "token"}, {"GITHUB_ACTIONS": "true"}):
        with pytest.raises(ActionRuntimeError):
            action_github_client(environment)


def test_action_boundary_can_be_required_without_exposing_the_token():
    environment = {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "short-lived-token"}
    assert require_action_runtime(environment) is None
    with pytest.raises(ActionRuntimeError):
        require_action_runtime({"GITHUB_TOKEN": "local-token"})


def plan(*, fingerprint="a" * 64, status=Status.DRAFT_PLANNED, **evidence):
    return Result(
        status=status,
        reason_code="clean_trial_application",
        message="clean",
        evidence={
            "plan_fingerprint": fingerprint,
            "core_request_fingerprint": "b" * 64,
            "destination_head": "c" * 40,
            "planned_tree": "d" * 40,
            "coverage_snapshot_sha256": "9" * 64,
            "authorization": {"fingerprint": "e" * 64},
            **evidence,
        },
        source_pr="https://github.com/ROCm/TheRock/pull/1",
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
    )


@pytest.mark.parametrize("mode", ["disabled", "validate", "shadow"])
def test_write_authority_requires_action_create_draft_mode(mode):
    with pytest.raises(ActionRuntimeError, match="mode"):
        revalidate_action_write_authority(
            {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
            train_mode=mode,
            expected=plan(),
            current=plan(),
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"status": Status.ALREADY_CONTAINED},
        {"fingerprint": "f" * 64},
        {"destination_head": "1" * 40},
        {"planned_tree": "2" * 40},
        {"core_request_fingerprint": "3" * 64},
        {"authorization": {"fingerprint": "4" * 64}},
        {"coverage_snapshot_sha256": "5" * 64},
    ],
)
def test_write_authority_rejects_any_write_time_plan_drift(changed):
    current = plan(
        fingerprint=changed.get("fingerprint", "a" * 64),
        status=changed.get("status", Status.DRAFT_PLANNED),
        **{
            key: value
            for key, value in changed.items()
            if key not in {"fingerprint", "status"}
        },
    )
    with pytest.raises(ActionRuntimeError, match="revalidation"):
        revalidate_action_write_authority(
            {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
            train_mode="create-draft",
            expected=plan(),
            current=current,
        )


def test_action_write_authority_is_opaque_and_scoped_to_one_plan():
    authority = revalidate_action_write_authority(
        {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        train_mode="create-draft",
        expected=plan(),
        current=plan(),
    )
    assert authority.plan_fingerprint == "a" * 64
    assert "write-token" not in repr(authority)

    assert is_valid_authority(authority, "a" * 64) is True
    assert is_valid_authority(authority, "b" * 64) is False
    assert is_valid_authority(DraftWriteAuthority("a" * 64, object())) is False


def test_action_write_authority_rejects_invalid_plan_fingerprint():
    with pytest.raises(ActionRuntimeError, match="fingerprint"):
        revalidate_action_write_authority(
            {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
            train_mode="create-draft",
            expected=plan(fingerprint="short"),
            current=plan(fingerprint="short"),
        )


def test_action_write_authority_rejects_identity_drift_and_missing_fingerprint():
    with pytest.raises(ActionRuntimeError, match="identity"):
        revalidate_action_write_authority(
            {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
            train_mode="create-draft",
            expected=plan(),
            current=replace(plan(), source_pr="https://github.com/ROCm/TheRock/pull/2"),
        )

    missing = plan(fingerprint=None)
    with pytest.raises(ActionRuntimeError, match="unavailable"):
        revalidate_action_write_authority(
            {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
            train_mode="create-draft",
            expected=missing,
            current=missing,
        )


def test_action_runtime_has_no_jira_or_release_hub_dependency():
    source = Path("scripts/cherry_pick/action_runtime.py").read_text().lower()
    assert "jira" not in source
    assert "release_hub" not in source
