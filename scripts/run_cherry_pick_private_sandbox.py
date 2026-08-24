# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Prepare and gate the private GitHub qualification scenario suite."""

import argparse
from dataclasses import dataclass
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_MANIFEST = ROOT / "config/cherry-pick-private-sandbox.json"
SCHEMA_VERSION = "cherry-pick-private-sandbox.v1"
WRITE_CONFIRMATION = "I_UNDERSTAND_THIS_WRITES_TO_A_PRIVATE_GITHUB_SANDBOX"
PRIVATE_VISIBILITY = "PRIVATE"
SENTINEL_NAME = "ROCM_CHERRY_PICK_PRIVATE_SANDBOX"
SENTINEL_VALUE = "REVIEWED_REMOTE_WRITES_ONLY"
SANDBOX_BRANCH_PREFIX = "sandbox/cherry-pick/"
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SCENARIO_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")
REQUIRED_SCENARIOS = (
    "oidc_config",
    "installation_token_exchange",
    "protected_branches",
    "draft_only",
    "partial_recovery",
    "duplicate_delivery",
    "branch_protection_denial",
    "stale_evidence_rejection",
    "conflict_handling",
    "dependency_ordering",
)
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "requires_private_repository",
        "required_repository_visibility",
        "repository_allowlist",
        "production_repository_ids",
        "repository_sentinel",
        "sandbox_branch_prefix",
        "write_confirmation",
        "scenarios",
    }
)
SCENARIO_FIELDS = frozenset(
    {"id", "description", "required_assertions", "cleanup", "writes_remote"}
)


class SandboxAuthorizationError(PermissionError):
    """Report missing or invalid authority for private remote writes."""


class SandboxManifestError(ValueError):
    """Report a malformed reviewed private-sandbox scenario manifest."""


@dataclass(frozen=True)
class SandboxRepositoryEvidence:
    """Bind immutable repository identity facts returned by a trusted probe.

    The prepare-only CLI never constructs this evidence. An explicitly reviewed
    harness may inject a probe, but the runner accepts no executor call until all
    four fields exactly match the reviewed manifest and code literals.
    """

    repository_id: int
    visibility: str
    sentinel_name: str
    sentinel_value: str


def _require_exact_fields(
    payload: Mapping[str, object], expected: frozenset[str], context: str
) -> None:
    """Reject omitted and unexpected fields in a security-reviewed object."""

    fields = set(payload)
    if fields != expected:
        missing = ", ".join(sorted(expected - fields)) or "none"
        unexpected = ", ".join(sorted(fields - expected)) or "none"
        raise SandboxManifestError(
            f"{context} fields differ: missing {missing}; unexpected {unexpected}"
        )


def _repository_id(value: object, context: str) -> int:
    """Return a positive numeric GitHub repository ID, excluding booleans."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SandboxManifestError(f"{context} must be a positive integer")
    return value


def _repository_allowlist(payload: Mapping[str, object]) -> dict[str, int]:
    """Validate and return the exact reviewed repository-name-to-ID bindings."""

    raw = payload.get("repository_allowlist")
    if not isinstance(raw, dict) or not raw:
        raise SandboxManifestError(
            "repository allowlist must be a nonempty name-to-ID object"
        )
    result: dict[str, int] = {}
    folded_names: set[str] = set()
    for repository, identifier in raw.items():
        if (
            not isinstance(repository, str)
            or REPOSITORY_RE.fullmatch(repository) is None
        ):
            raise SandboxManifestError(
                "repository allowlist entries must use exact OWNER/REPOSITORY names"
            )
        folded = repository.casefold()
        if folded in folded_names:
            raise SandboxManifestError(
                "repository allowlist contains case-ambiguous names"
            )
        folded_names.add(folded)
        result[repository] = _repository_id(
            identifier, f"repository ID for {repository}"
        )
    if len(set(result.values())) != len(result):
        raise SandboxManifestError(
            "repository allowlist must bind each repository ID only once"
        )
    return result


def _production_repository_ids(payload: Mapping[str, object]) -> frozenset[int]:
    """Validate and return the explicit production-repository ID denylist."""

    raw = payload.get("production_repository_ids")
    if not isinstance(raw, list) or not raw:
        raise SandboxManifestError("production repository IDs must be a nonempty array")
    identifiers = [
        _repository_id(value, "each production repository ID") for value in raw
    ]
    if len(set(identifiers)) != len(identifiers):
        raise SandboxManifestError("production repository IDs must be unique")
    return frozenset(identifiers)


def _validate_repository_contract(payload: Mapping[str, object]) -> None:
    """Validate every literal and identity binding that guards remote writes."""

    if payload.get("required_repository_visibility") != PRIVATE_VISIBILITY:
        raise SandboxManifestError(
            f"required repository visibility must be the exact {PRIVATE_VISIBILITY} literal"
        )
    allowlist = _repository_allowlist(payload)
    production_ids = _production_repository_ids(payload)
    if set(allowlist.values()) & production_ids:
        raise SandboxManifestError(
            "repository allowlist contains a listed production repository ID"
        )
    sentinel = payload.get("repository_sentinel")
    expected_sentinel = {"name": SENTINEL_NAME, "value": SENTINEL_VALUE}
    if sentinel != expected_sentinel:
        raise SandboxManifestError(
            "repository sentinel must match the exact reviewed name and value"
        )
    if payload.get("sandbox_branch_prefix") != SANDBOX_BRANCH_PREFIX:
        raise SandboxManifestError(
            "sandbox branch prefix does not match the reviewed literal"
        )
    if payload.get("write_confirmation") != WRITE_CONFIRMATION:
        raise SandboxManifestError(
            "write confirmation does not match the reviewed literal"
        )


def _validate_scenarios(raw: object) -> None:
    """Validate the complete ordered qualification inventory before execution."""

    if not isinstance(raw, list) or not raw:
        raise SandboxManifestError("scenarios must be a nonempty array")
    identifiers: list[str] = []
    for scenario in raw:
        if not isinstance(scenario, dict):
            raise SandboxManifestError("each scenario must be a JSON object")
        _require_exact_fields(scenario, SCENARIO_FIELDS, "scenario")
        identifier = scenario.get("id")
        if not isinstance(identifier, str) or SCENARIO_RE.fullmatch(identifier) is None:
            raise SandboxManifestError(
                "each scenario requires a lowercase underscore-delimited id"
            )
        identifiers.append(identifier)
        for field in ("description", "cleanup"):
            value = scenario.get(field)
            if not isinstance(value, str) or not value.strip():
                raise SandboxManifestError(
                    f"scenario {identifier} requires a nonempty {field}"
                )
        assertions = scenario.get("required_assertions")
        if (
            not isinstance(assertions, list)
            or not assertions
            or any(
                not isinstance(value, str) or not value.strip() for value in assertions
            )
            or len(set(assertions)) != len(assertions)
        ):
            raise SandboxManifestError(
                f"scenario {identifier} requires unique nonempty assertions"
            )
        if scenario.get("writes_remote") is not True:
            raise SandboxManifestError(
                f"scenario {identifier} must explicitly declare writes_remote true"
            )
    if tuple(identifiers) != REQUIRED_SCENARIOS:
        raise SandboxManifestError(
            "scenario inventory must exactly match the reviewed ordered suite"
        )


def load_manifest(path: Path) -> dict[str, object]:
    """Load and fully validate the reviewed remote-write security contract."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxManifestError(
            f"sandbox manifest could not be loaded: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SandboxManifestError("sandbox manifest must be a JSON object")
    _require_exact_fields(payload, MANIFEST_FIELDS, "sandbox manifest")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SandboxManifestError(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("requires_private_repository") is not True:
        raise SandboxManifestError("requires_private_repository must be true")
    _validate_repository_contract(payload)
    _validate_scenarios(payload.get("scenarios"))
    return payload


def _authorize(
    *,
    payload: Mapping[str, object],
    repository: str,
    confirmation: str | None,
    repository_probe: Callable[[str], SandboxRepositoryEvidence],
) -> None:
    """Prove operator intent and exact live sandbox identity before execution."""

    if confirmation != WRITE_CONFIRMATION:
        raise SandboxAuthorizationError(
            "private sandbox writes require the exact reviewed confirmation literal"
        )
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise SandboxAuthorizationError("sandbox repository must be OWNER/REPOSITORY")
    allowlist = _repository_allowlist(payload)
    if repository not in allowlist:
        raise SandboxAuthorizationError(
            "sandbox repository is absent from the reviewed repository allowlist"
        )
    try:
        evidence = repository_probe(repository)
    except Exception as exc:
        raise SandboxAuthorizationError(
            "private repository evidence could not be proven"
        ) from exc
    if not isinstance(evidence, SandboxRepositoryEvidence):
        raise SandboxAuthorizationError(
            "repository evidence must use SandboxRepositoryEvidence"
        )
    if (
        isinstance(evidence.repository_id, bool)
        or not isinstance(evidence.repository_id, int)
        or evidence.repository_id <= 0
    ):
        raise SandboxAuthorizationError("live repository ID must be a positive integer")
    production_ids = _production_repository_ids(payload)
    if evidence.repository_id in production_ids:
        raise SandboxAuthorizationError(
            "listed production repository ID cannot be used as a sandbox"
        )
    if evidence.repository_id != allowlist[repository]:
        raise SandboxAuthorizationError(
            "live repository ID does not match the reviewed allowlist binding"
        )
    if evidence.visibility != PRIVATE_VISIBILITY:
        raise SandboxAuthorizationError(
            "repository visibility must be the exact PRIVATE literal"
        )
    if (
        evidence.sentinel_name != SENTINEL_NAME
        or evidence.sentinel_value != SENTINEL_VALUE
    ):
        raise SandboxAuthorizationError(
            "live repository sentinel does not match the exact reviewed name and value"
        )


def run_sandbox(
    manifest_path: Path,
    *,
    repository: str,
    confirmation: str | None,
    repository_probe: Callable[[str], SandboxRepositoryEvidence],
    scenario_executor: Callable[[str, str, Mapping[str, object]], Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Run injected scenarios only after every reviewed gate has succeeded."""

    payload = load_manifest(manifest_path)
    _authorize(
        payload=payload,
        repository=repository,
        confirmation=confirmation,
        repository_probe=repository_probe,
    )
    scenarios = payload["scenarios"]
    prefix = payload["sandbox_branch_prefix"]
    # SECURITY INVARIANT: no executor reference may move above this line. The
    # complete manifest and all live identity evidence must fail closed first.
    return tuple(
        scenario_executor(repository, f"{prefix}{scenario['id']}", scenario)
        for scenario in scenarios
    )


def preparation_report(manifest_path: Path) -> dict[str, object]:
    """Return all reviewed gates without resolving credentials or GitHub state."""

    payload = load_manifest(manifest_path)
    scenarios = payload["scenarios"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared_for_human_review",
        "remote_execution_enabled": False,
        "requires_private_repository": True,
        "required_repository_visibility": payload["required_repository_visibility"],
        "repository_allowlist": payload["repository_allowlist"],
        "production_repository_ids": payload["production_repository_ids"],
        "repository_sentinel": payload["repository_sentinel"],
        "sandbox_branch_prefix": payload["sandbox_branch_prefix"],
        "required_confirmation": WRITE_CONFIRMATION,
        "scenarios": [scenario["id"] for scenario in scenarios],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the prepare-only command-line interface."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate and print the private-sandbox plan. "
            "This CLI contains no remote scenario executor."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the scenario suite locally without performing remote writes."""

    args = build_parser().parse_args(argv)
    try:
        report = preparation_report(args.manifest)
    except SandboxManifestError as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
