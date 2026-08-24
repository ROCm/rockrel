# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
import stat
from pathlib import Path

import pytest

from scripts.cherry_pick import control_plane as control_plane_module
from scripts.cherry_pick.release_hub import ReleaseHubError
from scripts.tests.cherry_pick_config_api_test import SOURCE_SHA, TOKEN, config_payload


OIDC_REQUEST_TOKEN = "request-token-secret"
OIDC_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJyZXBvOlJPQ20vcm9ja3JlbCJ9.signature"
AUDIENCE = "api://developer-central.amd.com/rocm-cherry-pick-config"


def functions():
    names = (
        "fetch_actions_oidc_token",
        "write_config_snapshot",
        "load_config_snapshot",
    )
    values = tuple(getattr(control_plane_module, name, None) for name in names)
    assert all(values), "the Actions control-plane adapter is incomplete"
    return values


def test_actions_oidc_request_is_exact_scoped_and_never_logs_credentials():
    fetch, _write, _load = functions()
    seen = {}

    def transport(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return {"value": OIDC_JWT}

    token = fetch(
        {
            "GITHUB_ACTIONS": "true",
            "ACTIONS_ID_TOKEN_REQUEST_URL": (
                "https://pipelines.actions.githubusercontent.com/token?job=123"
            ),
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_REQUEST_TOKEN,
        },
        audience=AUDIENCE,
        transport=transport,
    )

    assert token == OIDC_JWT
    assert seen["url"] == (
        "https://pipelines.actions.githubusercontent.com/token"
        "?job=123&audience=api%3A%2F%2Fdeveloper-central.amd.com"
        "%2Frocm-cherry-pick-config"
    )
    assert seen["headers"] == {
        "Accept": "application/json",
        "Authorization": f"Bearer {OIDC_REQUEST_TOKEN}",
        "User-Agent": "rocm-cherry-pick/1.0",
    }
    assert OIDC_REQUEST_TOKEN not in repr(control_plane_module)


@pytest.mark.parametrize(
    "environment,reason",
    [
        ({}, "GitHub Actions"),
        (
            {
                "GITHUB_ACTIONS": "true",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "http://example.com/token",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_REQUEST_TOKEN,
            },
            "HTTPS",
        ),
        (
            {
                "GITHUB_ACTIONS": "true",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.com/token",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_REQUEST_TOKEN,
            },
            "actions.githubusercontent.com",
        ),
        (
            {
                "GITHUB_ACTIONS": "true",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "",
            },
            "request token",
        ),
    ],
)
def test_actions_oidc_adapter_fails_closed_before_transport(environment, reason):
    fetch, _write, _load = functions()
    with pytest.raises(ReleaseHubError, match=reason):
        fetch(
            environment,
            audience=AUDIENCE,
            transport=lambda *_args: pytest.fail("transport must not run"),
        )


def test_actions_oidc_adapter_rejects_malformed_or_sanitized_responses():
    fetch, _write, _load = functions()
    environment = {
        "GITHUB_ACTIONS": "true",
        "ACTIONS_ID_TOKEN_REQUEST_URL": (
            "https://pipelines.actions.githubusercontent.com/token"
        ),
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_REQUEST_TOKEN,
    }
    for response, reason in (
        ({}, "value"),
        ({"value": "not-a-jwt"}, "JWT"),
        ([], "object"),
    ):
        with pytest.raises(ReleaseHubError, match=reason):
            fetch(
                environment,
                audience=AUDIENCE,
                transport=lambda *_args, value=response: value,
            )

    def fail(*_args):
        raise OSError("failure " + OIDC_REQUEST_TOKEN)

    with pytest.raises(ReleaseHubError) as caught:
        fetch(environment, audience=AUDIENCE, transport=fail)
    assert OIDC_REQUEST_TOKEN not in str(caught.value)


def test_config_snapshot_round_trip_is_private_complete_and_digest_bound(tmp_path):
    _fetch, write_snapshot, load_snapshot = functions()
    from scripts.cherry_pick.release_hub import ReleaseHubClient

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()
    path = tmp_path / "config.json"

    write_snapshot(path, snapshot)
    loaded = load_snapshot(path, expected_sha256=SOURCE_SHA)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert loaded.configuration_sha256 == SOURCE_SHA
    assert loaded.catalog.train("10.1-20260811").dependency_mode == "managed_stack"
    serialized = json.loads(path.read_text())
    assert serialized["schema_version"] == "release-hub-config-snapshot.v1"
    assert serialized["catalog"]["schema_version"] == 5
    assert "token" not in path.read_text().lower()


def test_config_snapshot_rejects_digest_drift_permissions_and_unknown_fields(tmp_path):
    _fetch, write_snapshot, load_snapshot = functions()
    from scripts.cherry_pick.release_hub import ReleaseHubClient

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()
    path = tmp_path / "config.json"
    write_snapshot(path, snapshot)

    with pytest.raises(ReleaseHubError, match="digest"):
        load_snapshot(path, expected_sha256="b" * 64)

    path.chmod(0o644)
    with pytest.raises(ReleaseHubError, match="permissions"):
        load_snapshot(path, expected_sha256=SOURCE_SHA)

    path.chmod(0o600)
    value = json.loads(path.read_text())
    value["unexpected"] = True
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(ReleaseHubError, match="unsupported"):
        load_snapshot(path, expected_sha256=SOURCE_SHA)


def oidc_environment(**overrides):
    environment = {
        "GITHUB_ACTIONS": "true",
        "ACTIONS_ID_TOKEN_REQUEST_URL": (
            "https://pipelines.actions.githubusercontent.com/token"
        ),
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_REQUEST_TOKEN,
    }
    environment.update(overrides)
    return environment


@pytest.mark.parametrize(
    "environment,audience,reason",
    [
        (
            oidc_environment(ACTIONS_ID_TOKEN_REQUEST_URL="https://["),
            AUDIENCE,
            "URL is invalid",
        ),
        (oidc_environment(), "", "audience is invalid"),
        (
            oidc_environment(
                ACTIONS_ID_TOKEN_REQUEST_URL=(
                    "https://pipelines.actions.githubusercontent.com/token"
                    "?audience=existing"
                )
            ),
            AUDIENCE,
            "already has an audience",
        ),
        (
            oidc_environment(ACTIONS_ID_TOKEN_REQUEST_TOKEN="x" * 16_385),
            AUDIENCE,
            "request token",
        ),
    ],
)
def test_actions_oidc_rejects_ambiguous_or_unbounded_requests(
    environment, audience, reason
):
    fetch, _write, _load = functions()
    with pytest.raises(ReleaseHubError, match=reason):
        fetch(
            environment,
            audience=audience,
            transport=lambda *_args: pytest.fail("transport must not run"),
        )


def test_actions_oidc_preserves_sanitized_release_hub_errors():
    fetch, _write, _load = functions()
    expected = ReleaseHubError("classified failure")

    with pytest.raises(ReleaseHubError) as caught:
        fetch(
            oidc_environment(),
            audience=AUDIENCE,
            transport=lambda *_args: (_ for _ in ()).throw(expected),
        )

    assert caught.value is expected


def test_action_config_fetch_composes_oidc_with_one_complete_config_request():
    seen = []

    snapshot = control_plane_module.fetch_action_config(
        "https://developer-central.amd.com",
        oidc_environment(),
        oidc_transport=lambda *_args: {"value": OIDC_JWT},
        release_hub_transport=lambda url, headers, timeout: (
            seen.append((url, headers, timeout)) or config_payload()
        ),
    )

    assert snapshot.configuration_sha256 == SOURCE_SHA
    assert [item[0] for item in seen] == [
        "https://developer-central.amd.com/api/v1/cherry-pick/config"
    ]
    assert seen[0][1]["Authorization"] == f"Bearer {OIDC_JWT}"


def test_snapshot_paths_fail_closed_without_following_links(tmp_path):
    _fetch, write_snapshot, load_snapshot = functions()
    from scripts.cherry_pick.release_hub import ReleaseHubClient

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()

    with pytest.raises(ReleaseHubError, match="absolute"):
        write_snapshot("relative.json", snapshot)
    with pytest.raises(ReleaseHubError, match="parent"):
        write_snapshot(tmp_path / "missing" / "snapshot.json", snapshot)
    link = tmp_path / "snapshot-link.json"
    link.symlink_to(tmp_path / "target.json")
    with pytest.raises(ReleaseHubError, match="symlink"):
        write_snapshot(link, snapshot)

    with pytest.raises(ReleaseHubError, match="unavailable"):
        load_snapshot(tmp_path / "missing.json")
    with pytest.raises(ReleaseHubError, match="regular file"):
        load_snapshot(tmp_path)
    with pytest.raises(ReleaseHubError, match="regular file"):
        load_snapshot(link)


def test_snapshot_loader_rejects_oversized_malformed_and_unbound_files(tmp_path):
    _fetch, _write, load_snapshot = functions()
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"")
    with path.open("r+b") as stream:
        stream.truncate(control_plane_module.MAX_SNAPSHOT_BYTES + 1)
    path.chmod(0o600)
    with pytest.raises(ReleaseHubError, match="too large"):
        load_snapshot(path)

    path.write_text("not-json")
    path.chmod(0o600)
    with pytest.raises(ReleaseHubError, match="malformed"):
        load_snapshot(path)

    from scripts.cherry_pick.release_hub import ReleaseHubClient

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()
    _write(path, snapshot)

    with pytest.raises(ReleaseHubError, match="digest is malformed"):
        load_snapshot(path, expected_sha256="bad")


def test_snapshot_writer_cleans_temporary_file_after_atomic_replace_failure(
    tmp_path, monkeypatch
):
    _fetch, write_snapshot, _load = functions()
    from scripts.cherry_pick.release_hub import ReleaseHubClient

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()
    secret = "filesystem-secret"

    def fail_replace(*_args):
        raise OSError(secret)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ReleaseHubError, match="could not be written") as caught:
        write_snapshot(tmp_path / "snapshot.json", snapshot)

    assert secret not in str(caught.value)
    assert list(tmp_path.glob(".cherry-pick-config-*")) == []
