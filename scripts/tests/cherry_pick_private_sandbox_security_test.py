# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Lock down the private-sandbox remote-write authorization boundary."""

from __future__ import annotations

import json
import socket
import subprocess

import pytest

from scripts import run_cherry_pick_private_sandbox as sandbox

SANDBOX_REPOSITORY = "ROCm/cherry-pick-private-sandbox"
SANDBOX_REPOSITORY_ID = 900_000_001
# These reviewed immutable IDs are the production repositories the harness must
# never accept as its private sandbox, even if a name is reused or redirected.
PRODUCTION_REPOSITORY_IDS = (
    765_605_091,  # ROCm/TheRock
    962_090_208,  # ROCm/rocm-systems
    971_570_345,  # ROCm/rocm-libraries
    1_071_689_640,  # ROCm/rockrel
)
EXPECTED_SCENARIOS = (
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


def _scenario(identifier: str) -> dict[str, object]:
    """Return one complete reviewed scenario record for manifest tests."""

    return {
        "id": identifier,
        "description": f"Exercise {identifier} in the private sandbox.",
        "required_assertions": [f"{identifier}_verified"],
        "cleanup": f"Remove the sandbox artifacts for {identifier}.",
        "writes_remote": True,
    }


def _manifest() -> dict[str, object]:
    """Return a fully authorized synthetic manifest with no remote dependency."""

    return {
        "schema_version": sandbox.SCHEMA_VERSION,
        "requires_private_repository": True,
        "required_repository_visibility": "PRIVATE",
        "repository_allowlist": {SANDBOX_REPOSITORY: SANDBOX_REPOSITORY_ID},
        "production_repository_ids": list(PRODUCTION_REPOSITORY_IDS),
        "repository_sentinel": {
            "name": "ROCM_CHERRY_PICK_PRIVATE_SANDBOX",
            "value": "REVIEWED_REMOTE_WRITES_ONLY",
        },
        "sandbox_branch_prefix": "sandbox/cherry-pick/",
        "write_confirmation": sandbox.WRITE_CONFIRMATION,
        "scenarios": [_scenario(identifier) for identifier in EXPECTED_SCENARIOS],
    }


def _write_manifest(tmp_path, payload: dict[str, object]):
    """Write one test-only manifest and return its path."""

    path = tmp_path / "private-sandbox.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _evidence(**overrides):
    """Build exact local repository evidence with optional unsafe mutations."""

    values = {
        "repository_id": SANDBOX_REPOSITORY_ID,
        "visibility": "PRIVATE",
        "sentinel_name": "ROCM_CHERRY_PICK_PRIVATE_SANDBOX",
        "sentinel_value": "REVIEWED_REMOTE_WRITES_ONLY",
    }
    values.update(overrides)
    return sandbox.SandboxRepositoryEvidence(**values)


def test_default_manifest_records_the_complete_reviewed_contract():
    """Keep every identity and scenario gate explicit in the shipped manifest."""

    payload = sandbox.load_manifest(sandbox.DEFAULT_MANIFEST)

    assert payload["required_repository_visibility"] == "PRIVATE"
    assert payload["repository_allowlist"] == {
        SANDBOX_REPOSITORY: SANDBOX_REPOSITORY_ID
    }
    assert payload["production_repository_ids"] == list(PRODUCTION_REPOSITORY_IDS)
    assert payload["repository_sentinel"] == {
        "name": "ROCM_CHERRY_PICK_PRIVATE_SANDBOX",
        "value": "REVIEWED_REMOTE_WRITES_ONLY",
    }
    assert payload["sandbox_branch_prefix"] == "sandbox/cherry-pick/"
    assert tuple(item["id"] for item in payload["scenarios"]) == EXPECTED_SCENARIOS


@pytest.mark.parametrize(
    ("field", "unsafe_value", "reason"),
    [
        ("repository_allowlist", {}, "allowlist"),
        (
            "repository_allowlist",
            {SANDBOX_REPOSITORY: True},
            "repository ID",
        ),
        ("production_repository_ids", [], "production repository IDs"),
        (
            "production_repository_ids",
            [SANDBOX_REPOSITORY_ID, *PRODUCTION_REPOSITORY_IDS],
            "production repository ID",
        ),
        ("required_repository_visibility", "private", "visibility"),
        (
            "repository_sentinel",
            {"name": "WRONG", "value": "REVIEWED_REMOTE_WRITES_ONLY"},
            "sentinel",
        ),
        (
            "repository_sentinel",
            {"name": "ROCM_CHERRY_PICK_PRIVATE_SANDBOX", "value": "WRONG"},
            "sentinel",
        ),
        ("sandbox_branch_prefix", "release/", "branch prefix"),
        ("write_confirmation", "close-enough", "confirmation"),
    ],
)
def test_manifest_rejects_missing_or_inexact_security_gates(
    tmp_path, field, unsafe_value, reason
):
    """Reject reviewed-contract drift before repository evidence is requested."""

    payload = _manifest()
    payload[field] = unsafe_value

    with pytest.raises(sandbox.SandboxManifestError, match=reason):
        sandbox.load_manifest(_write_manifest(tmp_path, payload))


@pytest.mark.parametrize(
    "required_scenario",
    ["stale_evidence_rejection", "conflict_handling", "dependency_ordering"],
)
def test_manifest_requires_each_new_failure_mode(tmp_path, required_scenario):
    """Keep stale evidence, conflicts, and dependency ordering in qualification."""

    payload = _manifest()
    payload["scenarios"] = [
        item for item in payload["scenarios"] if item["id"] != required_scenario
    ]

    with pytest.raises(sandbox.SandboxManifestError, match="scenario inventory"):
        sandbox.load_manifest(_write_manifest(tmp_path, payload))


def test_executor_receives_only_fully_gated_sandbox_branches(tmp_path):
    """Generate every executor branch inside the reviewed sandbox namespace."""

    manifest = _write_manifest(tmp_path, _manifest())
    probed = []
    executed = []

    def probe(repository):
        probed.append(repository)
        return _evidence()

    def execute(repository, branch, scenario):
        executed.append((repository, branch, scenario["id"]))
        return {"scenario": scenario["id"], "branch": branch}

    results = sandbox.run_sandbox(
        manifest,
        repository=SANDBOX_REPOSITORY,
        confirmation=sandbox.WRITE_CONFIRMATION,
        repository_probe=probe,
        scenario_executor=execute,
    )

    assert probed == [SANDBOX_REPOSITORY]
    assert len(results) == len(EXPECTED_SCENARIOS)
    assert executed == [
        (
            SANDBOX_REPOSITORY,
            f"sandbox/cherry-pick/{identifier}",
            identifier,
        )
        for identifier in EXPECTED_SCENARIOS
    ]


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("confirmation", "confirmation"),
        ("repository", "allowlist"),
        ("repository_id", "repository ID"),
        ("boolean_repository_id", "repository ID"),
        ("production_repository_id", "production repository ID"),
        ("visibility_lowercase", "visibility"),
        ("visibility_whitespace", "visibility"),
        ("sentinel_name", "sentinel"),
        ("sentinel_value", "sentinel"),
        ("probe_failure", "evidence"),
    ],
)
def test_every_authorization_gate_precedes_the_executor(tmp_path, case, reason):
    """Prove no injected executor runs after any authorization-gate failure."""

    payload = _manifest()
    repository = SANDBOX_REPOSITORY
    confirmation = sandbox.WRITE_CONFIRMATION
    evidence = _evidence()
    if case == "confirmation":
        confirmation = "not-the-reviewed-literal"
    elif case == "repository":
        repository = "someone/unreviewed"
    elif case == "repository_id":
        evidence = _evidence(repository_id=SANDBOX_REPOSITORY_ID + 1)
    elif case == "boolean_repository_id":
        payload["repository_allowlist"] = {SANDBOX_REPOSITORY: 1}
        evidence = _evidence(repository_id=True)
    elif case == "production_repository_id":
        evidence = _evidence(repository_id=PRODUCTION_REPOSITORY_IDS[0])
    elif case == "visibility_lowercase":
        evidence = _evidence(visibility="private")
    elif case == "visibility_whitespace":
        evidence = _evidence(visibility=" PRIVATE ")
    elif case == "sentinel_name":
        evidence = _evidence(sentinel_name="WRONG")
    elif case == "sentinel_value":
        evidence = _evidence(sentinel_value="WRONG")

    executed = []

    def probe(_repository):
        if case == "probe_failure":
            raise RuntimeError("offline probe failed")
        return evidence

    with pytest.raises(
        (sandbox.SandboxAuthorizationError, sandbox.SandboxManifestError),
        match=reason,
    ):
        sandbox.run_sandbox(
            _write_manifest(tmp_path, payload),
            repository=repository,
            confirmation=confirmation,
            repository_probe=probe,
            scenario_executor=lambda *args: executed.append(args),
        )

    assert executed == []


def test_public_cli_remains_local_prepare_only(monkeypatch, tmp_path, capsys):
    """Prevent the public CLI from gaining an executor or a network call path."""

    manifest = _write_manifest(tmp_path, _manifest())

    def forbidden(*_args, **_kwargs):
        pytest.fail("prepare-only CLI attempted a remote or executor operation")

    monkeypatch.setattr(sandbox, "run_sandbox", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    assert sandbox.main(["--manifest", str(manifest)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "prepared_for_human_review"
    assert report["remote_execution_enabled"] is False
    assert report["repository_allowlist"] == {SANDBOX_REPOSITORY: SANDBOX_REPOSITORY_ID}
    assert report["production_repository_ids"] == list(PRODUCTION_REPOSITORY_IDS)
    assert report["required_repository_visibility"] == "PRIVATE"
    assert report["repository_sentinel"] == {
        "name": "ROCM_CHERRY_PICK_PRIVATE_SANDBOX",
        "value": "REVIEWED_REMOTE_WRITES_ONLY",
    }
    assert report["sandbox_branch_prefix"] == "sandbox/cherry-pick/"
    assert tuple(report["scenarios"]) == EXPECTED_SCENARIOS
