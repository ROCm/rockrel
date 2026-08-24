# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from copy import deepcopy
from datetime import datetime, timezone

import pytest
import urllib.error

from scripts.cherry_pick.release_hub import (
    MAX_RESPONSE_BYTES,
    ReleaseHubClient,
    ReleaseHubError,
    _NoRedirect,
    _urllib_transport,
    validate_api_origin,
)

TOKEN = "rrh1.abcdefghijkl." + "A" * 43
CONFIG_SHA = "a" * 64


def session_payload(*, scopes=None, expires_at="2026-09-18T00:00:00Z"):
    return {
        "requestId": "request-session",
        "data": {
            "tokenId": "00000000-0000-4000-8000-000000000001",
            "subjectIssuer": "https://issuer.example",
            "subjectId": "subject",
            "subjectEmail": "engineer@amd.com",
            "subjectName": "Engineer",
            "displayName": "ROCm Cherry-Pick CLI",
            "scopes": scopes or ["read:evidence"],
            "expiresAt": expires_at,
        },
    }


def train_payload():
    return {
        "requestId": "request-train",
        "data": {
            "source": {
                "schemaVersion": "release-trains.v5",
                "sha256": CONFIG_SHA,
                "loadedAt": "2026-08-19T12:00:00Z",
            },
            "validation": {"valid": True, "findings": []},
            "train": {
                "trainId": "10.1-20260811",
                "trainType": "express",
                "name": "ROCm 10.1 0811",
                "version": "10.1.0a20260811",
                "state": "enabled",
                "boundaryAt": "2026-08-23T00:00:00Z",
                "parentTrainId": None,
                "parentCrdAt": None,
                "planned": False,
                "branchReadiness": {
                    "status": "ready",
                    "label": "Ready",
                    "reason": "All release branches are confirmed.",
                },
                "operators": [],
                "milestones": [],
                "branches": [
                    branch("ROCm/TheRock", "main"),
                    branch("ROCm/rocm-systems", "release/bkc/therock-10.1-20260811"),
                    branch("ROCm/rocm-libraries", "release/bkc/therock-10.1-20260811"),
                ],
                "jiraCohorts": [],
                "targetNightly": None,
            },
        },
    }


def branch(repository, name):
    return {
        "repoFullName": repository,
        "branch": name,
        "purpose": "bkc_cherrypick",
        "plannedAt": "2026-08-11T00:00:00Z",
        "createdAt": "2026-08-11T01:00:00Z",
        "createdSha": "b" * 40,
        "resolutionStatus": "confirmed",
        "source": "github",
        "updatedAt": "2026-08-19T12:00:00Z",
    }


def test_api_origin_requires_https_except_explicit_loopback():
    assert validate_api_origin("https://developer-central.amd.com/") == (
        "https://developer-central.amd.com"
    )
    assert validate_api_origin("http://127.0.0.1:8081") == "http://127.0.0.1:8081"
    for value in (
        "http://developer-central.amd.com",
        "https://developer-central.amd.com/path",
        "https://user:secret@developer-central.amd.com",
        "file:///tmp/api",
    ):
        with pytest.raises(ReleaseHubError):
            validate_api_origin(value)


def test_client_validates_session_and_resolves_exact_confirmed_train():
    seen = []

    def transport(url, headers, timeout):
        seen.append((url, headers, timeout))
        return session_payload() if url.endswith("/auth/session") else train_payload()

    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=transport,
        now=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    session = client.session()
    snapshot = client.resolve_train("10.1-20260811", "ROCm/rocm-systems")

    assert session.display_name == "ROCm Cherry-Pick CLI"
    assert session.expires_within_days is None
    assert snapshot.destination_branch == "release/bkc/therock-10.1-20260811"
    assert snapshot.destination_created_sha == "b" * 40
    assert snapshot.configuration_sha256 == CONFIG_SHA
    assert snapshot.request_id == "request-train"
    assert {item.repository for item in snapshot.branches} == {
        "ROCm/TheRock",
        "ROCm/rocm-systems",
        "ROCm/rocm-libraries",
    }
    assert all(call[1]["Authorization"] == f"Bearer {TOKEN}" for call in seen)
    assert all("token" not in call[0].lower() for call in seen)
    assert TOKEN not in repr(client)


def test_client_rejects_malformed_token():
    with pytest.raises(ReleaseHubError, match="format"):
        ReleaseHubClient("https://developer-central.amd.com", "not-a-token")


def test_session_requires_read_scope_and_nonexpired_token_and_warns_near_expiry():
    now = lambda: datetime(2026, 8, 19, tzinfo=timezone.utc)
    for payload, message in (
        (session_payload(scopes=["read:operations"]), "read:evidence"),
        (session_payload(expires_at="2026-08-18T00:00:00Z"), "expired"),
    ):
        with pytest.raises(ReleaseHubError, match=message):
            ReleaseHubClient(
                "https://developer-central.amd.com",
                TOKEN,
                transport=lambda *_args: payload,
                now=now,
            ).session()

    near = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: session_payload(expires_at="2026-08-22T00:00:00Z"),
        now=now,
    ).session()
    assert near.expires_within_days == 3


@pytest.mark.parametrize(
    "payload,reason",
    [
        ([], "object"),
        ({"requestId": "request", "data": []}, "object"),
        (
            {**session_payload(), "requestId": None},
            "requestId",
        ),
        (
            {
                **session_payload(),
                "data": {**session_payload()["data"], "scopes": "read:evidence"},
            },
            "scopes",
        ),
        (
            {
                **session_payload(),
                "data": {**session_payload()["data"], "scopes": ["read:evidence", 1]},
            },
            "scopes",
        ),
        (
            {
                **session_payload(),
                "data": {**session_payload()["data"], "expiresAt": "not-a-time"},
            },
            "ISO-8601",
        ),
        (
            {
                **session_payload(),
                "data": {
                    **session_payload()["data"],
                    "expiresAt": "2026-09-18T00:00:00",
                },
            },
            "timezone",
        ),
    ],
)
def test_session_contract_fails_closed(payload, reason):
    with pytest.raises(ReleaseHubError, match=reason):
        ReleaseHubClient(
            "https://developer-central.amd.com",
            TOKEN,
            transport=lambda *_args: payload,
        ).session()


def test_session_accepts_naive_injected_clock_as_utc():
    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: session_payload(),
        now=lambda: datetime(2026, 8, 19),
    )
    assert client.session().expires_within_days is None


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda value: value["data"]["train"].update({"planned": True}), "planned"),
        (lambda value: value["data"]["train"].update({"state": "draft"}), "enabled"),
        (
            lambda value: value["data"]["train"]["branchReadiness"].update(
                {"status": "blocked"}
            ),
            "ready",
        ),
        (lambda value: value["data"]["validation"].update({"valid": False}), "invalid"),
        (
            lambda value: value["data"]["source"].update(
                {"schemaVersion": "release-trains.v3"}
            ),
            "schema",
        ),
        (
            lambda value: value["data"]["train"]["branches"][1].update(
                {"resolutionStatus": "provisional"}
            ),
            "confirmed",
        ),
        (
            lambda value: value["data"]["train"]["branches"].append(
                branch("ROCm/rocm-systems", "release/ambiguous")
            ),
            "exactly one",
        ),
    ],
)
def test_train_resolution_fails_closed_on_unready_or_ambiguous_data(mutate, reason):
    payload = deepcopy(train_payload())
    mutate(payload)
    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: payload,
    )
    with pytest.raises(ReleaseHubError, match=reason):
        client.resolve_train("10.1-20260811", "ROCm/rocm-systems")


@pytest.mark.parametrize(
    "train_id,repository,reason",
    [
        ("bad train id", "ROCm/rocm-systems", "train id"),
        ("10.1-20260811", "ROCm/unsupported", "not supported"),
    ],
)
def test_train_resolution_rejects_invalid_identity_without_transport(
    train_id, repository, reason
):
    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: pytest.fail("transport must not run"),
    )
    with pytest.raises(ReleaseHubError, match=reason):
        client.resolve_train(train_id, repository)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda value: value.update({"requestId": None}), "requestId"),
        (lambda value: value["data"]["source"].update({"sha256": "bad"}), "hash"),
        (lambda value: value["data"]["source"].update({"loadedAt": "bad"}), "ISO-8601"),
        (
            lambda value: value["data"]["train"].update({"trainId": "other"}),
            "different",
        ),
        (lambda value: value["data"]["train"].update({"trainType": "nightly"}), "type"),
        (lambda value: value["data"]["train"].update({"branches": {}}), "malformed"),
        (
            lambda value: value["data"]["train"]["branches"][1].update(
                {"createdAt": None}
            ),
            "not created",
        ),
        (
            lambda value: value["data"]["train"]["branches"][1].update(
                {"branch": "bad branch"}
            ),
            "branch name",
        ),
        (
            lambda value: value["data"]["train"]["branches"][1].update(
                {"createdSha": "bad"}
            ),
            "creation SHA",
        ),
        (
            lambda value: value["data"]["train"]["branches"][1].update(
                {"purpose": "development"}
            ),
            "exactly one",
        ),
    ],
)
def test_train_contract_rejects_malformed_fields(mutate, reason):
    payload = deepcopy(train_payload())
    mutate(payload)
    client = ReleaseHubClient(
        "https://developer-central.amd.com", TOKEN, transport=lambda *_args: payload
    )
    with pytest.raises(ReleaseHubError, match=reason):
        client.resolve_train("10.1-20260811", "ROCm/rocm-systems")


def test_train_ignores_nonrelease_and_unsupported_branch_records():
    payload = train_payload()
    payload["data"]["train"]["branches"].extend(
        [
            {**branch("ROCm/TheRock", "ignored"), "purpose": "development"},
            branch("someone/fork", "release/fork"),
        ]
    )
    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com", TOKEN, transport=lambda *_args: payload
    ).resolve_train("10.1-20260811", "ROCm/rocm-systems")
    assert len(snapshot.branches) == 3


def test_transport_failures_are_sanitized():
    def fail(*_args):
        raise OSError(f"network failure containing {TOKEN}")

    client = ReleaseHubClient(
        "https://developer-central.amd.com", TOKEN, transport=fail
    )
    with pytest.raises(ReleaseHubError) as caught:
        client.session()
    assert TOKEN not in str(caught.value)


def test_origin_validation_handles_ipv6_bad_ports_and_parse_errors():
    assert validate_api_origin("http://[::1]:8081") == "http://[::1]:8081"
    for value in ("https://developer-central.amd.com:bad", "https://["):
        with pytest.raises(ReleaseHubError):
            validate_api_origin(value)


class Response:
    def __init__(self, payload=b"{}", content_type="application/json"):
        self.payload = payload
        self.headers = self
        self.content_type = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get_content_type(self):
        return self.content_type

    def read(self, _limit):
        return self.payload


def test_default_transport_accepts_bounded_json_and_refuses_redirects(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET"
            assert timeout == 15
            return Response(b'{"ok":true}')

    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Opener())
    assert _urllib_transport(
        "https://example.com/data", {"Accept": "application/json"}, 15
    ) == {"ok": True}
    assert (
        _NoRedirect().redirect_request(None, None, 302, "", {}, "https://other") is None
    )


@pytest.mark.parametrize(
    "failure,reason",
    [
        (Response(b"{}", "text/html"), "non-JSON"),
        (Response(b"x" * (MAX_RESPONSE_BYTES + 1)), "too large"),
        (Response(b"not-json"), "malformed JSON"),
        (urllib.error.HTTPError("https://example", 403, "", {}, None), "HTTP 403"),
        (urllib.error.URLError("offline"), "unreachable"),
    ],
)
def test_default_transport_failures_are_classified(monkeypatch, failure, reason):
    class Opener:
        def open(self, *_args, **_kwargs):
            if isinstance(failure, Exception):
                raise failure
            return failure

    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Opener())
    with pytest.raises(ReleaseHubError, match=reason):
        _urllib_transport("https://example.com/data", {}, 15)
