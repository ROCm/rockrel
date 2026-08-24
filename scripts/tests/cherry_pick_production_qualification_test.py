# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Specify Mermaid rendering and private-GitHub qualification contracts."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DESIGN = ROOT / "docs/cherry-pick-automation/technical-design.md"
RENDERER = ROOT / "scripts/render_cherry_pick_mermaid.py"
SANDBOX_MANIFEST = ROOT / "config/cherry-pick-private-sandbox.json"
CONFIRMATION = "I_UNDERSTAND_THIS_WRITES_TO_A_PRIVATE_GITHUB_SANDBOX"
MERMAID_IMAGE = (
    "ghcr.io/mermaid-js/mermaid-cli/mermaid-cli@"
    "sha256:e7fc5c569c039c7d663e875ac0fe70828910f686b9bbee2d064f7e8a096d49f8"
)
REQUIRED_SCENARIOS = {
    "oidc_config": {
        "oidc_token_requested",
        "configuration_authorized",
        "exact_workflow_sha_verified",
    },
    "installation_token_exchange": {
        "installation_token_narrowed",
        "repository_scope_proven",
        "token_expiry_recorded",
    },
    "protected_branches": {
        "target_branch_not_mutated",
        "automation_branch_used",
        "target_protection_observed",
    },
    "draft_only": {
        "pull_request_created",
        "pull_request_is_draft",
        "commands_recorded_in_body",
    },
    "partial_recovery": {
        "existing_branch_reused",
        "draft_pull_request_created_once",
        "recovery_evidence_preserved",
    },
    "duplicate_delivery": {
        "one_logical_result",
        "no_duplicate_branch_or_pull_request",
        "authorization_identity_unchanged",
    },
    "branch_protection_denial": {
        "permission_denial_is_fail_closed",
        "no_target_branch_write",
        "no_credential_fallback",
    },
    "stale_evidence_rejection": {
        "stale_plan_rejected",
        "fresh_evidence_required",
        "no_remote_write",
    },
    "conflict_handling": {
        "conflict_reported",
        "conflicted_paths_preserved",
        "no_remote_artifact_created",
    },
    "dependency_ordering": {
        "dependencies_topologically_ordered",
        "prerequisites_precede_source",
        "dependency_gap_fails_closed",
    },
}


def _load(path, name):
    """Load a local script only after its required path has been asserted."""

    assert path.is_file(), f"missing required qualification tool: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _diagram(title, description):
    """Build one fenced Mermaid diagram with optional accessibility metadata."""

    lines = ["graph TD"]
    if title is not None:
        lines.append(f"  accTitle: {title}")
    if description is not None:
        lines.append(f"  accDescr: {description}")
    lines.append("  A --> B")
    fence = chr(96) * 3
    body = "\n".join(lines)
    return f"{fence}mermaid\n{body}\n{fence}\n"


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        (_diagram(None, "Description"), "accTitle"),
        (_diagram("Title", None), "accDescr"),
        (
            _diagram("Duplicate title", "First")
            + _diagram("Duplicate title", "Second"),
            "duplicate",
        ),
    ],
)
def test_mermaid_accessibility_metadata_is_complete_and_unique(markdown, message):
    """Reject inaccessible or ambiguously identified architecture diagrams."""

    module = _load(RENDERER, f"render_cherry_pick_mermaid_{message}")
    with pytest.raises(module.MermaidRenderError, match=message):
        module.extract_diagrams(markdown)


def test_source_and_bundled_markdown_local_links_resolve(tmp_path):
    """Reject missing relative targets in both source and packaged skill contexts."""

    module = _load(RENDERER, "render_cherry_pick_mermaid_links")
    broken = tmp_path / "broken.md"
    broken.write_text("[missing](does-not-exist.md)\n", encoding="utf-8")
    with pytest.raises(module.MermaidRenderError, match="broken local link"):
        module.validate_local_links(broken)

    documents = sorted((ROOT / "docs/cherry-pick-automation").glob("*.md"))
    documents += sorted((ROOT / "skills/rocm-cherry-pick").rglob("*.md"))
    assert documents
    for document in documents:
        module.validate_local_links(document)


def test_all_design_diagrams_render_through_pinned_networkless_fake_seam(tmp_path):
    """Render all fourteen diagrams without invoking Docker in this unit test."""

    diagrams = re.findall(
        r"```mermaid\n(.*?)```", DESIGN.read_text(encoding="utf-8"), re.DOTALL
    )
    assert len(diagrams) == 14
    module = _load(RENDERER, "render_cherry_pick_mermaid")
    assert module.MERMAID_CLI_IMAGE == MERMAID_IMAGE
    calls = []

    def fake_runner(command, **kwargs):
        """Record the hermetic Docker command and synthesize its SVG."""

        calls.append((tuple(command), kwargs))
        output = command[command.index("-o") + 1]
        volume = command[command.index("--volume") + 1]
        staging = Path(volume.removesuffix(":/work:rw"))
        assert staging.stat().st_mode & 0o7777 == 0o1733
        (staging / Path(output).name).write_text("<svg/>\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    outputs = module.render_document(DESIGN, tmp_path, runner=fake_runner)
    assert len(outputs) == len(calls) == 14
    for command, kwargs in calls:
        assert command[:3] == ("docker", "run", "--rm")
        assert "--network=none" in command
        assert "--read-only" in command
        assert MERMAID_IMAGE in command
        assert any(value.endswith(":/work:rw") for value in command)
        assert not any(value.startswith("--user=") for value in command)
        assert "--env=HOME=/tmp" in command
        assert kwargs.get("check") is False and kwargs.get("text") is True
    assert all(path.is_file() and path.suffix == ".svg" for path in outputs)
    assert not list(tmp_path.glob(".mermaid-render-*"))
    assert all((path.stat().st_mode & 0o777) != 0o733 for path in outputs)
    workflow = (ROOT / ".github/workflows/unit_tests.yml").read_text()
    assert "python scripts/render_cherry_pick_mermaid.py" in workflow


@pytest.mark.parametrize("failure", ["os_error", "nonzero", "missing", "symlink"])
def test_renderer_cleans_staging_and_partial_outputs_on_failure(tmp_path, failure):
    """Remove staged and published artifacts across every renderer failure class."""

    module = _load(RENDERER, f"render_cherry_pick_mermaid_{failure}")
    document = tmp_path / "design.md"
    document.write_text(
        _diagram("Failure test one", "First renderer cleanup test diagram")
        + _diagram("Failure test two", "Second renderer cleanup test diagram"),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    calls = 0

    def failing_runner(command, **_kwargs):
        """Complete once, then inject the selected deterministic failure."""

        nonlocal calls
        calls += 1
        volume = command[command.index("--volume") + 1]
        staging = Path(volume.removesuffix(":/work:rw"))
        output = staging / Path(command[command.index("-o") + 1]).name
        if calls == 1:
            output.write_text("<svg/>\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if failure == "os_error":
            raise OSError("container unavailable")
        if failure == "nonzero":
            return subprocess.CompletedProcess(
                command, 7, stdout="", stderr="invalid diagram"
            )
        if failure == "symlink":
            output.symlink_to(document)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(module.MermaidRenderError, match="diagram 2"):
        module.render_document(document, output_dir, runner=failing_runner)

    assert not list(output_dir.glob("*.svg"))
    assert not list(output_dir.glob(".mermaid-render-*"))


def test_operator_docs_name_the_single_train_authority_and_local_gates():
    """Keep source authority and all non-writing qualification commands explicit."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    requirements = (
        ROOT / "docs/cherry-pick-automation/product-requirements.md"
    ).read_text(encoding="utf-8")
    design = DESIGN.read_text(encoding="utf-8")
    implementation = (
        ROOT / "docs/cherry-pick-automation/implementation-report.md"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs/cherry-pick-automation/runbook.md").read_text(
        encoding="utf-8"
    )
    remote_todo = (
        ROOT / "docs/cherry-pick-automation/REMOTE_ACTIONS_TODO.md"
    ).read_text(encoding="utf-8")

    assert "fixture-only" in readme
    assert "ROCm Release Hub `config/release-trains.json`" in readme
    for text in (requirements, design):
        assert "ROCm Release Hub `config/release-trains.json`" in text
    for text in (requirements, design, implementation, remote_todo):
        normalized = " ".join(text.split())
        assert "production-parity sandbox executor adapter" in normalized
        assert "is not implemented" in normalized
    assert "scripts/render_cherry_pick_mermaid.py" in runbook
    assert "rendered 14 Mermaid diagrams" in runbook
    assert "scripts/check_cherry_pick_integration.py" in runbook
    assert "--workspace-root" in runbook
    assert "--release-hub-root" in runbook
    assert "--cov=scripts.check_cherry_pick_integration" in runbook
    assert "--critical-module scripts/check_cherry_pick_integration.py" in runbook
    assert "scripts/run_cherry_pick_private_sandbox.py" in runbook
    assert '"remote_execution_enabled": false' in runbook
    assert "prepare-only" in runbook
    for required in (
        "repository allowlist",
        "sandbox sentinel",
        "sandbox-only branch prefix",
        "production repository IDs",
        "prepare-only",
        "redacted evidence",
    ):
        assert required in remote_todo


def test_private_sandbox_manifest_covers_every_production_blocker():
    """Keep the reviewed private-staging scenarios explicit and complete."""

    assert SANDBOX_MANIFEST.is_file(), f"missing sandbox manifest: {SANDBOX_MANIFEST}"
    payload = json.loads(SANDBOX_MANIFEST.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "cherry-pick-private-sandbox.v1"
    assert payload["requires_private_repository"] is True
    assert payload["required_repository_visibility"] == "PRIVATE"
    assert payload["repository_allowlist"]
    assert payload["production_repository_ids"]
    assert not (
        set(payload["repository_allowlist"].values())
        & set(payload["production_repository_ids"])
    )
    assert payload["repository_sentinel"] == {
        "name": "ROCM_CHERRY_PICK_PRIVATE_SANDBOX",
        "value": "REVIEWED_REMOTE_WRITES_ONLY",
    }
    assert payload["sandbox_branch_prefix"] == "sandbox/cherry-pick/"
    assert payload["write_confirmation"] == CONFIRMATION
    scenarios = {scenario["id"]: scenario for scenario in payload["scenarios"]}
    assert set(scenarios) == set(REQUIRED_SCENARIOS)
    for scenario_id, required in REQUIRED_SCENARIOS.items():
        scenario = scenarios[scenario_id]
        assert scenario["writes_remote"] is True
        assert required <= set(scenario["required_assertions"])
        assert scenario["cleanup"]
