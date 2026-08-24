# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Pure authorization of already-fetched GitHub label evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping, Sequence


SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
PERMISSION_ORDER = {
    "none": 0,
    "read": 1,
    "triage": 2,
    "write": 3,
    "maintain": 4,
    "admin": 5,
}


class AuthorizationError(ValueError):
    """Report authorization validation or execution failures."""

    def __init__(self, reason_code: str, message: str) -> None:
        """Initialize an authorization failure with stable machine-readable evidence."""

        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LabelTransition:
    """Represent one auditable pull request label transition."""

    event_id: int
    node_id: str
    label: str
    action: str
    created_at: str
    actor_id: int
    actor_login: str
    performed_via_app_id: int | None = None

    def __post_init__(self) -> None:
        """Validate label transition invariants after dataclass initialization."""

        if isinstance(self.event_id, bool) or self.event_id < 1:
            raise ValueError("event_id must be a positive integer")
        if self.action not in {"labeled", "unlabeled"}:
            raise ValueError("action must be labeled or unlabeled")
        for name in ("node_id", "label", "created_at", "actor_login"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        if isinstance(self.actor_id, bool) or self.actor_id < 1:
            raise ValueError("actor_id must be a positive integer")
        if self.performed_via_app_id is not None and (
            isinstance(self.performed_via_app_id, bool) or self.performed_via_app_id < 1
        ):
            raise ValueError("performed_via_app_id must be a positive integer")
        _parse_time(self.created_at)


def latest_label_transition(
    label: str, transitions: Sequence[LabelTransition]
) -> LabelTransition | None:
    """Select the one transition that can authorize the current request."""

    matches = (item for item in transitions if item.label == label)
    return max(
        matches,
        key=lambda item: (_parse_time(item.created_at), item.event_id),
        default=None,
    )


@dataclass(frozen=True)
class AuthorizationEnvelope:
    """Capture durable label-time authority over exact pull request state."""

    train_id: str
    label: str
    label_event_id: int
    label_event_node_id: str
    labeled_at: str
    actor_id: int
    actor_login: str
    actor_permission: str | None
    performed_via_app_id: int | None
    source_head_sha: str
    source_body_sha256: str
    dependency_snapshot_sha256: str
    config_revision: str
    fingerprint: str

    def __post_init__(self) -> None:
        """Validate authorization envelope invariants after dataclass initialization."""

        for name in (
            "train_id",
            "label",
            "label_event_node_id",
            "labeled_at",
            "actor_login",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("label_event_id", "actor_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.performed_via_app_id is not None and (
            isinstance(self.performed_via_app_id, bool)
            or not isinstance(self.performed_via_app_id, int)
            or self.performed_via_app_id < 1
        ):
            raise ValueError("performed_via_app_id must be a positive integer")
        if (
            self.actor_permission is not None
            and self.actor_permission not in PERMISSION_ORDER
        ):
            raise ValueError("actor_permission is unknown")
        if self.performed_via_app_id is None and self.actor_permission is None:
            raise ValueError("human authorization must record actor_permission")
        if self.performed_via_app_id is not None and self.actor_permission is not None:
            raise ValueError("App authorization must not record actor_permission")
        _parse_time(self.labeled_at)
        _require_sha(self.source_head_sha, "source_head_sha")
        _require_digest(self.source_body_sha256, "source_body_sha256")
        _require_digest(self.dependency_snapshot_sha256, "dependency_snapshot_sha256")
        _require_config_revision(self.config_revision)
        _require_digest(self.fingerprint, "fingerprint")

    @classmethod
    def from_dict(cls, value: object) -> "AuthorizationEnvelope":
        """Parse and validate a authorization envelope from its dictionary contract."""

        if not isinstance(value, dict):
            raise ValueError("authorization envelope must be an object")
        fields = {
            "train_id",
            "label",
            "label_event_id",
            "label_event_node_id",
            "labeled_at",
            "actor_id",
            "actor_login",
            "actor_permission",
            "performed_via_app_id",
            "source_head_sha",
            "source_body_sha256",
            "dependency_snapshot_sha256",
            "config_revision",
            "fingerprint",
        }
        unsupported = set(value) - fields
        if unsupported:
            raise ValueError(
                f"authorization envelope contains unsupported field {sorted(unsupported)[0]}"
            )
        missing = fields - set(value)
        if missing:
            raise ValueError(
                f"authorization envelope omitted field {sorted(missing)[0]}"
            )
        envelope = cls(**{name: value[name] for name in fields})
        signed_fields = {name: value[name] for name in fields if name != "fingerprint"}
        if envelope.fingerprint != _fingerprint(signed_fields):
            raise ValueError("authorization envelope fingerprint is invalid")
        return envelope

    def as_dict(self) -> dict[str, object]:
        """Serialize this authorization envelope into its stable dictionary contract."""

        return asdict(self)

    def check_external_id(self) -> str:
        """Return the durable executor Check external identifier."""

        return (
            f"cherrypick:v2:{self.train_id}:{self.label_event_id}:"
            f"{self.fingerprint}"
        )


def authorized_plan_fingerprint(
    core_request_sha256: str,
    envelope: AuthorizationEnvelope | object,
) -> str:
    """Bind one offline request to its exact control-plane authorization."""

    _require_digest(core_request_sha256, "core_request_sha256")
    parsed = (
        envelope
        if isinstance(envelope, AuthorizationEnvelope)
        else AuthorizationEnvelope.from_dict(envelope)
    )
    return _fingerprint(
        {
            "schema_version": 2,
            "core_request_sha256": core_request_sha256,
            "authorization_fingerprint": parsed.fingerprint,
        }
    )


def _parse_time(value: str) -> datetime:
    """Parse one required UTC timestamp from authorization evidence."""

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 timestamp") from exc


def _require_sha(value: str, name: str) -> None:
    """Require a full lowercase Git commit SHA."""

    if SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase Git SHA")


def _require_config_revision(value: str) -> None:
    """Require a valid immutable configuration revision."""

    if SHA_RE.fullmatch(value) is None and DIGEST_RE.fullmatch(value) is None:
        raise ValueError(
            "config_revision must be a lowercase Git SHA or SHA-256 digest"
        )


def _require_digest(value: str, name: str) -> None:
    """Require a full lowercase SHA-256 evidence digest."""

    if DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _body_digest(body: str) -> str:
    """Compute the canonical digest of the pull request body."""

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _fingerprint(fields: dict[str, object]) -> str:
    """Compute the canonical authorization-envelope fingerprint."""

    payload = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def authorize_label(
    *,
    train_id: str,
    label: str,
    current_labels: Sequence[str],
    transitions: Sequence[LabelTransition],
    actor_permissions: Mapping[int, str],
    minimum_human_permission: str,
    trusted_app_ids: Sequence[int],
    source_head_sha: str,
    source_body: str,
    dependency_snapshot_sha256: str,
    config_revision: str,
) -> AuthorizationEnvelope:
    """Authorize a label transition against current immutable pull request state."""

    _require_sha(source_head_sha, "source_head_sha")
    _require_config_revision(config_revision)
    _require_digest(dependency_snapshot_sha256, "dependency_snapshot_sha256")
    if minimum_human_permission not in PERMISSION_ORDER:
        raise ValueError("minimum_human_permission is unknown")
    if label not in current_labels:
        raise AuthorizationError("train_label_missing", "The train label is absent.")
    latest = latest_label_transition(label, transitions)
    if latest is None:
        raise AuthorizationError(
            "label_timeline_missing", "No canonical label transition is available."
        )
    if latest.action != "labeled":
        raise AuthorizationError(
            "latest_label_transition_removed",
            "The latest train-label transition removed the request.",
        )

    permission: str | None = None
    if latest.performed_via_app_id is not None:
        if latest.performed_via_app_id not in trusted_app_ids:
            raise AuthorizationError(
                "label_actor_unauthorized", "The labeling App is not allowlisted."
            )
    else:
        if latest.actor_login.casefold().endswith("[bot]"):
            raise AuthorizationError(
                "label_actor_unauthorized",
                "A bot label event must include canonical GitHub App identity.",
            )
        permission = actor_permissions.get(latest.actor_id, "none")
        if permission not in PERMISSION_ORDER or (
            PERMISSION_ORDER[permission] < PERMISSION_ORDER[minimum_human_permission]
        ):
            raise AuthorizationError(
                "label_actor_unauthorized",
                "The label actor does not have current write permission.",
            )

    fields: dict[str, object] = {
        "train_id": train_id,
        "label": label,
        "label_event_id": latest.event_id,
        "label_event_node_id": latest.node_id,
        "labeled_at": latest.created_at,
        "actor_id": latest.actor_id,
        "actor_login": latest.actor_login,
        "actor_permission": permission,
        "performed_via_app_id": latest.performed_via_app_id,
        "source_head_sha": source_head_sha,
        "source_body_sha256": _body_digest(source_body),
        "dependency_snapshot_sha256": dependency_snapshot_sha256,
        "config_revision": config_revision,
    }
    return AuthorizationEnvelope(**fields, fingerprint=_fingerprint(fields))


def validate_authorization(
    envelope: AuthorizationEnvelope,
    *,
    source_head_sha: str,
    source_body: str,
    dependency_snapshot_sha256: str,
    config_revision: str,
) -> AuthorizationEnvelope:
    """Revalidate a stored envelope against current immutable state."""

    comparisons = (
        (
            source_head_sha,
            envelope.source_head_sha,
            "authorization_source_changed",
        ),
        (
            _body_digest(source_body),
            envelope.source_body_sha256,
            "authorization_body_changed",
        ),
        (
            dependency_snapshot_sha256,
            envelope.dependency_snapshot_sha256,
            "authorization_dependencies_changed",
        ),
        (
            config_revision,
            envelope.config_revision,
            "authorization_config_changed",
        ),
    )
    for actual, expected, reason in comparisons:
        if actual != expected:
            raise AuthorizationError(reason, "Authorization evidence is stale.")
    return envelope
