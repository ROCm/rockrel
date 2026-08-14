from pathlib import Path


ROOT = Path(__file__).parents[2]
UNIT_WORKFLOW = ROOT / ".github/workflows/unit_tests.yml"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"


def test_repository_has_a_read_only_unit_test_workflow():
    assert UNIT_WORKFLOW.exists()
    text = UNIT_WORKFLOW.read_text()
    assert "name: Unit Tests" in text
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "python -m pip install -r requirements-test.txt" in text
    assert "python -m pytest" in text
    uses = [line for line in text.splitlines() if "uses:" in line]
    assert uses
    assert all(len(line.rsplit("@", 1)[-1].split()[0]) == 40 for line in uses)


def test_pre_commit_enforces_the_therock_test_file_convention():
    text = PRE_COMMIT.read_text()
    assert "id: test-file-naming" in text
    assert "language: fail" in text
    assert "scripts/tests/test_" in text

    incorrectly_named = sorted(
        path.name for path in (ROOT / "scripts/tests").glob("test_*.py")
    )
    assert incorrectly_named == []


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
        "scripts/cherry_pick/models.py": "scripts/tests/cherry_pick_cli_test.py",
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
