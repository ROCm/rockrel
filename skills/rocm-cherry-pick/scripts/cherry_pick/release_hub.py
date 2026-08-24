# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Read-only Release Hub adapter for exact destination-train resolution."""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import (
    SUPPORTED_REPOSITORIES,
    TRAIN_ID_RE,
    ConfigError,
    TrainCatalog,
    parse_config,
    valid_branch_name,
)
from .release_hub_auth import CredentialError, validate_token

SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
OIDC_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15


class ReleaseHubError(RuntimeError):
    """Release Hub data is unavailable, unauthorized, or unsafe to use."""


@dataclass(frozen=True)
class ReleaseHubSession:
    """Represent release hub session in the release hub contract."""

    display_name: str
    scopes: tuple[str, ...]
    expires_at: str
    expires_within_days: int | None


@dataclass(frozen=True)
class ReleaseHubConfigSnapshot:
    """Represent release hub config snapshot in the release hub contract."""

    request_id: str
    generated_at: str
    configuration_schema: str
    configuration_sha256: str
    configuration_loaded_at: str
    catalog: TrainCatalog
    catalog_payload: dict[str, object] = field(repr=False)

    def as_dict(self) -> dict[str, object]:
        """Serialize this release hub config snapshot into its stable dictionary contract."""

        return {
            "schema_version": "release-hub-config-snapshot.v1",
            "request_id": self.request_id,
            "generated_at": self.generated_at,
            "configuration": {
                "schema_version": self.configuration_schema,
                "sha256": self.configuration_sha256,
                "loaded_at": self.configuration_loaded_at,
            },
            "catalog": json.loads(json.dumps(self.catalog_payload)),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ReleaseHubConfigSnapshot":
        """Parse and validate a release hub config snapshot from its dictionary contract."""

        raw = _object(value, "config snapshot")
        _exact_fields(
            raw,
            {
                "schema_version",
                "request_id",
                "generated_at",
                "configuration",
                "catalog",
            },
            "config snapshot",
        )
        if raw.get("schema_version") != "release-hub-config-snapshot.v1":
            raise ReleaseHubError("Release Hub config snapshot schema is unsupported.")
        request_id = _string(raw, "request_id", "config snapshot")
        generated_at = _string(raw, "generated_at", "config snapshot")
        _timestamp(generated_at, "config snapshot generated_at")
        configuration = _object(
            raw.get("configuration"), "config snapshot configuration"
        )
        _exact_fields(
            configuration,
            {"schema_version", "sha256", "loaded_at"},
            "config snapshot configuration",
        )
        schema = _string(
            configuration, "schema_version", "config snapshot configuration"
        )
        if schema != "release-trains.v5":
            raise ReleaseHubError("Release Hub config source schema is unsupported.")
        digest = _string(configuration, "sha256", "config snapshot configuration")
        if DIGEST_RE.fullmatch(digest) is None:
            raise ReleaseHubError("Release Hub config source hash is malformed.")
        loaded_at = _string(configuration, "loaded_at", "config snapshot configuration")
        _timestamp(loaded_at, "config snapshot source loaded_at")
        catalog_payload = _object(raw.get("catalog"), "config snapshot catalog")
        try:
            catalog = parse_config(catalog_payload)
        except ConfigError as exc:
            raise ReleaseHubError(
                f"Release Hub config catalog is invalid: {exc}"
            ) from exc
        return cls(
            request_id=request_id,
            generated_at=generated_at,
            configuration_schema=schema,
            configuration_sha256=digest,
            configuration_loaded_at=loaded_at,
            catalog=catalog,
            catalog_payload=json.loads(json.dumps(catalog_payload)),
        )


@dataclass(frozen=True)
class ReleaseHubBranchSnapshot:
    """Represent release hub branch snapshot in the release hub contract."""

    repository: str
    branch: str
    purpose: str
    created_sha: str

    def as_dict(self) -> dict[str, str]:
        """Serialize this release hub branch snapshot into its stable dictionary contract."""

        return {
            "repository": self.repository,
            "branch": self.branch,
            "purpose": self.purpose,
            "created_sha": self.created_sha,
        }


@dataclass(frozen=True)
class ReleaseHubTrainSnapshot:
    """Represent release hub train snapshot in the release hub contract."""

    train_id: str
    train_type: str
    state: str
    request_id: str
    configuration_schema: str
    configuration_sha256: str
    configuration_loaded_at: str
    branches: tuple[ReleaseHubBranchSnapshot, ...]
    destination_repository: str
    destination_branch: str
    destination_created_sha: str

    def as_dict(self) -> dict[str, object]:
        """Serialize this release hub train snapshot into its stable dictionary contract."""

        return {
            "schema_version": "release-hub-train-snapshot.v1",
            "train_id": self.train_id,
            "train_type": self.train_type,
            "state": self.state,
            "request_id": self.request_id,
            "configuration": {
                "schema_version": self.configuration_schema,
                "sha256": self.configuration_sha256,
                "loaded_at": self.configuration_loaded_at,
            },
            "branches": [item.as_dict() for item in self.branches],
            "destination": {
                "repository": self.destination_repository,
                "branch": self.destination_branch,
                "created_sha": self.destination_created_sha,
            },
        }


Transport = Callable[[str, Mapping[str, str], int], object]


class ReleaseHubClient:
    """Provide a read-only client for Developer Central release evidence."""

    def __init__(
        self,
        api_origin: str,
        token: str,
        *,
        transport: Transport | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        now: Callable[[], datetime] | None = None,
        token_kind: str = "api",
    ) -> None:
        """Initialize the read-only Release Hub client with its token and transport."""

        self.api_origin = validate_api_origin(api_origin)
        if token_kind == "api":
            try:
                self._token = validate_token(token)
            except CredentialError as exc:
                raise ReleaseHubError(str(exc)) from exc
        elif token_kind == "oidc":
            if (
                not isinstance(token, str)
                or len(token) > 16_384
                or OIDC_RE.fullmatch(token) is None
            ):
                raise ReleaseHubError(
                    "GitHub Actions OIDC credential is not a compact JWT."
                )
            self._token = token
        else:
            raise ReleaseHubError("Release Hub credential kind is unsupported.")
        self._transport = transport or _urllib_transport
        self._timeout = timeout
        self._now = now or (lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        """Return a redacted diagnostic representation of the release hub client."""

        return f"ReleaseHubClient(api_origin={self.api_origin!r}, token=<redacted>)"

    def session(self) -> ReleaseHubSession:
        """Fetch and validate the current Developer Central token session."""

        payload = self._get("/api/v1/auth/session")
        root = _object(payload, "session response")
        _string(root, "requestId", "session response")
        data = _object(root.get("data"), "session response data")
        display_name = _string(data, "displayName", "session response data")
        scopes_raw = data.get("scopes")
        if not isinstance(scopes_raw, list) or any(
            not isinstance(item, str) for item in scopes_raw
        ):
            raise ReleaseHubError("Release Hub session scopes are malformed.")
        scopes = tuple(sorted(set(scopes_raw)))
        if "read:evidence" not in scopes:
            raise ReleaseHubError("Release Hub token requires the read:evidence scope.")
        expires_at = _string(data, "expiresAt", "session response data")
        expiry = _timestamp(expires_at, "session expiry")
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        remaining = expiry - now.astimezone(timezone.utc)
        if remaining.total_seconds() <= 0:
            raise ReleaseHubError("Release Hub token is expired.")
        days = max(1, math.ceil(remaining.total_seconds() / 86_400))
        return ReleaseHubSession(
            display_name=display_name,
            scopes=scopes,
            expires_at=expires_at,
            expires_within_days=days if days <= 7 else None,
        )

    def cherry_pick_config(self) -> ReleaseHubConfigSnapshot:
        """Fetch and validate the reviewed cherry-pick configuration snapshot."""

        root = _object(
            self._get("/api/v1/cherry-pick/config"),
            "cherry-pick config response",
        )
        _exact_fields(
            root,
            {"requestId", "data"},
            "cherry-pick config response",
        )
        request_id = _string(root, "requestId", "cherry-pick config response")
        data = _object(root.get("data"), "cherry-pick config data")
        _exact_fields(
            data,
            {
                "schemaVersion",
                "generatedAt",
                "source",
                "authorization",
                "dependency_policy",
                "coverage_policy",
                "trains",
            },
            "cherry-pick config data",
        )
        if data.get("schemaVersion") != "cherry-pick-config.v1":
            raise ReleaseHubError(
                "Release Hub cherry-pick config schema is unsupported."
            )
        generated_at = _string(data, "generatedAt", "cherry-pick config data")
        _timestamp(generated_at, "cherry-pick config generatedAt")
        source = _object(data.get("source"), "cherry-pick config source")
        _exact_fields(
            source,
            {"schemaVersion", "sha256", "loadedAt"},
            "cherry-pick config source",
        )
        source_schema = _string(source, "schemaVersion", "cherry-pick config source")
        if source_schema != "release-trains.v5":
            raise ReleaseHubError(
                "Release Hub cherry-pick config source schema is unsupported."
            )
        source_sha = _string(source, "sha256", "cherry-pick config source")
        if DIGEST_RE.fullmatch(source_sha) is None:
            raise ReleaseHubError(
                "Release Hub cherry-pick config source hash is malformed."
            )
        loaded_at = _string(source, "loadedAt", "cherry-pick config source")
        _timestamp(loaded_at, "cherry-pick config source loadedAt")

        trains_raw = data.get("trains")
        if not isinstance(trains_raw, list):
            raise ReleaseHubError(
                "Release Hub cherry-pick config trains are malformed."
            )
        trains: list[dict[str, object]] = []
        for index, value in enumerate(trains_raw):
            train = _object(value, f"cherry-pick config trains[{index}]")
            normalized = json.loads(json.dumps(train))
            state = normalized.get("state")
            if state == "disabled":
                normalized["state"] = "inactive"
            elif state != "active":
                raise ReleaseHubError(
                    f"Release Hub cherry-pick config trains[{index}].state is invalid."
                )
            trains.append(normalized)

        catalog_payload: dict[str, object] = {
            "schema_version": 5,
            "authorization": json.loads(json.dumps(data.get("authorization"))),
            "dependency_policy": json.loads(json.dumps(data.get("dependency_policy"))),
            "coverage_policy": json.loads(json.dumps(data.get("coverage_policy"))),
            "trains": trains,
        }
        try:
            catalog = parse_config(catalog_payload)
        except ConfigError as exc:
            raise ReleaseHubError(
                f"Release Hub cherry-pick config catalog is invalid: {exc}"
            ) from exc
        return ReleaseHubConfigSnapshot(
            request_id=request_id,
            generated_at=generated_at,
            configuration_schema=source_schema,
            configuration_sha256=source_sha,
            configuration_loaded_at=loaded_at,
            catalog=catalog,
            catalog_payload=catalog_payload,
        )

    def resolve_train(self, train_id: str, repository: str) -> ReleaseHubTrainSnapshot:
        """Resolve one enabled train to exactly one confirmed repository destination."""

        if TRAIN_ID_RE.fullmatch(train_id) is None:
            raise ReleaseHubError("Release Hub train id is invalid.")
        if repository not in SUPPORTED_REPOSITORIES:
            raise ReleaseHubError("Source repository is not supported.")
        quoted = urllib.parse.quote(train_id, safe="")
        root = _object(self._get(f"/api/v1/trains/{quoted}"), "train response")
        request_id = _string(root, "requestId", "train response")
        data = _object(root.get("data"), "train response data")
        validation = _object(data.get("validation"), "train validation")
        if validation.get("valid") is not True:
            raise ReleaseHubError("Release Hub train configuration is invalid.")
        source = _object(data.get("source"), "train source")
        schema = _string(source, "schemaVersion", "train source")
        if schema != "release-trains.v5":
            raise ReleaseHubError("Release Hub train source schema is unsupported.")
        configuration_sha = _string(source, "sha256", "train source")
        if DIGEST_RE.fullmatch(configuration_sha) is None:
            raise ReleaseHubError("Release Hub train source hash is malformed.")
        loaded_at = _string(source, "loadedAt", "train source")
        _timestamp(loaded_at, "train source loadedAt")

        train = _object(data.get("train"), "train")
        if _string(train, "trainId", "train") != train_id:
            raise ReleaseHubError("Release Hub returned a different train id.")
        if train.get("planned") is not False:
            raise ReleaseHubError("Release Hub train is planned, not configured.")
        state = _string(train, "state", "train")
        if state != "enabled":
            raise ReleaseHubError("Release Hub train must be enabled.")
        train_type = _string(train, "trainType", "train")
        if train_type not in {"patch", "standard", "express"}:
            raise ReleaseHubError("Release Hub train type is invalid.")
        readiness = _object(train.get("branchReadiness"), "train branch readiness")
        if readiness.get("status") != "ready":
            raise ReleaseHubError("Release Hub train branches are not ready.")
        branches_raw = train.get("branches")
        if not isinstance(branches_raw, list):
            raise ReleaseHubError("Release Hub train branches are malformed.")

        by_repository: dict[str, ReleaseHubBranchSnapshot] = {}
        for index, raw_branch in enumerate(branches_raw):
            item = _object(raw_branch, f"train branches[{index}]")
            branch_repository = _string(
                item, "repoFullName", f"train branches[{index}]"
            )
            purpose = _string(item, "purpose", f"train branches[{index}]")
            if branch_repository not in SUPPORTED_REPOSITORIES or purpose not in {
                "release",
                "bkc_cherrypick",
            }:
                continue
            if branch_repository in by_repository:
                raise ReleaseHubError(
                    f"Release Hub must provide exactly one destination branch for {branch_repository}."
                )
            if item.get("resolutionStatus") != "confirmed":
                raise ReleaseHubError(
                    f"Release Hub destination branch for {branch_repository} is not confirmed."
                )
            created_at = item.get("createdAt")
            if not isinstance(created_at, str):
                raise ReleaseHubError(
                    f"Release Hub destination branch for {branch_repository} is not created."
                )
            _timestamp(created_at, f"train branches[{index}].createdAt")
            branch_name = _string(item, "branch", f"train branches[{index}]")
            if not valid_branch_name(branch_name):
                raise ReleaseHubError("Release Hub destination branch name is invalid.")
            created_sha = _string(item, "createdSha", f"train branches[{index}]")
            if SHA_RE.fullmatch(created_sha) is None:
                raise ReleaseHubError("Release Hub branch creation SHA is malformed.")
            by_repository[branch_repository] = ReleaseHubBranchSnapshot(
                repository=branch_repository,
                branch=branch_name,
                purpose=purpose,
                created_sha=created_sha.lower(),
            )
        selected = by_repository.get(repository)
        if selected is None:
            raise ReleaseHubError(
                f"Release Hub must provide exactly one confirmed destination branch for {repository}."
            )
        branches = tuple(by_repository[key] for key in sorted(by_repository))
        return ReleaseHubTrainSnapshot(
            train_id=train_id,
            train_type=train_type,
            state=state,
            request_id=request_id,
            configuration_schema=schema,
            configuration_sha256=configuration_sha,
            configuration_loaded_at=loaded_at,
            branches=branches,
            destination_repository=repository,
            destination_branch=selected.branch,
            destination_created_sha=selected.created_sha,
        )

    def _get(self, path: str) -> object:
        """Send an authenticated Release Hub GET and sanitize unexpected transport failures."""

        url = f"{self.api_origin}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "rocm-cherry-pick/1.0",
        }
        try:
            return self._transport(url, headers, self._timeout)
        except ReleaseHubError:
            raise
        except Exception as exc:
            raise ReleaseHubError(
                "Release Hub request failed without a usable response."
            ) from exc


def validate_api_origin(value: str) -> str:
    """Validate and normalize an HTTPS Release Hub API origin."""

    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError as exc:
        raise ReleaseHubError("Release Hub API origin is invalid.") from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseHubError(
            "Release Hub API must be an origin without credentials or a path."
        )
    loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ReleaseHubError("Release Hub API requires HTTPS except on loopback.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ReleaseHubError("Release Hub API port is invalid.") from exc
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, authority, "", "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject HTTP redirects so API-origin validation remains fail closed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects so the reviewed API origin cannot change."""

        return None


def _urllib_transport(url: str, headers: Mapping[str, str], timeout: int) -> object:
    """Perform one no-redirect Release Hub HTTP request."""

    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise ReleaseHubError("Release Hub returned a non-JSON response.")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ReleaseHubError("Release Hub response is too large.")
    except urllib.error.HTTPError as exc:
        raise ReleaseHubError(f"Release Hub request returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReleaseHubError("Release Hub is unreachable.") from exc
    try:
        return json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseHubError("Release Hub returned malformed JSON.") from exc


def _object(value: object, context: str) -> dict[str, Any]:
    """Validate and return an object-shaped contract value."""

    if not isinstance(value, dict):
        raise ReleaseHubError(f"{context} must be an object.")
    return value


def _exact_fields(value: Mapping[str, object], fields: set[str], context: str) -> None:
    """Reject missing or unknown fields in a versioned contract."""

    unsupported = set(value) - fields
    if unsupported:
        raise ReleaseHubError(
            f"{context} contains unsupported field {sorted(unsupported)[0]}."
        )
    missing = fields - set(value)
    if missing:
        raise ReleaseHubError(f"{context} omitted {sorted(missing)[0]}.")


def _string(value: Mapping[str, object], key: str, context: str) -> str:
    """Validate and return a required string contract value."""

    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ReleaseHubError(f"{context}.{key} must be a non-empty string.")
    return item


def _timestamp(value: str, context: str) -> datetime:
    """Parse and normalize one Release Hub timestamp."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseHubError(f"{context} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ReleaseHubError(f"{context} must include a timezone.")
    return parsed.astimezone(timezone.utc)
