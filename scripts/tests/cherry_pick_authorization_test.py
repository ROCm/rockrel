# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from dataclasses import replace

import pytest

from scripts.cherry_pick.authorization import (
    AuthorizationEnvelope,
    AuthorizationError,
    LabelTransition,
    authorized_plan_fingerprint,
    authorize_label,
    validate_authorization,
)


LABEL = "cherry-pick:train"
HEAD = "a" * 40
GRAPH = "b" * 64
CONFIG = "c" * 40


def transition(
    event_id,
    *,
    action="labeled",
    actor_id=7,
    actor_login="maintainer",
    app_id=None,
    created_at="2026-08-16T10:00:00Z",
):
    return LabelTransition(
        event_id=event_id,
        node_id=f"LE_{event_id}",
        label=LABEL,
        action=action,
        created_at=created_at,
        actor_id=actor_id,
        actor_login=actor_login,
        performed_via_app_id=app_id,
    )


def authorize(*, events=None, permissions=None, trusted=(123,), body="Body"):
    return authorize_label(
        train_id="train",
        label=LABEL,
        current_labels=(LABEL,),
        transitions=tuple((transition(1),) if events is None else events),
        actor_permissions={7: "write"} if permissions is None else permissions,
        minimum_human_permission="write",
        trusted_app_ids=trusted,
        source_head_sha=HEAD,
        source_body=body,
        dependency_snapshot_sha256=GRAPH,
        config_revision=CONFIG,
    )


@pytest.mark.parametrize("permission", ["write", "maintain", "admin"])
def test_authorizes_human_with_current_write_or_higher_permission(permission):
    envelope = authorize(permissions={7: permission})
    assert envelope.actor_id == 7
    assert envelope.actor_permission == permission
    assert envelope.performed_via_app_id is None
    assert len(envelope.fingerprint) == 64


def test_authorization_binds_the_developer_central_sha256_config_revision():
    revision = "d" * 64

    envelope = authorize_label(
        train_id="train",
        label=LABEL,
        current_labels=(LABEL,),
        transitions=(transition(1),),
        actor_permissions={7: "write"},
        minimum_human_permission="write",
        trusted_app_ids=(123,),
        source_head_sha=HEAD,
        source_body="Body",
        dependency_snapshot_sha256=GRAPH,
        config_revision=revision,
    )

    assert envelope.config_revision == revision
    assert AuthorizationEnvelope.from_dict(envelope.as_dict()) == envelope


@pytest.mark.parametrize("permission", ["none", "read", "triage"])
def test_rejects_human_below_write_permission(permission):
    with pytest.raises(AuthorizationError) as error:
        authorize(permissions={7: permission})
    assert error.value.reason_code == "label_actor_unauthorized"


def test_authorizes_only_exact_numeric_allowlisted_app_identity():
    envelope = authorize(
        events=[transition(1, actor_login="approved-labeler[bot]", app_id=123)],
        permissions={},
    )
    assert envelope.performed_via_app_id == 123
    assert envelope.actor_permission is None

    for app_id in (None, 122, 124):
        with pytest.raises(AuthorizationError) as error:
            authorize(
                events=[
                    transition(1, actor_login="approved-labeler[bot]", app_id=app_id)
                ],
                permissions={},
            )
        assert error.value.reason_code == "label_actor_unauthorized"


def test_bot_login_without_canonical_app_identity_is_never_a_human_authority():
    with pytest.raises(AuthorizationError) as error:
        authorize(
            events=[
                transition(
                    1,
                    actor_login="unattributed-automation[bot]",
                    app_id=None,
                )
            ],
            permissions={7: "admin"},
        )

    assert error.value.reason_code == "label_actor_unauthorized"


def test_uses_latest_matching_transition_regardless_of_input_order():
    older = transition(1, created_at="2026-08-16T10:00:00Z")
    removed = transition(2, action="unlabeled", created_at="2026-08-16T11:00:00Z")
    newest = transition(
        3,
        actor_id=8,
        actor_login="second",
        created_at="2026-08-16T12:00:00Z",
    )
    envelope = authorize(
        events=[newest, older, removed], permissions={7: "write", 8: "maintain"}
    )
    assert envelope.label_event_id == 3
    assert envelope.actor_id == 8


def test_current_label_and_latest_transition_must_agree():
    with pytest.raises(AuthorizationError) as removed:
        authorize(events=[transition(2, action="unlabeled")])
    assert removed.value.reason_code == "latest_label_transition_removed"

    with pytest.raises(AuthorizationError) as absent:
        authorize_label(
            train_id="train",
            label=LABEL,
            current_labels=(),
            transitions=(transition(1),),
            actor_permissions={7: "write"},
            minimum_human_permission="write",
            trusted_app_ids=(123,),
            source_head_sha=HEAD,
            source_body="Body",
            dependency_snapshot_sha256=GRAPH,
            config_revision=CONFIG,
        )
    assert absent.value.reason_code == "train_label_missing"


def test_missing_or_malformed_transition_evidence_fails_closed():
    with pytest.raises(AuthorizationError) as missing:
        authorize(events=[])
    assert missing.value.reason_code == "label_timeline_missing"

    with pytest.raises(ValueError, match="action"):
        replace(transition(1), action="renamed")

    with pytest.raises(ValueError, match="SHA"):
        authorize_label(
            train_id="train",
            label=LABEL,
            current_labels=(LABEL,),
            transitions=(transition(1),),
            actor_permissions={7: "write"},
            minimum_human_permission="write",
            trusted_app_ids=(123,),
            source_head_sha="short",
            source_body="Body",
            dependency_snapshot_sha256=GRAPH,
            config_revision=CONFIG,
        )


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"event_id": 0}, "event_id"),
        ({"event_id": True}, "event_id"),
        ({"node_id": ""}, "node_id"),
        ({"actor_id": 0}, "actor_id"),
        ({"actor_id": True}, "actor_id"),
        ({"performed_via_app_id": 0}, "performed_via_app_id"),
        ({"performed_via_app_id": True}, "performed_via_app_id"),
        ({"created_at": "not-a-time"}, "ISO-8601"),
    ],
)
def test_transition_rejects_invalid_canonical_identity(changes, message):
    with pytest.raises(ValueError, match=message):
        replace(transition(1), **changes)


def test_authorization_rejects_invalid_policy_and_graph_digest():
    with pytest.raises(ValueError, match="minimum_human_permission"):
        authorize_label(
            train_id="train",
            label=LABEL,
            current_labels=(LABEL,),
            transitions=(transition(1),),
            actor_permissions={7: "write"},
            minimum_human_permission="owner",
            trusted_app_ids=(),
            source_head_sha=HEAD,
            source_body="Body",
            dependency_snapshot_sha256=GRAPH,
            config_revision=CONFIG,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        authorize_label(
            train_id="train",
            label=LABEL,
            current_labels=(LABEL,),
            transitions=(transition(1),),
            actor_permissions={7: "write"},
            minimum_human_permission="write",
            trusted_app_ids=(),
            source_head_sha=HEAD,
            source_body="Body",
            dependency_snapshot_sha256="short",
            config_revision=CONFIG,
        )


def test_validation_returns_same_envelope_when_every_binding_is_fresh():
    envelope = authorize()
    assert (
        validate_authorization(
            envelope,
            source_head_sha=HEAD,
            source_body="Body",
            dependency_snapshot_sha256=GRAPH,
            config_revision=CONFIG,
        )
        is envelope
    )


def test_envelope_is_deterministic_and_binds_body_graph_head_and_config():
    first = authorize()
    second = authorize()
    assert first == second
    assert first.source_body_sha256 != GRAPH

    for kwargs, reason in (
        ({"source_head_sha": "d" * 40}, "authorization_source_changed"),
        ({"source_body": "Changed"}, "authorization_body_changed"),
        (
            {"dependency_snapshot_sha256": "e" * 64},
            "authorization_dependencies_changed",
        ),
        ({"config_revision": "f" * 40}, "authorization_config_changed"),
    ):
        with pytest.raises(AuthorizationError) as error:
            validate_authorization(
                first,
                source_head_sha=kwargs.get("source_head_sha", HEAD),
                source_body=kwargs.get("source_body", "Body"),
                dependency_snapshot_sha256=kwargs.get(
                    "dependency_snapshot_sha256", GRAPH
                ),
                config_revision=kwargs.get("config_revision", CONFIG),
            )
        assert error.value.reason_code == reason


def test_check_external_id_is_bounded_and_contains_no_login_or_body():
    envelope = authorize(body="secret-shaped source text")
    external_id = envelope.check_external_id()
    assert external_id.startswith("cherrypick:v2:train:1:")
    assert len(external_id) < 128
    assert "maintainer" not in external_id
    assert "secret-shaped" not in external_id


def test_authorized_plan_fingerprint_binds_core_request_and_authorization():
    envelope = authorize()
    request_digest = "d" * 64
    fingerprint = authorized_plan_fingerprint(request_digest, envelope)

    assert len(fingerprint) == 64
    assert fingerprint == authorized_plan_fingerprint(request_digest, envelope)
    assert fingerprint != authorized_plan_fingerprint("e" * 64, envelope)
    assert fingerprint != authorized_plan_fingerprint(
        request_digest, authorize(body="changed")
    )


def test_authorization_envelope_round_trip_is_strict_and_self_authenticating():
    envelope = authorize()
    assert AuthorizationEnvelope.from_dict(envelope.as_dict()) == envelope

    unknown = {**envelope.as_dict(), "unexpected": True}
    with pytest.raises(ValueError, match="unsupported"):
        AuthorizationEnvelope.from_dict(unknown)

    tampered = {**envelope.as_dict(), "actor_login": "someone-else"}
    with pytest.raises(ValueError, match="fingerprint"):
        AuthorizationEnvelope.from_dict(tampered)

    with pytest.raises(ValueError, match="SHA-256"):
        authorized_plan_fingerprint("short", envelope)


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"train_id": ""}, "train_id"),
        ({"label_event_id": True}, "label_event_id"),
        ({"performed_via_app_id": True}, "performed_via_app_id"),
        ({"actor_permission": "owner"}, "actor_permission"),
        ({"actor_permission": None}, "human authorization"),
        ({"performed_via_app_id": 123}, "App authorization"),
    ],
)
def test_authorization_envelope_rejects_invalid_typed_identity(changes, message):
    with pytest.raises(ValueError, match=message):
        replace(authorize(), **changes)


def test_authorization_envelope_parser_rejects_non_object_and_missing_field():
    with pytest.raises(ValueError, match="object"):
        AuthorizationEnvelope.from_dict([])

    missing = authorize().as_dict()
    del missing["actor_login"]
    with pytest.raises(ValueError, match="omitted"):
        AuthorizationEnvelope.from_dict(missing)
