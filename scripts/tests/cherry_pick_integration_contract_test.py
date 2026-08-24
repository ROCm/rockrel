# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Contracts for the local, five-repository cherry-pick integration checker."""

from __future__ import annotations

import importlib
import json
import socket
from collections.abc import Callable
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "config/cherry-pick-integration.json"
REVISION = "c53e703568fe41129abf7139f018ac920bca9c59"
AUDIENCE = "api://developer-central.amd.com/rocm-cherry-pick-config"
ENDPOINT = "https://developer-central.amd.com/api/v1/cherry-pick/config"
WORKFLOW = "ROCm/rockrel/.github/workflows/cherry_pick.yml"
REPOSITORY_OWNER = "ROCm"
REPOSITORY_OWNER_ID = "21157610"
CALLERS = ("ROCm/TheRock", "ROCm/rocm-systems", "ROCm/rocm-libraries")
REPOSITORY_IDS = {
    "ROCm/TheRock": "765605091",
    "ROCm/rocm-systems": "962090208",
    "ROCm/rocm-libraries": "971570345",
    "ROCm/rockrel": "1071689640",
}
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


def caller_policies_value() -> list[dict[str, object]]:
    """Return the five reviewed caller tuples in their canonical order."""

    reusable_ref = f"{WORKFLOW}@{REVISION}"
    return [
        {
            "repository": "ROCm/TheRock",
            "repository_owner_id": REPOSITORY_OWNER_ID,
            "repository_id": REPOSITORY_IDS["ROCm/TheRock"],
            "refs": ["refs/heads/main"],
            "events": ["pull_request_target"],
            "workflow": {
                "kind": "reusable",
                "ref": reusable_ref,
                "sha": REVISION,
            },
        },
        {
            "repository": "ROCm/rocm-systems",
            "repository_owner_id": REPOSITORY_OWNER_ID,
            "repository_id": REPOSITORY_IDS["ROCm/rocm-systems"],
            "refs": ["refs/heads/develop"],
            "events": ["pull_request_target"],
            "workflow": {
                "kind": "reusable",
                "ref": reusable_ref,
                "sha": REVISION,
            },
        },
        {
            "repository": "ROCm/rocm-libraries",
            "repository_owner_id": REPOSITORY_OWNER_ID,
            "repository_id": REPOSITORY_IDS["ROCm/rocm-libraries"],
            "refs": ["refs/heads/develop"],
            "events": ["pull_request_target"],
            "workflow": {
                "kind": "reusable",
                "ref": reusable_ref,
                "sha": REVISION,
            },
        },
        {
            "repository": "ROCm/rockrel",
            "repository_owner_id": REPOSITORY_OWNER_ID,
            "repository_id": REPOSITORY_IDS["ROCm/rockrel"],
            "refs": ["refs/heads/main"],
            "events": ["schedule", "workflow_dispatch"],
            "workflow": {
                "kind": "direct",
                "ref": (
                    "ROCm/rockrel/.github/workflows/"
                    "cherry_pick_reconcile.yml@refs/heads/main"
                ),
                "sha": REVISION,
            },
        },
        {
            "repository": "ROCm/rockrel",
            "repository_owner_id": REPOSITORY_OWNER_ID,
            "repository_id": REPOSITORY_IDS["ROCm/rockrel"],
            "refs": ["refs/heads/main"],
            "events": ["workflow_dispatch"],
            "workflow": {
                "kind": "direct",
                "ref": f"{WORKFLOW}@refs/heads/main",
                "sha": REVISION,
            },
        },
    ]


def release_hub_callers_value() -> list[dict[str, object]]:
    """Project reviewed snake-case caller identities into Release Hub v5 JSON."""

    return [
        {
            "repository": caller["repository"],
            "repositoryOwnerId": caller["repository_owner_id"],
            "repositoryId": caller["repository_id"],
            "refs": caller["refs"],
            "events": caller["events"],
            "workflow": caller["workflow"],
        }
        for caller in caller_policies_value()
    ]


def checker_module():
    """Load the implementation lazily so every contract produces useful red output."""

    try:
        return importlib.import_module("scripts.check_cherry_pick_integration")
    except ModuleNotFoundError:
        pytest.fail(
            "scripts/check_cherry_pick_integration.py does not exist; "
            "implement the local five-repository contract checker"
        )


def manifest_value() -> dict[str, object]:
    """Return the one reviewed integration contract used by all fixture repos."""

    return {
        "schema_version": "rocm-cherry-pick-integration.v2",
        "config_endpoint": ENDPOINT,
        "oidc_issuer": "https://token.actions.githubusercontent.com",
        "oidc_audience": AUDIENCE,
        "repository_owner": REPOSITORY_OWNER,
        "reusable_workflow": WORKFLOW,
        "rockrel_revision": REVISION,
        "callers": caller_policies_value(),
    }


def write_text(path: Path, value: str) -> None:
    """Create a fixture file and all of its parent directories."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def build_checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a minimal but structurally faithful five-repository checkout."""

    workspace_root = tmp_path / "label-driven-cherrypick-automation"
    rockrel_root = workspace_root / "rockrel"
    release_hub_root = tmp_path / "rocm-release-hub"
    manifest = rockrel_root / "config/cherry-pick-integration.json"
    write_text(manifest, json.dumps(manifest_value(), indent=2) + "\n")
    write_text(
        rockrel_root / "scripts/cherry_pick/control_plane.py",
        f'ACTIONS_OIDC_AUDIENCE = "{AUDIENCE}"\n',
    )
    write_text(
        rockrel_root / ".github/workflows/cherry_pick.yml",
        (
            "# The workflow requests an exact, non-HTTP API audience.\n"
            f"# Audience: {AUDIENCE}\n"
            f"# Endpoint: {ENDPOINT}\n"
        ),
    )

    release_config = {
        "schemaVersion": "release-trains.v5",
        "automation": {
            "cherryPick": {
                "githubActionsOidc": {
                    "issuer": "https://token.actions.githubusercontent.com",
                    "audience": AUDIENCE,
                    "repositoryOwner": REPOSITORY_OWNER,
                    "callers": release_hub_callers_value(),
                }
            }
        },
    }
    write_text(
        release_hub_root / "config/release-trains.json",
        json.dumps(release_config, indent=2) + "\n",
    )
    write_text(
        release_hub_root / "lambdas/query-proxy/src/server.ts",
        (
            f'const cherryPickConfigRoute = "{ENDPOINT.removeprefix("https://developer-central.amd.com")}";\n'
            "verifyGitHubActionsOidc(token, {\n"
            "  issuer: oidc.issuer,\n"
            "  audience: oidc.audience,\n"
            "  repository_owner: oidc.repositoryOwner,\n"
            "  callers: oidc.callers.map((caller) => ({\n"
            "    repository: caller.repository,\n"
            "    repository_owner_id: caller.repositoryOwnerId,\n"
            "    repository_id: caller.repositoryId,\n"
            "    refs: caller.refs,\n"
            "    events: caller.events,\n"
            "    workflow: {\n"
            "      kind: caller.workflow.kind,\n"
            "      ref: caller.workflow.ref,\n"
            "      sha: caller.workflow.sha\n"
            "    }\n"
            "  }))\n"
            "});\n"
        ),
    )
    write_text(
        release_hub_root / "lambdas/query-proxy/src/githubActionsOidc.ts",
        (
            'const repository = stringClaim(claims.repository, "repository");\n'
            "const repositoryOwner = stringClaim(claims.repository_owner, "
            '"repository_owner");\n'
            'const ref = stringClaim(claims.ref, "ref");\n'
            "const eventName = eventNameClaim(claims.event_name);\n"
            "const repositoryCallers = policy.callers.filter((caller) => "
            "caller.repository === repository);\n"
            "const refCallers = repositoryCallers.filter((caller) => "
            "caller.refs.includes(ref));\n"
            "const eventCallers = refCallers.filter((caller) => "
            "caller.events.includes(eventName));\n"
            "const repositoryOwnerId = idClaim(claims.repository_owner_id, "
            '"repository_owner_id");\n'
            'const repositoryId = idClaim(claims.repository_id, "repository_id");\n'
            "if (repositoryOwnerId !== caller.repository_owner_id) throw new Error();\n"
            "if (repositoryId !== caller.repository_id) throw new Error();\n"
            'const refClaim = kind === "reusable" ? "job_workflow_ref" : "workflow_ref";\n'
            'const shaClaim = kind === "reusable" ? "job_workflow_sha" : "workflow_sha";\n'
            "const workflow = stringClaim(claims[refClaim], refClaim);\n"
            "const workflowSha = stringClaim(claims[shaClaim], shaClaim);\n"
            "const refMatches = callers.filter((caller) => "
            "caller.workflow.ref === workflow);\n"
            "const caller = refMatches.find((candidate) => "
            "candidate.workflow.sha === workflowSha);\n"
        ),
    )

    for caller in CALLERS:
        checkout_name = caller.split("/", 1)[1]
        write_text(
            workspace_root
            / checkout_name
            / ".github/workflows/cherry_pick_request.yml",
            (
                "jobs:\n"
                "  process:\n"
                f"    uses: {WORKFLOW}@{REVISION}\n"
                "    with:\n"
                f"      automation_ref: {REVISION}\n"
            ),
        )
    return workspace_root, release_hub_root, manifest


def validate_checkout(
    workspace_root: Path,
    release_hub_root: Path,
    manifest: Path,
) -> dict[str, object]:
    """Call the intended pure validation API with explicit local roots."""

    module = checker_module()
    return module.validate_local_checkout(
        manifest_path=manifest,
        workspace_root=workspace_root,
        release_hub_root=release_hub_root,
    )


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn any accidental network access into an immediate test failure."""

    def fail(*_args, **_kwargs):
        pytest.fail("the integration checker must read local files only")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket.socket, "connect", fail)


def test_reviewed_manifest_is_complete_strict_and_immutable():
    module = checker_module()

    contract = module.load_manifest(MANIFEST)

    assert contract.to_dict() == manifest_value()
    assert len(contract.rockrel_revision) == 40
    assert set(contract.rockrel_revision) <= set("0123456789abcdef")
    assert contract.oidc_audience.startswith("api://")
    assert contract.repository_owner == REPOSITORY_OWNER
    assert contract.reusable_workflow == WORKFLOW
    assert "@" not in contract.reusable_workflow
    assert [caller.to_dict() for caller in contract.callers] == caller_policies_value()


def test_valid_five_repo_checkout_passes_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    forbid_network(monkeypatch)

    report = validate_checkout(workspace_root, release_hub_root, manifest)

    assert report == {
        "schema_version": "rocm-cherry-pick-integration-report.v1",
        "valid": True,
        "manifest": str(manifest.resolve()),
        "checked_repositories": [
            "ROCm/rockrel",
            "ROCm/Release-Hub",
            *CALLERS,
        ],
        "errors": [],
    }


@pytest.mark.parametrize(
    "relative_path,fragment,expected_contract",
    [
        *[
            (Path("lambdas/query-proxy/src/server.ts"), fragment, "server_mapping")
            for fragment in SERVER_MAPPING_FRAGMENTS
        ],
        *[
            (
                Path("lambdas/query-proxy/src/githubActionsOidc.ts"),
                fragment,
                "verifier_mapping",
            )
            for fragment in VERIFIER_MAPPING_FRAGMENTS
        ],
    ],
)
def test_every_release_hub_policy_mapping_is_required_structurally(
    tmp_path: Path,
    relative_path: Path,
    fragment: str,
    expected_contract: str,
):
    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    path = release_hub_root / relative_path
    source = path.read_text()
    assert fragment in source
    path.write_text(source.replace(fragment, "removed_mapping", 1))

    report = validate_checkout(workspace_root, release_hub_root, manifest)

    assert report["valid"] is False
    serialized = json.dumps(report["errors"], sort_keys=True)
    assert path.name in serialized
    assert expected_contract in serialized


def change_rockrel_audience(workspace: Path, _release_hub: Path) -> None:
    path = workspace / "rockrel/scripts/cherry_pick/control_plane.py"
    path.write_text(path.read_text().replace(AUDIENCE, "api://wrong-audience"))


def change_workflow_endpoint(workspace: Path, _release_hub: Path) -> None:
    path = workspace / "rockrel/.github/workflows/cherry_pick.yml"
    path.write_text(path.read_text().replace(ENDPOINT, f"{ENDPOINT}/wrong"))


def change_release_hub_endpoint(workspace: Path, release_hub: Path) -> None:
    del workspace
    path = release_hub / "lambdas/query-proxy/src/server.ts"
    path.write_text(path.read_text().replace("/api/v1/cherry-pick/config", "/wrong"))


def change_release_hub_audience(workspace: Path, release_hub: Path) -> None:
    del workspace
    path = release_hub / "config/release-trains.json"
    path.write_text(path.read_text().replace(AUDIENCE, ENDPOINT))


def change_release_hub_oidc(
    release_hub: Path,
    change: Callable[[dict[str, object]], None],
) -> None:
    """Apply one controlled mutation to the fixture's v5 OIDC object."""

    path = release_hub / "config/release-trains.json"
    value = json.loads(path.read_text())
    oidc = value["automation"]["cherryPick"]["githubActionsOidc"]
    change(oidc)
    path.write_text(json.dumps(value))


def change_release_hub_workflow_ref(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][0]["workflow"].update(
            ref=f"ROCm/other/.github/workflows/cherry_pick.yml@{REVISION}"
        ),
    )


def change_release_hub_workflow_sha(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][0]["workflow"].update(sha="a" * 40),
    )


def change_release_hub_owner_id(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][0].update(repositoryOwnerId="999"),
    )


def change_release_hub_repository_id(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][1].update(repositoryId="999"),
    )


def change_release_hub_ref(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][2].update(refs=["refs/heads/main"]),
    )


def change_release_hub_event(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][0].update(events=["workflow_dispatch"]),
    )


def change_release_hub_workflow_kind(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][0]["workflow"].update(kind="direct"),
    )


def change_release_hub_direct_workflow(workspace: Path, release_hub: Path) -> None:
    del workspace
    change_release_hub_oidc(
        release_hub,
        lambda oidc: oidc["callers"][3]["workflow"].update(
            ref=f"{WORKFLOW}@refs/heads/main"
        ),
    )


def restore_legacy_release_hub_shape(workspace: Path, release_hub: Path) -> None:
    del workspace

    def change(oidc: dict[str, object]) -> None:
        oidc.pop("callers")
        oidc.update(
            repositories=list(CALLERS),
            refs=["refs/heads/main", "refs/heads/develop"],
            reusableWorkflow=f"{WORKFLOW}@{REVISION}",
            reusableWorkflowSha=REVISION,
        )

    change_release_hub_oidc(release_hub, change)


def remove_job_workflow_sha_policy(workspace: Path, release_hub: Path) -> None:
    del workspace
    path = release_hub / "lambdas/query-proxy/src/githubActionsOidc.ts"
    path.write_text(path.read_text().replace("job_workflow_sha", "ignored_sha_claim"))


def change_caller_uses(workspace: Path, _release_hub: Path) -> None:
    path = workspace / "rocm-systems/.github/workflows/cherry_pick_request.yml"
    path.write_text(
        path.read_text().replace(f"{WORKFLOW}@{REVISION}", f"{WORKFLOW}@{'b' * 40}")
    )


def change_caller_automation_ref(workspace: Path, _release_hub: Path) -> None:
    path = workspace / "rocm-libraries/.github/workflows/cherry_pick_request.yml"
    path.write_text(
        path.read_text().replace(
            f"automation_ref: {REVISION}", f"automation_ref: {'d' * 40}"
        )
    )


@pytest.mark.parametrize(
    "mutate,expected_path,expected_contract",
    [
        (change_rockrel_audience, "control_plane.py", "oidc_audience"),
        (change_workflow_endpoint, "cherry_pick.yml", "config_endpoint"),
        (change_release_hub_endpoint, "server.ts", "config_endpoint"),
        (change_release_hub_audience, "release-trains.json", "oidc_audience"),
        (change_release_hub_workflow_ref, "release-trains.json", "workflow_ref"),
        (change_release_hub_workflow_sha, "release-trains.json", "workflow_sha"),
        (change_release_hub_owner_id, "release-trains.json", "repository_owner_id"),
        (change_release_hub_repository_id, "release-trains.json", "repository_id"),
        (change_release_hub_ref, "release-trains.json", "refs"),
        (change_release_hub_event, "release-trains.json", "events"),
        (change_release_hub_workflow_kind, "release-trains.json", "workflow_kind"),
        (change_release_hub_direct_workflow, "release-trains.json", "workflow_ref"),
        (restore_legacy_release_hub_shape, "release-trains.json", "callers"),
        (remove_job_workflow_sha_policy, "githubActionsOidc.ts", "verifier_mapping"),
        (change_caller_uses, "rocm-systems", "uses"),
        (change_caller_automation_ref, "rocm-libraries", "automation_ref"),
    ],
)
def test_every_cross_repo_mismatch_fails_closed_with_actionable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[Path, Path], None],
    expected_path: str,
    expected_contract: str,
):
    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    mutate(workspace_root, release_hub_root)
    forbid_network(monkeypatch)

    report = validate_checkout(workspace_root, release_hub_root, manifest)

    assert report["valid"] is False
    assert report["errors"]
    serialized = json.dumps(report["errors"], sort_keys=True)
    assert expected_path in serialized
    assert expected_contract in serialized
    assert (
        REVISION not in serialized
    ), "diagnostics must not dump the complete trust anchor"


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda value: value.pop("oidc_audience"), "oidc_audience"),
        (lambda value: value.update(extra="unsupported"), "extra"),
        (lambda value: value.update(rockrel_revision="main"), "rockrel_revision"),
        (lambda value: value.update(rockrel_revision="A" * 40), "rockrel_revision"),
        (lambda value: value.update(oidc_audience=ENDPOINT), "oidc_audience"),
        (
            lambda value: value.update(reusable_workflow=f"{WORKFLOW}@{REVISION}"),
            "reusable_workflow",
        ),
        (
            lambda value: value.update(repository_owner="Other"),
            "repository_owner",
        ),
        (lambda value: value.update(callers=[]), "callers"),
        (lambda value: value.update(authorized_callers=list(CALLERS)), "extra"),
    ],
)
def test_manifest_rejects_ambiguous_or_mutable_contracts_before_checkout_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[dict[str, object]], object],
    reason: str,
):
    module = checker_module()
    value = manifest_value()
    change(value)
    manifest = tmp_path / "contract.json"
    manifest.write_text(json.dumps(value))

    original_read_text = Path.read_text

    def read_manifest_only(path: Path, *args, **kwargs):
        assert path == manifest, "invalid manifests must fail before checkout reads"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_manifest_only)
    with pytest.raises(module.IntegrationContractError, match=reason):
        module.load_manifest(manifest)


def test_cli_requires_explicit_local_roots_and_emits_deterministic_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    module = checker_module()
    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    forbid_network(monkeypatch)
    monkeypatch.chdir(workspace_root)

    with pytest.raises(SystemExit) as missing_root:
        module.main(
            [
                "--manifest",
                str(manifest),
                "--release-hub-root",
                str(release_hub_root),
                "--format",
                "json",
            ]
        )
    assert missing_root.value.code == 2
    assert "--workspace-root" in capsys.readouterr().err

    arguments = [
        "--manifest",
        str(manifest),
        "--workspace-root",
        str(workspace_root),
        "--release-hub-root",
        str(release_hub_root),
        "--format",
        "json",
    ]
    assert module.main(arguments) == 0
    first = capsys.readouterr().out
    assert module.main(arguments) == 0
    second = capsys.readouterr().out
    assert first == second
    assert json.loads(first)["valid"] is True


def test_cli_returns_one_for_contract_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    module = checker_module()
    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    change_caller_automation_ref(workspace_root, release_hub_root)
    forbid_network(monkeypatch)

    assert (
        module.main(
            [
                "--manifest",
                str(manifest),
                "--workspace-root",
                str(workspace_root),
                "--release-hub-root",
                str(release_hub_root),
                "--format",
                "json",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_manifest_loader_and_text_cli_fail_closed_for_malformed_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Cover loader failures and the human-readable fail-closed CLI path."""

    module = checker_module()
    invalid_inputs = {
        "missing.json": None,
        "invalid.json": "{",
        "array.json": "[]",
    }
    for name, content in invalid_inputs.items():
        path = tmp_path / name
        if content is not None:
            path.write_text(content)
        with pytest.raises(module.IntegrationContractError, match="manifest"):
            module.load_manifest(path)

    wrong_callers = manifest_value()
    wrong_callers["callers"] = caller_policies_value()[:-1]
    manifest = tmp_path / "wrong-callers.json"
    manifest.write_text(json.dumps(wrong_callers))
    with pytest.raises(module.IntegrationContractError, match="callers"):
        module.load_manifest(manifest)

    assert (
        module.main(
            [
                "--manifest",
                str(tmp_path / "missing.json"),
                "--workspace-root",
                str(tmp_path),
                "--release-hub-root",
                str(tmp_path),
                "--format",
                "text",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert output.startswith("invalid\n")
    assert "manifest" in output


def test_checker_reports_all_missing_local_contract_files(tmp_path: Path):
    """Exercise every missing-file branch without attempting recovery or I/O."""

    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    missing = [
        workspace_root / "rockrel/scripts/cherry_pick/control_plane.py",
        workspace_root / "rockrel/.github/workflows/cherry_pick.yml",
        workspace_root / "TheRock/.github/workflows/cherry_pick_request.yml",
        release_hub_root / "lambdas/query-proxy/src/server.ts",
        release_hub_root / "lambdas/query-proxy/src/githubActionsOidc.ts",
    ]
    for path in missing:
        path.unlink()
    (release_hub_root / "config/release-trains.json").write_text("{")

    report = validate_checkout(workspace_root, release_hub_root, manifest)

    assert report["valid"] is False
    diagnostics = json.dumps(report["errors"], sort_keys=True)
    for path in missing:
        assert path.name in diagnostics
    assert "release-trains.json" in diagnostics


@pytest.mark.parametrize("release_config", ["[]", "{}"])
def test_checker_rejects_nonobject_or_incomplete_release_policy(
    tmp_path: Path,
    release_config: str,
):
    """Reject JSON values that cannot supply the complete nested OIDC policy."""

    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    path = release_hub_root / "config/release-trains.json"
    path.write_text(release_config)

    report = validate_checkout(workspace_root, release_hub_root, manifest)

    assert report["valid"] is False
    assert "release-trains.json" in json.dumps(report["errors"], sort_keys=True)


def test_text_cli_reports_a_valid_checkout_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Cover the concise operator output for a fully matching local checkout."""

    module = checker_module()
    workspace_root, release_hub_root, manifest = build_checkout(tmp_path)
    forbid_network(monkeypatch)

    assert (
        module.main(
            [
                "--manifest",
                str(manifest),
                "--workspace-root",
                str(workspace_root),
                "--release-hub-root",
                str(release_hub_root),
                "--format",
                "text",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "valid\n"
