# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Explicit production-only construction boundary for trusted Actions jobs."""

from __future__ import annotations

from collections.abc import Mapping

from .clients import GitHubClient, UrlLibTransport
from .models import Result, Status
from .write_authority import DraftWriteAuthority, _action_authority


class ActionRuntimeError(RuntimeError):
    """Report action runtime validation or execution failures."""

    pass


def _token(environment: Mapping[str, str]) -> str:
    """Read the required Actions token without logging its value."""

    if environment.get("GITHUB_ACTIONS") != "true":
        raise ActionRuntimeError(
            "production transport is available only in GitHub Actions"
        )
    token = environment.get("GITHUB_TOKEN")
    if not token:
        raise ActionRuntimeError("the scoped GitHub Actions token is unavailable")
    return token


def action_github_client(environment: Mapping[str, str]) -> GitHubClient:
    """Create the narrow GitHub client used by the Actions runtime."""

    return GitHubClient(_token(environment), transport=UrlLibTransport())


def require_action_runtime(environment: Mapping[str, str]) -> None:
    """Validate the trusted job boundary without returning its credential."""

    _token(environment)


def revalidate_action_write_authority(
    environment: Mapping[str, str],
    *,
    train_mode: str,
    expected: Result,
    current: Result,
) -> DraftWriteAuthority:
    """Issue one plan-bound capability after exact write-time comparison."""

    _token(environment)
    if train_mode != "create-draft":
        raise ActionRuntimeError("train mode does not authorize draft creation")
    if (
        expected.status is not Status.DRAFT_PLANNED
        or current.status is not Status.DRAFT_PLANNED
    ):
        raise ActionRuntimeError(
            "write-time revalidation did not produce draft_planned"
        )
    identity_fields = (
        "source_pr",
        "source_repository",
        "train_id",
        "destination_branch",
    )
    if any(
        getattr(expected, field) != getattr(current, field) for field in identity_fields
    ):
        raise ActionRuntimeError("write-time revalidation changed request identity")
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
        raise ActionRuntimeError("write-time revalidation detected plan drift")
    plan_fingerprint = current.evidence.get("plan_fingerprint")
    if not isinstance(plan_fingerprint, str):
        raise ActionRuntimeError("plan fingerprint is unavailable")
    try:
        return _action_authority(plan_fingerprint)
    except ValueError as exc:
        raise ActionRuntimeError(str(exc)) from exc


def revalidate_action_frontier_authorities(
    environment: Mapping[str, str],
    *,
    train_mode: str,
    expected: Result,
    current: Result,
) -> dict[str, DraftWriteAuthority]:
    """Mint one plan-bound capability for each exact managed frontier item."""

    _token(environment)
    if train_mode != "create-draft":
        raise ActionRuntimeError("train mode does not authorize frontier drafts")
    for name, root in (("expected", expected), ("current", current)):
        if (
            root.status is not Status.AWAITING_DEPENDENCIES
            or root.reason_code != "managed_dependency_frontier"
        ):
            raise ActionRuntimeError(
                f"{name} revalidation result is not a managed dependency frontier"
            )
    identity_fields = (
        "source_pr",
        "source_repository",
        "train_id",
        "destination_branch",
    )
    if any(
        getattr(expected, field) != getattr(current, field) for field in identity_fields
    ):
        raise ActionRuntimeError("managed frontier revalidation changed root identity")
    if expected.as_dict() != current.as_dict():
        raise ActionRuntimeError("managed frontier revalidation detected drift")

    root_fingerprint = current.evidence.get("plan_fingerprint")
    raw_frontier = current.evidence.get("managed_frontier_results")
    if not isinstance(root_fingerprint, str):
        raise ActionRuntimeError("managed frontier root fingerprint is unavailable")
    if not isinstance(raw_frontier, list) or not raw_frontier:
        raise ActionRuntimeError("managed frontier result list is unavailable")

    authorities: dict[str, DraftWriteAuthority] = {}
    for index, raw in enumerate(raw_frontier):
        try:
            item = Result.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise ActionRuntimeError(
                f"managed frontier result {index} is invalid"
            ) from exc
        if item.status is not Status.DRAFT_PLANNED:
            raise ActionRuntimeError("managed frontier items must all be draft_planned")
        if (
            item.train_id != current.train_id
            or item.evidence.get("root_plan_fingerprint") != root_fingerprint
        ):
            raise ActionRuntimeError(
                "managed frontier item is not bound to the current root"
            )
        if item.source_pr is None or item.source_pr in authorities:
            raise ActionRuntimeError(
                "managed frontier contains a duplicate source identity"
            )
        fingerprint = item.evidence.get("plan_fingerprint")
        if not isinstance(fingerprint, str):
            raise ActionRuntimeError("managed frontier item fingerprint is unavailable")
        try:
            authorities[item.source_pr] = _action_authority(fingerprint)
        except ValueError as exc:
            raise ActionRuntimeError(str(exc)) from exc
    return authorities
