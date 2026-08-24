# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os

import pytest

from scripts.cherry_pick.release_hub_auth import (
    DEVELOPER_CENTRAL_TOKEN_URL,
    CredentialError,
    default_credential_path,
    load_credential,
    read_token_file,
    remove_credential,
    save_credential,
)

API = "https://developer-central.amd.com"
TOKEN = "rrh1.abcdefghijkl." + "A" * 43


def test_environment_token_has_precedence_without_being_exposed(tmp_path):
    path = tmp_path / "auth.json"
    save_credential(path, API, "rrh1.mnopqrstuvwx." + "B" * 43)
    credential = load_credential(
        api_origin=API,
        path=path,
        environ={"ROCM_RELEASE_HUB_TOKEN": TOKEN},
    )
    assert credential.token == TOKEN
    assert credential.source == "environment"
    assert TOKEN not in repr(credential)


def test_default_credential_path_honors_explicit_and_xdg_locations(tmp_path):
    assert (
        default_credential_path({"ROCM_CHERRY_PICK_AUTH_FILE": str(tmp_path / "one")})
        == tmp_path / "one"
    )
    assert (
        default_credential_path({"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
        == tmp_path / "xdg/rocm-cherry-pick/auth.json"
    )
    assert default_credential_path({}).name == "auth.json"


def test_private_versioned_credential_store_round_trips_and_logout_is_scoped(tmp_path):
    path = tmp_path / "nested" / "auth.json"
    save_credential(path, API, TOKEN)
    save_credential(path, "http://127.0.0.1:8081", "rrh1.mnopqrstuvwx." + "B" * 43)

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["schema_version"] == "rrh-auth.v1"
    loaded = load_credential(api_origin=API, path=path, environ={})
    assert loaded.token == TOKEN
    assert loaded.source == "credential_file"

    assert remove_credential(path, API) is True
    assert load_credential(
        api_origin="http://127.0.0.1:8081", path=path, environ={}
    ).token.startswith("rrh1.mnopqrstuvwx")
    assert remove_credential(path, API) is False


def test_removing_last_credential_removes_store(tmp_path):
    path = tmp_path / "auth.json"
    save_credential(path, API, TOKEN)
    assert remove_credential(path, API) is True
    assert not path.exists()


def test_private_token_input_file_is_bounded(tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n")
    path.chmod(0o600)
    assert read_token_file(path) == TOKEN
    path.write_text("x" * 1025)
    with pytest.raises(CredentialError, match="too large"):
        read_token_file(path)


def test_missing_credential_points_directly_to_developer_central(tmp_path):
    with pytest.raises(CredentialError) as caught:
        load_credential(api_origin=API, path=tmp_path / "missing.json", environ={})
    message = str(caught.value)
    assert DEVELOPER_CENTRAL_TOKEN_URL in message
    assert "ROCm Cherry-Pick CLI" in message
    assert "read:evidence" in message
    assert (
        "python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth login"
        in message
    )
    assert "replace /path/to/rocm-cherry-pick" in message


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_credential_store_rejects_group_or_world_access(tmp_path, mode):
    path = tmp_path / "auth.json"
    save_credential(path, API, TOKEN)
    path.chmod(mode)
    with pytest.raises(CredentialError, match="permissions"):
        load_credential(api_origin=API, path=path, environ={})


def test_credential_store_rejects_symlinks_and_malformed_tokens(tmp_path):
    target = tmp_path / "target.json"
    save_credential(target, API, TOKEN)
    link = tmp_path / "auth.json"
    link.symlink_to(target)
    with pytest.raises(CredentialError, match="symbolic"):
        load_credential(api_origin=API, path=link, environ={})

    for malformed in ("token", "rrh1.short.value", TOKEN + "x"):
        with pytest.raises(CredentialError, match="format"):
            save_credential(tmp_path / f"bad-{len(malformed)}", API, malformed)


@pytest.mark.parametrize(
    "payload,reason",
    [
        ("not-json", "malformed"),
        ("[]", "fields"),
        (json.dumps({"schema_version": "old", "credentials": {}}), "schema"),
        (
            json.dumps({"schema_version": "rrh-auth.v1", "credentials": []}),
            "credentials",
        ),
        (
            json.dumps(
                {
                    "schema_version": "rrh-auth.v1",
                    "credentials": {API: {"token": TOKEN, "extra": True}},
                }
            ),
            "invalid entry",
        ),
    ],
)
def test_credential_store_rejects_malformed_contracts(tmp_path, payload, reason):
    path = tmp_path / "auth.json"
    path.write_text(payload)
    path.chmod(0o600)
    with pytest.raises(CredentialError, match=reason):
        load_credential(api_origin=API, path=path, environ={})


def test_credential_paths_reject_nonfiles_and_insecure_or_symlinked_parents(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(CredentialError, match="regular file"):
        load_credential(api_origin=API, path=directory, environ={})

    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    with pytest.raises(CredentialError, match="directory permissions"):
        save_credential(insecure / "auth.json", API, TOKEN)

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(CredentialError, match="real directory"):
        save_credential(linked / "auth.json", API, TOKEN)


def test_oversized_store_is_rejected(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(" " * (64 * 1024 + 1))
    path.chmod(0o600)
    with pytest.raises(CredentialError, match="too large"):
        load_credential(api_origin=API, path=path, environ={})


def test_atomic_replacement_preserves_private_mode(tmp_path):
    path = tmp_path / "auth.json"
    save_credential(path, API, TOKEN)
    os.chmod(path, 0o600)
    replacement = "rrh1.mnopqrstuvwx." + "C" * 43
    save_credential(path, API, replacement)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_credential(api_origin=API, path=path, environ={}).token == replacement


def test_atomic_write_failure_is_sanitized_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "auth.json"

    def fail(_source, _destination):
        raise OSError("sensitive temporary path")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(CredentialError, match="could not be saved") as caught:
        save_credential(path, API, TOKEN)
    assert "sensitive" not in str(caught.value)
    assert list(tmp_path.glob(".auth-*")) == []
