# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Deterministic GitHub Check/comment projection for typed results."""

from __future__ import annotations

from .authorization import AuthorizationEnvelope
from .clients import GitHubClient, parse_pull_request_url
from .models import Result, Status
from .orchestrator import render_status_comment, status_marker


_SUCCESS = frozenset(
    {
        Status.ALREADY_CONTAINED,
        Status.COVERED_BY_EXISTING_PR,
        Status.DRAFT_CREATED,
        Status.DRAFT_EXISTS,
    }
)
_NEUTRAL = frozenset(
    {
        Status.AWAITING_MERGE,
        Status.AWAITING_DEPENDENCIES,
        Status.DRAFT_PLANNED,
    }
)


def check_conclusion(status: Status) -> str:
    """Map a planner status to its stable GitHub Check conclusion."""

    if status in _SUCCESS:
        return "success"
    if status in _NEUTRAL:
        return "neutral"
    if status is Status.CANCELLED:
        return "cancelled"
    return "action_required"


def publish_result_feedback(github: GitHubClient, result: Result) -> str | None:
    """Upsert the authorization-bound Check when possible and one comment."""

    if (
        result.source_pr is None
        or result.source_repository is None
        or result.train_id is None
    ):
        raise ValueError("result identity is incomplete")
    owner, repo, number = parse_pull_request_url(result.source_pr)
    if f"{owner}/{repo}" != result.source_repository:
        raise ValueError("result source repository does not match its URL")

    summary = render_status_comment(result)
    check_url: str | None = None
    raw_authorization = result.evidence.get("authorization")
    if raw_authorization is not None:
        envelope = AuthorizationEnvelope.from_dict(raw_authorization)
        source_head = result.evidence.get("source_head")
        if source_head is not None and source_head != envelope.source_head_sha:
            raise ValueError("result source head does not match authorization")
        check_url = github.upsert_check_run(
            owner,
            repo,
            head_sha=envelope.source_head_sha,
            name=f"ROCm Cherry-Pick / {result.train_id}",
            external_id=envelope.check_external_id(),
            conclusion=check_conclusion(result.status),
            title=f"{result.status.value}: {result.reason_code}",
            summary=summary,
        )
    github.upsert_comment(
        owner,
        repo,
        number,
        marker=status_marker(result.train_id),
        body=summary,
    )
    return check_url
