"""Shared immutable data models for Express Train automation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    WAITING_FOR_MERGE = "waiting_for_merge"
    INVALID = "invalid"
    BLOCKED = "blocked"
    ALREADY_CONTAINED = "already_contained"
    COVERED_BY_EXISTING_PR = "covered_by_existing_pr"
    CHERRY_PICK_REQUIRED = "cherry_pick_required"
    DRAFT_CREATED = "draft_created"
    MANUAL_RESOLUTION_REQUIRED = "manual_resolution_required"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Result:
    """Machine-readable outcome emitted by every command."""

    status: Status
    reason_code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source_pr: str | None = None
    train_id: str | None = None
    target_branch: str | None = None
    pull_request_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value
