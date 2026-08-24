# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Shared immutable data models for cherry-pick automation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .compat import StrEnum


class Status(StrEnum):
    """Stable machine-readable planner and writer outcomes."""

    AWAITING_MERGE = "awaiting_merge"
    AWAITING_DEPENDENCIES = "awaiting_dependencies"
    INELIGIBLE_SOURCE = "ineligible_source"
    BLOCKED_EVIDENCE = "blocked_evidence"
    BLOCKED_AUTHORIZATION = "blocked_authorization"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    BLOCKED_AMBIGUOUS_CHANGESET = "blocked_ambiguous_changeset"
    BLOCKED_CONFLICT = "blocked_conflict"
    ALREADY_CONTAINED = "already_contained"
    COVERED_BY_EXISTING_PR = "covered_by_existing_pr"
    DRAFT_PLANNED = "draft_planned"
    DRAFT_CREATED = "draft_created"
    DRAFT_EXISTS = "draft_exists"
    RETRYABLE_PARTIAL_WRITE = "retryable_partial_write"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Result:
    """Machine-readable outcome emitted by every command.

    Git-only helpers may construct a result before request identity is known.
    Serialized artifacts, however, require the complete identity and are
    validated by :meth:`from_dict`.
    """

    status: Status
    reason_code: str
    message: str
    evidence: dict[str, object] = field(default_factory=dict)
    source_pr: str | None = None
    source_repository: str | None = None
    train_id: str | None = None
    destination_branch: str | None = None
    pull_request_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Serialize this result into its stable dictionary contract."""

        value: dict[str, object] = asdict(self)
        value["status"] = self.status.value
        return value

    @classmethod
    def from_dict(cls, value: object) -> "Result":
        """Validate and deserialize a trusted result artifact."""

        if not isinstance(value, dict):
            raise ValueError("result must be an object")
        fields = {
            "status",
            "reason_code",
            "message",
            "evidence",
            "source_pr",
            "source_repository",
            "train_id",
            "destination_branch",
            "pull_request_url",
        }
        unsupported = set(value) - fields
        if unsupported:
            raise ValueError(
                f"result contains unsupported field {sorted(unsupported)[0]}"
            )
        for key in (
            "reason_code",
            "message",
            "source_pr",
            "source_repository",
            "train_id",
            "destination_branch",
        ):
            if not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"result {key} must be a non-empty string")
        evidence = value.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("result evidence must be an object")
        pull_request_url = value.get("pull_request_url")
        if pull_request_url is not None and not isinstance(pull_request_url, str):
            raise ValueError("result pull_request_url must be a string or null")
        try:
            status = Status(value.get("status"))
        except (TypeError, ValueError) as exc:
            raise ValueError("result status is unknown") from exc
        return cls(
            status=status,
            reason_code=str(value["reason_code"]),
            message=str(value["message"]),
            evidence=dict(evidence),
            source_pr=str(value["source_pr"]),
            source_repository=str(value["source_repository"]),
            train_id=str(value["train_id"]),
            destination_branch=str(value["destination_branch"]),
            pull_request_url=pull_request_url,
        )
