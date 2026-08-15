# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from pathlib import Path


ROOT = Path(__file__).parents[2]
UNIT_WORKFLOW = ROOT / ".github/workflows/unit_tests.yml"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"


def test_repository_has_read_only_coverage_enforcing_unit_workflow():
    text = UNIT_WORKFLOW.read_text()
    assert "name: Unit Tests" in text
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "python -m pip install -r requirements-test.txt" in text
    assert "--cov=scripts.cherry_pick" in text
    assert "--cov-branch" in text
    assert "--cov-fail-under=90" in text
    uses = [line for line in text.splitlines() if "uses:" in line]
    assert uses
    assert all(len(line.rsplit("@", 1)[-1].split()[0]) == 40 for line in uses)


def test_local_test_requirements_include_format_and_coverage_tools():
    requirements = (ROOT / "requirements-test.txt").read_text().splitlines()
    names = {line.split("=", 1)[0].split("<", 1)[0] for line in requirements}
    assert {"pytest", "pytest-cov", "black"} <= names


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
        ROOT / "scripts/render_cherry_pick_workflow.py",
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
        "scripts/cherry_pick/clients.py": "scripts/tests/cherry_pick_clients_test.py",
        "scripts/cherry_pick/config.py": "scripts/tests/cherry_pick_config_test.py",
        "scripts/cherry_pick/coverage.py": "scripts/tests/cherry_pick_coverage_test.py",
        "scripts/cherry_pick/git.py": "scripts/tests/cherry_pick_git_test.py",
        "scripts/cherry_pick/models.py": "scripts/tests/cherry_pick_models_test.py",
        "scripts/cherry_pick/orchestrator.py": (
            "scripts/tests/cherry_pick_orchestrator_test.py"
        ),
        "scripts/cherry_pick/policy.py": "scripts/tests/cherry_pick_policy_test.py",
        "scripts/cherry_pick/writer.py": "scripts/tests/cherry_pick_writer_test.py",
        "scripts/cherry_pick/__main__.py": "scripts/tests/cherry_pick_cli_test.py",
    }
    for implementation, test in expected.items():
        assert (ROOT / implementation).exists(), implementation
        assert (ROOT / test).exists(), f"{implementation} is missing {test}"
