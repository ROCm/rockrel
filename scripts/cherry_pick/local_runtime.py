# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Explicit local-operator runtime backed by GitHub CLI credentials."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping

from .clients import GitHubClient, UrlLibTransport
from .models import Result, Status
from .write_authority import DraftWriteAuthority, _local_authority

CONFIRMATION = "CREATE_DRAFT"


class LocalRuntimeError(RuntimeError):
    """Report local runtime validation or execution failures."""

    pass


def gh_github_client(
    environment: Mapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> GitHubClient:
    """Resolve github.com credentials through gh without mutating its state."""

    if environment.get("GITHUB_ACTIONS") == "true":
        raise LocalRuntimeError(
            "local gh authentication is unavailable in GitHub Actions"
        )
    if environment.get("GH_HOST", "github.com") != "github.com":
        raise LocalRuntimeError("local gh authentication supports github.com only")
    try:
        result = run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalRuntimeError("GitHub CLI credentials are unavailable") from exc
    token = result.stdout.strip() if result.returncode == 0 else ""
    if not token or any(character.isspace() for character in token):
        raise LocalRuntimeError("GitHub CLI credentials are unavailable")
    return GitHubClient(token, transport=UrlLibTransport())


def revalidate_local_write_authority(
    *,
    train_mode: str,
    expected: Result,
    current: Result,
    confirmation: str | None,
) -> DraftWriteAuthority:
    """Issue one local capability after literal consent and exact replanning."""

    if confirmation != CONFIRMATION:
        raise LocalRuntimeError(
            f"remote write confirmation must be the literal {CONFIRMATION}"
        )
    authorization = current.evidence.get("authorization")
    if isinstance(authorization, dict) and authorization.get("kind") == (
        "local_only_operator_request"
    ):
        raise LocalRuntimeError(
            "local-only materialization evidence cannot authorize remote writes"
        )
    if train_mode != "create-draft":
        raise LocalRuntimeError("train mode does not authorize draft creation")
    if (
        expected.status is not Status.DRAFT_PLANNED
        or current.status is not Status.DRAFT_PLANNED
    ):
        raise LocalRuntimeError("write-time revalidation did not produce draft_planned")
    identity_fields = (
        "source_pr",
        "source_repository",
        "train_id",
        "destination_branch",
    )
    if any(
        getattr(expected, field) != getattr(current, field) for field in identity_fields
    ):
        raise LocalRuntimeError("write-time revalidation changed request identity")
    evidence_fields = (
        "plan_fingerprint",
        "core_request_fingerprint",
        "destination_head",
        "planned_tree",
        "coverage_snapshot_sha256",
        "authorization",
    )
    if any(
        expected.evidence.get(field) != current.evidence.get(field)
        for field in evidence_fields
    ):
        raise LocalRuntimeError("write-time revalidation detected plan drift")
    plan_fingerprint = current.evidence.get("plan_fingerprint")
    if not isinstance(plan_fingerprint, str):
        raise LocalRuntimeError("plan fingerprint is unavailable")
    try:
        return _local_authority(plan_fingerprint)
    except ValueError as exc:
        raise LocalRuntimeError(str(exc)) from exc
