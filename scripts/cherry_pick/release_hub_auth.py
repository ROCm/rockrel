# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Private local credential storage for the Release Hub read adapter."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEVELOPER_CENTRAL_TOKEN_URL = "https://developer-central.amd.com/settings/api-tokens"
TOKEN_RE = re.compile(r"rrh1\.[A-Za-z0-9_-]{12}\.[A-Za-z0-9_-]{43}\Z")
MAX_CREDENTIAL_FILE_BYTES = 64 * 1024


class CredentialError(ValueError):
    """A Release Hub credential is missing or unsafe."""


@dataclass(frozen=True)
class Credential:
    """Represent credential in the release hub auth contract."""

    token: str = field(repr=False)
    source: str


def default_credential_path(environ: Mapping[str, str]) -> Path:
    """Return the private default path for Release Hub credentials."""

    configured = environ.get("ROCM_CHERRY_PICK_AUTH_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "rocm-cherry-pick" / "auth.json"


def validate_token(token: str) -> str:
    """Validate a Developer Central token without exposing its value."""

    value = token.strip()
    if TOKEN_RE.fullmatch(value) is None:
        raise CredentialError("Release Hub token format is invalid.")
    return value


def load_credential(
    *,
    api_origin: str,
    path: Path,
    environ: Mapping[str, str],
) -> Credential:
    """Load a validated token from the environment first, then the private store."""

    environment_token = environ.get("ROCM_RELEASE_HUB_TOKEN", "").strip()
    if environment_token:
        return Credential(validate_token(environment_token), "environment")
    store = _read_store(path)
    record = store["credentials"].get(api_origin)
    if record is None:
        raise CredentialError(
            "Release Hub API token is required. Create the ROCm Cherry-Pick CLI "
            f"preset with read:evidence at {DEVELOPER_CENTRAL_TOKEN_URL}, copy it "
            "once, then run `python3 /path/to/rocm-cherry-pick/scripts/"
            "rocm_cherry_pick.py auth login` after you replace "
            "/path/to/rocm-cherry-pick with the installed skill directory."
        )
    return Credential(validate_token(record["token"]), "credential_file")


def save_credential(path: Path, api_origin: str, token: str) -> None:
    """Validate and atomically persist one API-origin token in the private store."""

    value = validate_token(token)
    destination = Path(path).expanduser()
    _ensure_private_parent(destination.parent)
    store = _read_store(destination)
    store["credentials"][api_origin] = {"token": value}
    _atomic_write(destination, store)


def remove_credential(path: Path, api_origin: str) -> bool:
    """Remove one API-origin token and delete the store when it becomes empty."""

    destination = Path(path).expanduser()
    store = _read_store(destination)
    if api_origin not in store["credentials"]:
        return False
    del store["credentials"][api_origin]
    if store["credentials"]:
        _atomic_write(destination, store)
    elif destination.exists():
        destination.unlink()
    return True


def read_token_file(path: Path) -> str:
    """Read a token from a size-bounded private regular file and validate it."""

    source = Path(path).expanduser()
    info = _private_regular_file(source)
    if info.st_size > 1024:
        raise CredentialError("Token input file is too large.")
    try:
        return validate_token(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CredentialError("Token input file could not be read.") from exc


def _empty_store() -> dict[str, object]:
    """Return a new empty versioned credential store."""

    return {"schema_version": "rrh-auth.v1", "credentials": {}}


def _read_store(path: Path) -> dict[str, object]:
    """Read and strictly validate the versioned credential store."""

    destination = Path(path).expanduser()
    if not destination.exists() and not destination.is_symlink():
        return _empty_store()
    info = _private_regular_file(destination)
    if info.st_size > MAX_CREDENTIAL_FILE_BYTES:
        raise CredentialError("Credential file is too large.")
    try:
        raw = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialError("Credential file is malformed.") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "credentials"}:
        raise CredentialError("Credential file fields are invalid.")
    if raw.get("schema_version") != "rrh-auth.v1":
        raise CredentialError("Credential file schema is unsupported.")
    records = raw.get("credentials")
    if not isinstance(records, dict):
        raise CredentialError("Credential file credentials must be an object.")
    normalized: dict[str, dict[str, str]] = {}
    for origin, record in records.items():
        if (
            not isinstance(origin, str)
            or not isinstance(record, dict)
            or set(record) != {"token"}
            or not isinstance(record.get("token"), str)
        ):
            raise CredentialError("Credential file contains an invalid entry.")
        normalized[origin] = {"token": validate_token(record["token"])}
    return {"schema_version": "rrh-auth.v1", "credentials": normalized}


def _private_regular_file(path: Path) -> os.stat_result:
    """Prove a credential path is a private regular file."""

    try:
        info = path.lstat()
    except OSError as exc:
        raise CredentialError("Credential file could not be inspected.") from exc
    if stat.S_ISLNK(info.st_mode):
        raise CredentialError("Credential file must not be a symbolic link.")
    if not stat.S_ISREG(info.st_mode):
        raise CredentialError("Credential path must be a regular file.")
    if info.st_mode & 0o077:
        raise CredentialError("Credential file permissions must be 0600 or stricter.")
    return info


def _ensure_private_parent(parent: Path) -> None:
    """Create and validate the private credential parent directory."""

    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.lstat()
    except OSError as exc:
        raise CredentialError("Credential directory could not be prepared.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise CredentialError("Credential directory must be a real directory.")
    if info.st_mode & 0o077:
        raise CredentialError(
            "Credential directory permissions must be 0700 or stricter."
        )


def _atomic_write(path: Path, value: dict[str, object]) -> None:
    """Write the credential store atomically with private permissions."""

    encoded = (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    temporary: Path | None = None
    descriptor = -1
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
        temporary = Path(raw_path)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
    except OSError as exc:
        raise CredentialError("Credential file could not be saved.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
