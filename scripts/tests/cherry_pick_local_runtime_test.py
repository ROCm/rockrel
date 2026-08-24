# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import subprocess

import pytest

from scripts.cherry_pick.clients import UrlLibTransport
from scripts.cherry_pick.local_runtime import (
    LocalRuntimeError,
    gh_github_client,
    revalidate_local_write_authority,
)
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.write_authority import is_valid_authority


def completed(*, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        ["gh", "auth", "token", "--hostname", "github.com"],
        returncode,
        stdout=stdout,
        stderr=stderr,
    )


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


def test_gh_runtime_resolves_cli_token_without_exposing_it():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(stdout="gho_secret-token\n")

    client = gh_github_client({}, run=run)

    assert calls[0][0] == ["gh", "auth", "token", "--hostname", "github.com"]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["timeout"] == 30
    assert isinstance(client.transport, UrlLibTransport)
    assert client.token == "gho_secret-token"
    assert "gho_secret-token" not in repr(client)


@pytest.mark.parametrize(
    "environment,result",
    [
        ({"GITHUB_ACTIONS": "true"}, completed(stdout="gho_token\n")),
        ({}, completed(returncode=1, stderr="secret diagnostic")),
        ({}, completed(stdout="\n")),
        ({"GH_HOST": "example.com"}, completed(stdout="gho_token\n")),
    ],
)
def test_gh_runtime_fails_closed_and_sanitizes_cli_failures(environment, result):
    with pytest.raises(LocalRuntimeError) as caught:
        gh_github_client(environment, run=lambda *_args, **_kwargs: result)

    assert "secret diagnostic" not in str(caught.value)
    assert "gho_token" not in str(caught.value)


def test_gh_runtime_sanitizes_process_start_and_timeout_failures():
    for failure in (OSError("secret path"), subprocess.TimeoutExpired("gh", 30)):

        def fail(*_args, **_kwargs):
            raise failure

        with pytest.raises(LocalRuntimeError) as caught:
            gh_github_client({}, run=fail)
        assert "secret path" not in str(caught.value)


def test_local_write_authority_requires_literal_confirmation_and_exact_revalidation():
    expected = plan()
    current = plan()

    with pytest.raises(LocalRuntimeError, match="confirmation"):
        revalidate_local_write_authority(
            train_mode="create-draft",
            expected=expected,
            current=current,
            confirmation="yes",
        )

    authority = revalidate_local_write_authority(
        train_mode="create-draft",
        expected=expected,
        current=current,
        confirmation="CREATE_DRAFT",
    )
    assert is_valid_authority(authority, "a" * 64)


@pytest.mark.parametrize(
    "mode,current",
    [
        ("validate", plan()),
        ("create-draft", plan(fingerprint="f" * 64)),
        ("create-draft", plan(status=Status.BLOCKED_CONFLICT)),
        (
            "create-draft",
            Result(**{**plan().__dict__, "destination_branch": "release/moved"}),
        ),
    ],
)
def test_local_write_authority_rejects_disabled_mode_or_any_plan_drift(mode, current):
    with pytest.raises(LocalRuntimeError):
        revalidate_local_write_authority(
            train_mode=mode,
            expected=plan(),
            current=current,
            confirmation="CREATE_DRAFT",
        )


@pytest.mark.parametrize("fingerprint", [None, "short"])
def test_local_write_authority_rejects_missing_or_malformed_fingerprint(fingerprint):
    request = plan(fingerprint=fingerprint)
    with pytest.raises(LocalRuntimeError, match="fingerprint"):
        revalidate_local_write_authority(
            train_mode="create-draft",
            expected=request,
            current=request,
            confirmation="CREATE_DRAFT",
        )
