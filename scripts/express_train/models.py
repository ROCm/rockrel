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

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Result":
        """Validate and deserialize a result artifact before publishing it."""

        if not isinstance(value, dict):
            raise ValueError("result must be an object")
        if any(
            not isinstance(value.get(key), str)
            for key in ("reason_code", "message")
        ):
            raise ValueError("result reason_code and message must be strings")
        source_pr = value.get("source_pr")
        train_id = value.get("train_id")
        if not isinstance(source_pr, str) or not isinstance(train_id, str):
            raise ValueError("result source_pr and train_id must be strings")
        evidence = value.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("result evidence must be an object")
        optional_strings = {}
        for key in ("target_branch", "pull_request_url"):
            item = value.get(key)
            if item is not None and not isinstance(item, str):
                raise ValueError(f"result {key} must be a string or null")
            optional_strings[key] = item
        try:
            status = Status(value.get("status"))
        except (TypeError, ValueError) as exc:
            raise ValueError("result status is unknown") from exc
        return cls(
            status=status,
            reason_code=value["reason_code"],
            message=value["message"],
            evidence=evidence,
            source_pr=source_pr,
            train_id=train_id,
            target_branch=optional_strings["target_branch"],
            pull_request_url=optional_strings["pull_request_url"],
        )
