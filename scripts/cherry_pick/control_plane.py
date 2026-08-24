# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""GitHub Actions OIDC and immutable Developer Central config snapshots."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path

from .release_hub import (
    DEFAULT_TIMEOUT_SECONDS,
    DIGEST_RE,
    ReleaseHubClient,
    ReleaseHubConfigSnapshot,
    ReleaseHubError,
    _urllib_transport,
)


ACTIONS_OIDC_AUDIENCE = "api://developer-central.amd.com/rocm-cherry-pick-config"
JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
Transport = Callable[[str, Mapping[str, str], int], object]


def fetch_actions_oidc_token(
    environment: Mapping[str, str],
    *,
    audience: str = ACTIONS_OIDC_AUDIENCE,
    transport: Transport = _urllib_transport,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Request one short-lived OIDC token without persisting or displaying it."""

    if environment.get("GITHUB_ACTIONS") != "true":
        raise ReleaseHubError("OIDC configuration is available only in GitHub Actions.")
    request_url = environment.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not request_token or len(request_token) > 16_384:
        raise ReleaseHubError("GitHub Actions OIDC request token is unavailable.")
    try:
        parsed = urllib.parse.urlsplit(request_url)
    except ValueError as exc:
        raise ReleaseHubError("GitHub Actions OIDC request URL is invalid.") from exc
    if parsed.scheme != "https":
        raise ReleaseHubError("GitHub Actions OIDC request URL requires HTTPS.")
    hostname = (parsed.hostname or "").lower()
    if (
        not hostname.endswith(".actions.githubusercontent.com")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ReleaseHubError(
            "GitHub Actions OIDC request URL must use actions.githubusercontent.com."
        )
    if not audience or len(audience) > 512:
        raise ReleaseHubError("GitHub Actions OIDC audience is invalid.")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(key == "audience" for key, _value in query):
        raise ReleaseHubError(
            "GitHub Actions OIDC request URL already has an audience."
        )
    url = urllib.parse.urlunsplit(
        (
            "https",
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode([*query, ("audience", audience)]),
            "",
        )
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {request_token}",
        "User-Agent": "rocm-cherry-pick/1.0",
    }
    try:
        response = transport(url, headers, timeout)
    except ReleaseHubError:
        raise
    except Exception as exc:
        raise ReleaseHubError(
            "GitHub Actions OIDC request failed without a usable response."
        ) from exc
    if not isinstance(response, dict):
        raise ReleaseHubError("GitHub Actions OIDC response must be an object.")
    value = response.get("value")
    if not isinstance(value, str) or not value:
        raise ReleaseHubError("GitHub Actions OIDC response.value is unavailable.")
    if len(value) > 16_384 or JWT_RE.fullmatch(value) is None:
        raise ReleaseHubError(
            "GitHub Actions OIDC response value is not a compact JWT."
        )
    return value


def fetch_action_config(
    api_origin: str,
    environment: Mapping[str, str],
    *,
    oidc_transport: Transport = _urllib_transport,
    release_hub_transport: Transport | None = None,
) -> ReleaseHubConfigSnapshot:
    """Use an Actions OIDC assertion to fetch a validated Release Hub config snapshot."""

    token = fetch_actions_oidc_token(
        environment,
        transport=oidc_transport,
    )
    client = ReleaseHubClient(
        api_origin,
        token,
        token_kind="oidc",
        transport=release_hub_transport,
    )
    return client.cherry_pick_config()


def write_config_snapshot(path: str | Path, snapshot: ReleaseHubConfigSnapshot) -> None:
    """Atomically write a validated config snapshot to a private 0600 regular file."""

    destination = Path(path)
    if not destination.is_absolute():
        raise ReleaseHubError("Release Hub config snapshot path must be absolute.")
    if not destination.parent.is_dir():
        raise ReleaseHubError("Release Hub config snapshot parent is unavailable.")
    if destination.is_symlink():
        raise ReleaseHubError("Release Hub config snapshot path must not be a symlink.")
    payload = (
        json.dumps(
            snapshot.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".cherry-pick-config-",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise ReleaseHubError(
            "Release Hub config snapshot could not be written."
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def load_config_snapshot(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> ReleaseHubConfigSnapshot:
    """Load a private regular snapshot and reject permission, size, or digest drift."""

    source = Path(path)
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise ReleaseHubError("Release Hub config snapshot is unavailable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseHubError("Release Hub config snapshot must be a regular file.")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ReleaseHubError("Release Hub config snapshot permissions must be 0600.")
    if metadata.st_size > MAX_SNAPSHOT_BYTES:
        raise ReleaseHubError("Release Hub config snapshot is too large.")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseHubError("Release Hub config snapshot is malformed.") from exc
    snapshot = ReleaseHubConfigSnapshot.from_dict(raw)
    if expected_sha256 is not None:
        if DIGEST_RE.fullmatch(expected_sha256) is None:
            raise ReleaseHubError("Expected Release Hub config digest is malformed.")
        if snapshot.configuration_sha256 != expected_sha256:
            raise ReleaseHubError("Release Hub config snapshot digest changed.")
    return snapshot
