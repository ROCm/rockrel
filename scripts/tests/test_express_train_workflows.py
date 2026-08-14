import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
CENTRAL = ROOT / ".github/workflows/express_train_cherry_pick.yml"
RECONCILE = ROOT / ".github/workflows/express_train_reconcile.yml"
SYNC = ROOT / ".github/workflows/express_train_sync_labels.yml"
TEMPLATE = ROOT / "templates/express_train_request.yml"
FULL_SHA_USE = re.compile(r"uses:\s+[^\s]+@[0-9a-f]{40}(?:\s|$)", re.MULTILINE)
APP_TOKEN_SHA = "bcd2ba49218906704ab6c1aa796996da409d3eb1"


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
    assert "event_action:" in text
    assert "--event-action" in text


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


def _job(text, name, next_name=None):
    start = text.index(f"  {name}:\n")
    end = text.index(f"  {next_name}:\n", start) if next_name else len(text)
    return text[start:end]


def test_plan_uses_a_cross_repository_read_only_app_token():
    text = workflow_text(CENTRAL)
    plan = _job(text, "plan", "feedback")
    assert f"actions/create-github-app-token@{APP_TOKEN_SHA}" in plan
    assert "client-id: ${{ secrets.ROCM_CHERRYPICK_APP_CLIENT_ID }}" in plan
    assert "permission-administration: read" in plan
    assert "permission-contents: read" in plan
    assert "permission-issues: read" in plan
    assert "permission-pull-requests: read" in plan
    assert "permission-contents: write" not in plan
    assert "permission-issues: write" not in plan
    assert "permission-pull-requests: write" not in plan
    assert "GITHUB_TOKEN: ${{ steps.read-token.outputs.token }}" in plan
    assert "--publish-status" not in plan


def test_feedback_write_token_is_isolated_from_read_only_modes():
    text = workflow_text(CENTRAL)
    feedback = _job(text, "feedback", "create-draft")
    assert "inputs.event_action != 'manual'" in feedback
    assert "needs.plan.outputs.train_mode == 'create-draft'" in feedback
    assert "needs.plan.outputs.status != 'cherry_pick_required'" in feedback
    assert f"actions/create-github-app-token@{APP_TOKEN_SHA}" in feedback
    assert "permission-issues: write" in feedback
    assert "permission-contents: write" not in feedback
    assert "permission-pull-requests: write" not in feedback
    assert "publish-result" in feedback


def test_app_write_token_is_limited_to_create_draft_job():
    text = workflow_text(CENTRAL)
    create = _job(text, "create-draft")
    assert "needs.plan.outputs.train_mode == 'create-draft'" in text
    assert "needs.plan.outputs.status == 'cherry_pick_required'" in text
    assert "train_mode: ${{ steps.result.outputs.train_mode }}" in text
    assert "status: ${{ steps.result.outputs.status }}" in text
    assert f"actions/create-github-app-token@{APP_TOKEN_SHA}" in create
    assert "permission-administration: read" in create
    assert "permission-contents: write" in create
    assert "permission-issues: write" in create
    assert "permission-pull-requests: write" in create
    assert "permissions:\n  contents: read" in text
    assert "secrets: inherit" not in text
    assert "github.token" not in text


def test_source_template_uses_only_named_secrets_and_safe_event_metadata():
    text = workflow_text(TEMPLATE)
    assert "pull_request_target:" in text
    assert "types: [labeled, unlabeled, closed]" in text
    assert "secrets: inherit" not in text
    assert "ROCM_CHERRYPICK_APP_CLIENT_ID: ${{ secrets.ROCM_CHERRYPICK_APP_CLIENT_ID }}" in text
    assert "ROCM_CHERRYPICK_JIRA_TOKEN: ${{ secrets.ROCM_CHERRYPICK_JIRA_TOKEN }}" in text
    assert "event_action: ${{ github.event.action }}" in text


def test_reconciliation_defaults_to_plan_only():
    text = workflow_text(RECONCILE)
    assert "mode: plan" in text
    assert "schedule:" in text
    assert "python3 -m scripts.express_train" in text
    assert '            reconcile \\' in text
    assert "uses: ROCm/rockrel/.github/workflows/" not in text
    assert f"actions/create-github-app-token@{APP_TOKEN_SHA}" in text
    assert "permission-administration: read" in text
    assert "permission-contents: read" in text
    assert "permission-issues: read" in text
    assert "permission-pull-requests: read" in text
    assert "permission-contents: write" not in text
    assert "permission-issues: write" not in text
    assert "permission-pull-requests: write" not in text
    assert "github.token" not in text


def test_label_sync_token_requests_only_issues_write():
    text = workflow_text(SYNC)
    assert f"actions/create-github-app-token@{APP_TOKEN_SHA}" in text
    assert "permission-issues: write" in text
    assert "permission-contents: write" not in text
    assert "permission-pull-requests: write" not in text
