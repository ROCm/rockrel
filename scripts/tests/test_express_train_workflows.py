import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
CENTRAL = ROOT / ".github/workflows/express_train_cherry_pick.yml"
RECONCILE = ROOT / ".github/workflows/express_train_reconcile.yml"
SYNC = ROOT / ".github/workflows/express_train_sync_labels.yml"
TEMPLATE = ROOT / "templates/express_train_request.yml"
FULL_SHA_USE = re.compile(r"uses:\s+[^\s]+@[0-9a-f]{40}(?:\s|$)", re.MULTILINE)


def workflow_text(path):
    assert path.exists(), f"missing workflow: {path}"
    return path.read_text()


def test_central_workflow_supports_reuse_and_manual_replay():
    text = workflow_text(CENTRAL)
    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    assert "source_pr:" in text
    assert "train_id:" in text
    assert "automation_ref:" in text


def test_workflows_pin_every_external_action_to_full_sha():
    for path in (CENTRAL, RECONCILE, SYNC):
        text = workflow_text(path)
        uses_lines = [line for line in text.splitlines() if "uses:" in line]
        assert uses_lines
        assert all(FULL_SHA_USE.search(line) for line in uses_lines), uses_lines


def test_privileged_workflow_never_references_pull_request_head_code():
    for path in (CENTRAL, TEMPLATE):
        text = workflow_text(path)
        assert "pull_request.head" not in text
        assert "github.head_ref" not in text
        assert "ref: ${{ github.event.pull_request" not in text


def test_app_write_token_is_limited_to_create_draft_job():
    text = workflow_text(CENTRAL)
    assert "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349" in text
    assert "if: inputs.mode == 'create-draft'" in text
    assert "permissions:\n  contents: read" in text
    assert "secrets: inherit" not in text


def test_source_template_uses_only_named_secrets_and_safe_event_metadata():
    text = workflow_text(TEMPLATE)
    assert "pull_request_target:" in text
    assert "types: [labeled, unlabeled, closed]" in text
    assert "secrets: inherit" not in text
    assert "app_id: ${{ secrets.ROCM_CHERRYPICK_APP_ID }}" in text
    assert "jira_token: ${{ secrets.ROCM_CHERRYPICK_JIRA_TOKEN }}" in text


def test_reconciliation_defaults_to_plan_only():
    text = workflow_text(RECONCILE)
    assert "mode: plan" in text
    assert "schedule:" in text
