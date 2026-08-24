# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
UNIT_WORKFLOW = ROOT / ".github/workflows/unit_tests.yml"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"
TECHNICAL_DESIGN = ROOT / "docs/cherry-pick-automation/technical-design.md"
CHERRY_PICK_SKILL = ROOT / "skills/rocm-cherry-pick/SKILL.md"
USER_MANUAL = ROOT / "docs/cherry-pick-automation/user-manual.md"


def test_repository_has_read_only_coverage_enforcing_unit_workflow():
    text = UNIT_WORKFLOW.read_text()
    assert "name: Unit Tests" in text
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "python -m pip install -r requirements-test.txt" in text
    assert "--cov=scripts.cherry_pick" in text
    assert "--cov=scripts.build_cherry_pick_skill" in text
    assert "--cov-branch" in text
    assert "--cov-report=json:coverage.json" in text
    assert "--cov-fail-under=90" in text
    assert "python scripts/check_cherry_pick_coverage.py coverage.json" in text
    assert "--minimum-lines 95" in text
    assert "--minimum-branches 90" in text
    for module in (
        "__main__.py",
        "authorization.py",
        "control_plane.py",
        "control_plane_cli.py",
        "core.py",
        "dependencies.py",
        "feedback.py",
        "git_auth.py",
        "github_read.py",
        "local_runtime.py",
        "managed_stack.py",
        "marketplace_cli.py",
        "orchestrator.py",
        "refs.py",
        "release_hub.py",
        "release_hub_auth.py",
        "writer.py",
    ):
        assert f"--critical-module scripts/cherry_pick/{module}" in text
    assert "--critical-module scripts/build_cherry_pick_skill.py" in text
    uses = [line for line in text.splitlines() if "uses:" in line]
    assert uses
    assert all(len(line.rsplit("@", 1)[-1].split()[0]) == 40 for line in uses)


def test_local_test_requirements_include_format_and_coverage_tools():
    requirements = (ROOT / "requirements-test.txt").read_text().splitlines()
    names = {line.split("=", 1)[0].split("<", 1)[0] for line in requirements}
    assert {"pytest", "pytest-cov", "black"} <= names


def test_generated_local_coverage_artifacts_are_ignored():
    ignored = set((ROOT / ".gitignore").read_text().splitlines())
    assert {".coverage", ".coverage.*", "htmlcov/", "coverage.json"} <= ignored


def test_pre_commit_matches_therock_python_and_data_file_bar():
    text = PRE_COMMIT.read_text()
    for hook in (
        "id: test-file-naming",
        "id: black",
        "id: check-json",
        "id: check-yaml",
        "id: actionlint",
    ):
        assert hook in text
    assert "scripts/tests/test_" in text
    assert list((ROOT / "scripts/tests").glob("test_*.py")) == []


def test_every_new_python_file_has_copyright_and_spdx_headers():
    files = [
        *sorted((ROOT / "scripts/cherry_pick").glob("*.py")),
        ROOT / "scripts/build_cherry_pick_skill.py",
        ROOT / "scripts/render_cherry_pick_workflow.py",
        ROOT / "scripts/render_cherry_pick_mermaid.py",
        ROOT / "scripts/run_cherry_pick_private_sandbox.py",
        ROOT / "scripts/check_cherry_pick_coverage.py",
        ROOT / "scripts/check_cherry_pick_integration.py",
        *sorted((ROOT / "scripts/tests").glob("*cherry*_test.py")),
    ]
    for path in files:
        first_lines = path.read_text().splitlines()[:5]
        header = "\n".join(first_lines)
        assert "Copyright Advanced Micro Devices, Inc." in header, path
        assert "SPDX-License-Identifier: MIT" in header, path


def test_every_automation_module_has_a_corresponding_test_module():
    expected = {
        "scripts/create_release_branch.py": "scripts/tests/create_release_branch_test.py",
        "scripts/rock_tagging.py": "scripts/tests/rock_tagging_test.py",
        "scripts/render_cherry_pick_workflow.py": (
            "scripts/tests/render_cherry_pick_workflow_test.py"
        ),
        "scripts/render_cherry_pick_mermaid.py": (
            "scripts/tests/cherry_pick_production_qualification_test.py"
        ),
        "scripts/run_cherry_pick_private_sandbox.py": (
            "scripts/tests/cherry_pick_production_qualification_test.py"
        ),
        "scripts/check_cherry_pick_coverage.py": (
            "scripts/tests/cherry_pick_coverage_gate_test.py"
        ),
        "scripts/check_cherry_pick_integration.py": (
            "scripts/tests/cherry_pick_integration_contract_test.py"
        ),
        "scripts/cherry_pick/clients.py": "scripts/tests/cherry_pick_clients_test.py",
        "scripts/cherry_pick/github_read.py": (
            "scripts/tests/cherry_pick_github_read_test.py"
        ),
        "scripts/cherry_pick/config.py": "scripts/tests/cherry_pick_config_test.py",
        "scripts/cherry_pick/control_plane.py": (
            "scripts/tests/cherry_pick_action_config_test.py"
        ),
        "scripts/cherry_pick/control_plane_cli.py": (
            "scripts/tests/cherry_pick_control_plane_cli_test.py"
        ),
        "scripts/cherry_pick/git.py": "scripts/tests/cherry_pick_git_test.py",
        "scripts/cherry_pick/models.py": "scripts/tests/cherry_pick_models_test.py",
        "scripts/cherry_pick/orchestrator.py": (
            "scripts/tests/cherry_pick_orchestrator_test.py"
        ),
        "scripts/cherry_pick/authorization.py": (
            "scripts/tests/cherry_pick_authorization_test.py"
        ),
        "scripts/cherry_pick/core.py": "scripts/tests/cherry_pick_core_test.py",
        "scripts/cherry_pick/core_cli.py": "scripts/tests/cherry_pick_core_cli_test.py",
        "scripts/cherry_pick/dependencies.py": (
            "scripts/tests/cherry_pick_dependencies_test.py"
        ),
        "scripts/cherry_pick/feedback.py": (
            "scripts/tests/cherry_pick_feedback_test.py"
        ),
        "scripts/cherry_pick/git_auth.py": (
            "scripts/tests/cherry_pick_git_auth_test.py"
        ),
        "scripts/cherry_pick/refs.py": "scripts/tests/cherry_pick_refs_test.py",
        "scripts/cherry_pick/action_runtime.py": (
            "scripts/tests/cherry_pick_action_runtime_test.py"
        ),
        "scripts/cherry_pick/local_runtime.py": (
            "scripts/tests/cherry_pick_local_runtime_test.py"
        ),
        "scripts/cherry_pick/managed_stack.py": (
            "scripts/tests/cherry_pick_managed_stack_adapter_test.py"
        ),
        "scripts/cherry_pick/write_authority.py": (
            "scripts/tests/cherry_pick_action_runtime_test.py"
        ),
        "scripts/cherry_pick/simulation.py": (
            "scripts/tests/cherry_pick_simulation_test.py"
        ),
        "scripts/cherry_pick/writer.py": "scripts/tests/cherry_pick_writer_test.py",
        "scripts/cherry_pick/replay.py": "scripts/tests/cherry_pick_replay_test.py",
        "scripts/cherry_pick/release_hub.py": (
            "scripts/tests/cherry_pick_release_hub_test.py"
        ),
        "scripts/cherry_pick/release_hub_auth.py": (
            "scripts/tests/cherry_pick_release_hub_auth_test.py"
        ),
        "scripts/cherry_pick/marketplace_cli.py": (
            "scripts/tests/cherry_pick_marketplace_cli_test.py"
        ),
        "scripts/cherry_pick/__main__.py": "scripts/tests/cherry_pick_cli_test.py",
        "scripts/build_cherry_pick_skill.py": (
            "scripts/tests/build_cherry_pick_skill_test.py"
        ),
        "scripts/replay_cherry_pick_history.py": (
            "scripts/tests/replay_cherry_pick_history_test.py"
        ),
    }
    for implementation, test in expected.items():
        assert (ROOT / implementation).exists(), implementation
        assert (ROOT / test).exists(), f"{implementation} is missing {test}"


def test_marketplace_skill_documents_self_contained_local_only_flow():
    text = CHERRY_PICK_SKILL.read_text()
    assert "name: rocm-cherry-pick" in text
    assert "https://developer-central.amd.com/settings/api-tokens" in text
    assert "ROCm Cherry-Pick CLI" in text
    assert "read:evidence" in text
    assert "auth login" in text
    assert " plan " in text
    assert " materialize " in text
    assert "--output-repo" in text
    assert "without a rockrel checkout" in text.lower()
    assert "cannot create or update a remote" in text.lower()
    for forbidden in (
        "local-create-draft",
        "--confirm-remote-write",
        "CREATE_DRAFT",
    ):
        assert forbidden not in text
    assert "commands executed" in text.lower()


def test_user_manual_explains_direct_git_tradeoff_and_complete_local_workflow():
    assert USER_MANUAL.exists()
    text = USER_MANUAL.read_text()
    for heading in (
        "# ROCm cherry-pick CLI user manual",
        "## Choose direct Git or the CLI",
        "## Fresh-checkout quick start",
        "## Understand the result",
        "## Handle dependencies and conflicts",
        "## What the CLI does not prove",
    ):
        assert heading in text
    for contract in (
        "local-materialize",
        "git cherry-pick -x",
        "planned_tree",
        "disabled://local-only",
        "human_review_required",
        "No remote branch or pull request",
    ):
        assert contract in text
    assert "user-manual.md" in (ROOT / "README.md").read_text()


def test_technical_design_captures_complete_production_architecture_contract():
    text = TECHNICAL_DESIGN.read_text()
    for heading in (
        "## Document status and production-readiness verdict",
        "## End-to-end architecture",
        "## End-to-end execution flows",
        "## Threat model",
        "## Implementation status",
        "## Known gaps and residual risks",
        "## TODOs and next steps",
    ):
        assert heading in text

    assert "**Production-readiness verdict:** **NOT READY**" in text
    assert "REMOTE_ACTIONS_TODO.md" in text
    assert "Release Hub" in text and "read-only observer" in text

    diagrams = re.findall(r"```mermaid\n(.*?)```", text, flags=re.DOTALL)
    assert len(diagrams) >= 8
    for diagram in diagrams:
        # Mermaid uses semicolons as statement delimiters. A literal semicolon
        # in sequence-message text terminates the message and makes the next
        # ``else`` or ``Note`` fail parsing. Avoid them in every diagram so the
        # contract remains safe across GitHub's Mermaid parser versions.
        assert ";" not in diagram
    rendered_contract = "\n".join(diagrams)
    for diagram_kind in ("flowchart TB", "flowchart LR", "sequenceDiagram"):
        assert diagram_kind in rendered_contract
    assert rendered_contract.count("sequenceDiagram") >= 3
    assert rendered_contract.count("stateDiagram-v2") >= 2
    for boundary in (
        "Offline Git core",
        "GitHub control plane",
        "Draft-only writer",
        "Release Hub",
        "AuthorizationEnvelope",
        "blocked_conflict",
        "create-draft",
    ):
        assert boundary in rendered_contract
