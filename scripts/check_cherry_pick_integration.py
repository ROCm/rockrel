# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Validate the local five-repository cherry-pick integration contract."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

SCHEMA_VERSION = "rocm-cherry-pick-integration.v2"
REPORT_VERSION = "rocm-cherry-pick-integration-report.v1"
CANONICAL_ENDPOINT = "https://developer-central.amd.com/api/v1/cherry-pick/config"
CANONICAL_ISSUER = "https://token.actions.githubusercontent.com"
CANONICAL_AUDIENCE = "api://developer-central.amd.com/rocm-cherry-pick-config"
CANONICAL_WORKFLOW = "ROCm/rockrel/.github/workflows/cherry_pick.yml"
CANONICAL_REPOSITORY_OWNER = "ROCm"
CANONICAL_REPOSITORY_OWNER_ID = "21157610"
CANONICAL_REVISION = "c53e703568fe41129abf7139f018ac920bca9c59"
SERVER_MAPPING_FRAGMENTS = (
    "issuer: oidc.issuer",
    "audience: oidc.audience",
    "repository_owner: oidc.repositoryOwner",
    "callers: oidc.callers.map((caller) => ({",
    "repository: caller.repository",
    "repository_owner_id: caller.repositoryOwnerId",
    "repository_id: caller.repositoryId",
    "refs: caller.refs",
    "events: caller.events",
    "kind: caller.workflow.kind",
    "ref: caller.workflow.ref",
    "sha: caller.workflow.sha",
)
VERIFIER_MAPPING_FRAGMENTS = (
    'stringClaim(claims.repository, "repository")',
    'stringClaim(claims.repository_owner, "repository_owner")',
    'stringClaim(claims.ref, "ref")',
    "eventNameClaim(claims.event_name)",
    "caller.repository === repository",
    "caller.refs.includes(ref)",
    "caller.events.includes(eventName)",
    'idClaim(claims.repository_owner_id, "repository_owner_id")',
    'idClaim(claims.repository_id, "repository_id")',
    "repositoryOwnerId !== caller.repository_owner_id",
    "repositoryId !== caller.repository_id",
    'kind === "reusable" ? "job_workflow_ref" : "workflow_ref"',
    'kind === "reusable" ? "job_workflow_sha" : "workflow_sha"',
    "caller.workflow.ref === workflow",
    "candidate.workflow.sha === workflowSha",
)


def _canonical_caller_values() -> list[dict[str, object]]:
    """Return the reviewed five-caller GitHub Actions OIDC policy."""

    reusable = f"{CANONICAL_WORKFLOW}@{CANONICAL_REVISION}"
    identities = (
        ("ROCm/TheRock", "765605091", "refs/heads/main"),
        ("ROCm/rocm-systems", "962090208", "refs/heads/develop"),
        ("ROCm/rocm-libraries", "971570345", "refs/heads/develop"),
    )
    callers = [
        {
            "repository": repository,
            "repository_owner_id": CANONICAL_REPOSITORY_OWNER_ID,
            "repository_id": repository_id,
            "refs": [ref],
            "events": ["pull_request_target"],
            "workflow": {
                "kind": "reusable",
                "ref": reusable,
                "sha": CANONICAL_REVISION,
            },
        }
        for repository, repository_id, ref in identities
    ]
    callers.extend(
        [
            {
                "repository": "ROCm/rockrel",
                "repository_owner_id": CANONICAL_REPOSITORY_OWNER_ID,
                "repository_id": "1071689640",
                "refs": ["refs/heads/main"],
                "events": ["schedule", "workflow_dispatch"],
                "workflow": {
                    "kind": "direct",
                    "ref": (
                        "ROCm/rockrel/.github/workflows/"
                        "cherry_pick_reconcile.yml@refs/heads/main"
                    ),
                    "sha": CANONICAL_REVISION,
                },
            },
            {
                "repository": "ROCm/rockrel",
                "repository_owner_id": CANONICAL_REPOSITORY_OWNER_ID,
                "repository_id": "1071689640",
                "refs": ["refs/heads/main"],
                "events": ["workflow_dispatch"],
                "workflow": {
                    "kind": "direct",
                    "ref": f"{CANONICAL_WORKFLOW}@refs/heads/main",
                    "sha": CANONICAL_REVISION,
                },
            },
        ]
    )
    return callers


class IntegrationContractError(ValueError):
    """Report malformed or mutable reviewed integration configuration."""


@dataclass(frozen=True)
class WorkflowPolicy:
    """Hold one exact reusable or direct workflow identity."""

    kind: str
    ref: str
    sha: str

    def to_dict(self) -> dict[str, str]:
        """Return the reviewed workflow identity."""

        return {"kind": self.kind, "ref": self.ref, "sha": self.sha}


@dataclass(frozen=True)
class CallerPolicy:
    """Hold one exact repository, ref, event, identity, and workflow tuple."""

    repository: str
    repository_owner_id: str
    repository_id: str
    refs: tuple[str, ...]
    events: tuple[str, ...]
    workflow: WorkflowPolicy

    def to_dict(self) -> dict[str, object]:
        """Return the reviewed caller identity."""

        return {
            "repository": self.repository,
            "repository_owner_id": self.repository_owner_id,
            "repository_id": self.repository_id,
            "refs": list(self.refs),
            "events": list(self.events),
            "workflow": self.workflow.to_dict(),
        }


@dataclass(frozen=True)
class IntegrationContract:
    """Hold the exact trust anchors shared by all five local repositories."""

    schema_version: str
    config_endpoint: str
    oidc_issuer: str
    oidc_audience: str
    repository_owner: str
    reusable_workflow: str
    rockrel_revision: str
    callers: tuple[CallerPolicy, ...]

    @property
    def authorized_callers(self) -> tuple[str, ...]:
        """Return the three thin repositories that call the reusable workflow."""

        return tuple(
            caller.repository
            for caller in self.callers
            if caller.workflow.kind == "reusable"
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation of this reviewed contract."""

        return {
            "schema_version": self.schema_version,
            "config_endpoint": self.config_endpoint,
            "oidc_issuer": self.oidc_issuer,
            "oidc_audience": self.oidc_audience,
            "repository_owner": self.repository_owner,
            "reusable_workflow": self.reusable_workflow,
            "rockrel_revision": self.rockrel_revision,
            "callers": [caller.to_dict() for caller in self.callers],
        }


def _contract_error(field: str, message: str) -> IntegrationContractError:
    """Build a field-addressed validation error without exposing trust anchors."""

    return IntegrationContractError(f"{field}: {message}")


def _exact_string(value: object, field: str, expected: str) -> str:
    """Require one exact reviewed string value."""

    if value != expected:
        raise _contract_error(field, "must equal the reviewed canonical value")
    return expected


def load_manifest(path: Path) -> IntegrationContract:
    """Load and strictly validate one reviewed integration manifest."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _contract_error("manifest", f"could not be loaded: {exc}") from exc
    if not isinstance(value, dict):
        raise _contract_error("manifest", "must be a JSON object")
    fields = {
        "schema_version",
        "config_endpoint",
        "oidc_issuer",
        "oidc_audience",
        "repository_owner",
        "reusable_workflow",
        "rockrel_revision",
        "callers",
    }
    unexpected = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unexpected:
        raise _contract_error("extra", ", ".join(unexpected))
    if missing:
        raise _contract_error(missing[0], "is required")

    schema_version = _exact_string(
        value["schema_version"], "schema_version", SCHEMA_VERSION
    )
    config_endpoint = _exact_string(
        value["config_endpoint"], "config_endpoint", CANONICAL_ENDPOINT
    )
    oidc_issuer = _exact_string(value["oidc_issuer"], "oidc_issuer", CANONICAL_ISSUER)
    oidc_audience = _exact_string(
        value["oidc_audience"], "oidc_audience", CANONICAL_AUDIENCE
    )
    repository_owner = _exact_string(
        value["repository_owner"],
        "repository_owner",
        CANONICAL_REPOSITORY_OWNER,
    )
    reusable_workflow = _exact_string(
        value["reusable_workflow"], "reusable_workflow", CANONICAL_WORKFLOW
    )
    revision = _exact_string(
        value["rockrel_revision"], "rockrel_revision", CANONICAL_REVISION
    )
    caller_values = value["callers"]
    if caller_values != _canonical_caller_values():
        raise _contract_error(
            "callers", "must equal the reviewed five-caller OIDC policy"
        )
    callers = tuple(
        CallerPolicy(
            repository=caller["repository"],
            repository_owner_id=caller["repository_owner_id"],
            repository_id=caller["repository_id"],
            refs=tuple(caller["refs"]),
            events=tuple(caller["events"]),
            workflow=WorkflowPolicy(**caller["workflow"]),
        )
        for caller in caller_values
    )
    return IntegrationContract(
        schema_version=schema_version,
        config_endpoint=config_endpoint,
        oidc_issuer=oidc_issuer,
        oidc_audience=oidc_audience,
        repository_owner=repository_owner,
        reusable_workflow=reusable_workflow,
        rockrel_revision=revision,
        callers=callers,
    )


def _record(
    errors: list[dict[str, str]], path: Path, contract: str, condition: bool
) -> None:
    """Record a stable, non-secret diagnostic when one local assertion fails."""

    if not condition:
        errors.append(
            {
                "path": str(path),
                "contract": contract,
                "message": "does not match the reviewed integration contract",
            }
        )


def _text(path: Path, errors: list[dict[str, str]], contract: str) -> str | None:
    """Read one required local text file or append a fail-closed diagnostic."""

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        _record(errors, path, contract, False)
        return None


def _json_object(
    path: Path, errors: list[dict[str, str]], contract: str
) -> dict[str, object] | None:
    """Read one required local JSON object without accepting malformed data."""

    text = _text(path, errors, contract)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        _record(errors, path, contract, False)
        return None
    if not isinstance(value, dict):
        _record(errors, path, contract, False)
        return None
    return value


def _nested(value: object, *keys: str) -> object:
    """Return a nested mapping value, or a sentinel-free missing value."""

    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _contains_exact_token(source: str, expected: str) -> bool:
    """Match a reviewed URL or identifier without accepting a longer value."""

    token_character = r"A-Za-z0-9:/?#[\]@!$&'()*+,;=._~-"
    return (
        re.search(
            rf"(?<![{token_character}]){re.escape(expected)}(?![{token_character}])",
            source,
        )
        is not None
    )


def _check_rockrel(
    root: Path,
    contract: IntegrationContract,
    errors: list[dict[str, str]],
) -> None:
    """Validate rockrel source constants and reusable-workflow documentation."""

    control_plane = root / "scripts/cherry_pick/control_plane.py"
    source = _text(control_plane, errors, "oidc_audience")
    if source is not None:
        expected = f'ACTIONS_OIDC_AUDIENCE = "{contract.oidc_audience}"'
        _record(errors, control_plane, "oidc_audience", expected in source)

    workflow = root / ".github/workflows/cherry_pick.yml"
    source = _text(workflow, errors, "reusable_workflow")
    if source is not None:
        _record(
            errors,
            workflow,
            "oidc_audience",
            _contains_exact_token(source, contract.oidc_audience),
        )
        _record(
            errors,
            workflow,
            "config_endpoint",
            _contains_exact_token(source, contract.config_endpoint),
        )


def _check_release_hub(
    root: Path,
    contract: IntegrationContract,
    errors: list[dict[str, str]],
) -> None:
    """Validate the v5 policy projection and verifier wiring structurally."""

    config_path = root / "config/release-trains.json"
    config = _json_object(config_path, errors, "release_trains")
    if config is not None:
        _record(
            errors,
            config_path,
            "schema_version",
            config.get("schemaVersion") == "release-trains.v5",
        )
        oidc = _nested(config, "automation", "cherryPick", "githubActionsOidc")
        if not isinstance(oidc, dict):
            for label in (
                "oidc_issuer",
                "oidc_audience",
                "repository_owner",
                "callers",
            ):
                _record(errors, config_path, label, False)
        else:
            _record(
                errors,
                config_path,
                "oidc_issuer",
                oidc.get("issuer") == contract.oidc_issuer,
            )
            _record(
                errors,
                config_path,
                "oidc_audience",
                oidc.get("audience") == contract.oidc_audience,
            )
            _record(
                errors,
                config_path,
                "repository_owner",
                oidc.get("repositoryOwner") == contract.repository_owner,
            )
            _record(
                errors,
                config_path,
                "oidc_shape",
                set(oidc) == {"issuer", "audience", "repositoryOwner", "callers"},
            )
            actual_callers = oidc.get("callers")
            _record(
                errors,
                config_path,
                "callers",
                isinstance(actual_callers, list)
                and len(actual_callers) == len(contract.callers),
            )
            if isinstance(actual_callers, list):
                for actual, expected in zip(actual_callers, contract.callers):
                    if not isinstance(actual, dict):
                        _record(errors, config_path, "callers", False)
                        continue
                    _record(
                        errors,
                        config_path,
                        "caller_shape",
                        set(actual)
                        == {
                            "repository",
                            "repositoryOwnerId",
                            "repositoryId",
                            "refs",
                            "events",
                            "workflow",
                        },
                    )
                    caller_checks = (
                        ("repository", "repository", expected.repository),
                        (
                            "repository_owner_id",
                            "repositoryOwnerId",
                            expected.repository_owner_id,
                        ),
                        ("repository_id", "repositoryId", expected.repository_id),
                        ("refs", "refs", list(expected.refs)),
                        ("events", "events", list(expected.events)),
                    )
                    for label, field, expected_value in caller_checks:
                        _record(
                            errors,
                            config_path,
                            label,
                            actual.get(field) == expected_value,
                        )
                    workflow = actual.get("workflow")
                    if not isinstance(workflow, dict):
                        for label in (
                            "workflow_kind",
                            "workflow_ref",
                            "workflow_sha",
                        ):
                            _record(errors, config_path, label, False)
                        continue
                    _record(
                        errors,
                        config_path,
                        "workflow_shape",
                        set(workflow) == {"kind", "ref", "sha"},
                    )
                    workflow_checks = (
                        ("workflow_kind", "kind", expected.workflow.kind),
                        ("workflow_ref", "ref", expected.workflow.ref),
                        ("workflow_sha", "sha", expected.workflow.sha),
                    )
                    for label, field, expected_value in workflow_checks:
                        _record(
                            errors,
                            config_path,
                            label,
                            workflow.get(field) == expected_value,
                        )

    server = root / "lambdas/query-proxy/src/server.ts"
    source = _text(server, errors, "config_endpoint")
    if source is not None:
        route = urlsplit(contract.config_endpoint).path
        normalized = " ".join(source.split())
        _record(errors, server, "config_endpoint", route in source)
        _record(
            errors,
            server,
            "server_mapping",
            all(fragment in normalized for fragment in SERVER_MAPPING_FRAGMENTS),
        )

    verifier = root / "lambdas/query-proxy/src/githubActionsOidc.ts"
    source = _text(verifier, errors, "verifier_mapping")
    if source is not None:
        normalized = " ".join(source.split())
        _record(
            errors,
            verifier,
            "verifier_mapping",
            all(fragment in normalized for fragment in VERIFIER_MAPPING_FRAGMENTS),
        )


def _check_callers(
    workspace_root: Path,
    contract: IntegrationContract,
    errors: list[dict[str, str]],
) -> None:
    """Validate immutable reusable-workflow pins in every thin caller."""

    for caller in contract.callers:
        if caller.workflow.kind != "reusable":
            continue
        checkout = caller.repository.split("/", 1)[1]
        workflow = (
            workspace_root / checkout / ".github/workflows/cherry_pick_request.yml"
        )
        source = _text(workflow, errors, "caller_workflow")
        if source is None:
            continue
        expected_uses = f"uses: {caller.workflow.ref}"
        expected_ref = f"automation_ref: {caller.workflow.sha}"
        _record(errors, workflow, "uses", expected_uses in source)
        _record(errors, workflow, "automation_ref", expected_ref in source)


def validate_local_checkout(
    *, manifest_path: Path, workspace_root: Path, release_hub_root: Path
) -> dict[str, object]:
    """Validate all five repositories using local files and no network access."""

    contract = load_manifest(manifest_path)
    workspace_root = Path(workspace_root).resolve()
    release_hub_root = Path(release_hub_root).resolve()
    errors: list[dict[str, str]] = []
    _check_rockrel(workspace_root / "rockrel", contract, errors)
    _check_release_hub(release_hub_root, contract, errors)
    _check_callers(workspace_root, contract, errors)
    errors.sort(key=lambda item: (item["path"], item["contract"], item["message"]))
    return {
        "schema_version": REPORT_VERSION,
        "valid": not errors,
        "manifest": str(Path(manifest_path).resolve()),
        "checked_repositories": [
            "ROCm/rockrel",
            "ROCm/Release-Hub",
            *contract.authorized_callers,
        ],
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit-root command-line interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--release-hub-root", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the checkout and return zero only for an exact contract match."""

    args = build_parser().parse_args(argv)
    try:
        report = validate_local_checkout(
            manifest_path=args.manifest,
            workspace_root=args.workspace_root,
            release_hub_root=args.release_hub_root,
        )
    except IntegrationContractError as exc:
        report = {
            "schema_version": REPORT_VERSION,
            "valid": False,
            "manifest": str(args.manifest.resolve()),
            "checked_repositories": [],
            "errors": [
                {
                    "path": str(args.manifest),
                    "contract": "manifest",
                    "message": str(exc),
                }
            ],
        }
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        print("valid" if report["valid"] else "invalid")
        for error in report["errors"]:
            print(f"{error['path']}: {error['contract']}: {error['message']}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
