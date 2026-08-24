# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
CENTRAL = ROOT / ".github/workflows/cherry_pick.yml"
RECONCILE = ROOT / ".github/workflows/cherry_pick_reconcile.yml"
SYNC = ROOT / ".github/workflows/cherry_pick_sync_labels.yml"
TEMPLATE = ROOT / "templates/cherry_pick_request.yml"
FULL_SHA_USE = re.compile(r"uses:\s+[^\s]+@[0-9a-f]{40}(?:\s|$)", re.MULTILINE)
APP_TOKEN_SHA = "bcd2ba49218906704ab6c1aa796996da409d3eb1"
UPLOAD_ARTIFACT_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_ARTIFACT_SHA = "d3f86a106a0bac45b974a628896c90dbdf5c8093"


def workflow_text(path):
    assert path.exists(), f"missing workflow: {path}"
    return path.read_text()


def job(text, name, next_name=None):
    start = text.index(f"  {name}:\n")
    end = text.index(f"  {next_name}:\n", start) if next_name else len(text)
    return text[start:end]


def test_source_template_is_one_thin_pinned_reusable_workflow_call():
    text = workflow_text(TEMPLATE)
    assert "pull_request_target:" in text
    assert "types: [labeled, unlabeled, edited, synchronize, closed]" in text
    assert "  process:\n" in text
    assert "  discover:\n" not in text
    assert "run:" not in text
    assert "python" not in text.lower()
    assert "matrix:" not in text
    assert "labels_json: ${{ toJSON(github.event.pull_request.labels.*.name) }}" in text
    assert "event_label: ${{ github.event.label.name }}" in text
    assert "event_action: ${{ github.event.action }}" in text
    assert "JIRA" not in text


def test_central_workflow_owns_label_discovery_and_train_fanout():
    text = workflow_text(CENTRAL)
    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    for input_name in (
        "source_pr:",
        "automation_ref:",
        "event_action:",
        "event_label:",
        "labels_json:",
    ):
        assert input_name in text
    assert "  discover:\n" in text
    assert "python3 -m scripts.cherry_pick \\" in text
    assert "--config-snapshot" in text
    assert "              discover \\" in text
    assert "fromJSON(needs.discover.outputs.trains)" in text
    assert "matrix.train" in text


def test_manual_dispatch_revision_is_validated_before_checkout_or_app_token():
    for path in (CENTRAL, RECONCILE):
        text = workflow_text(path)
        preflight = job(text, "preflight", "discover")
        discover = job(text, "discover", "plan" if path == CENTRAL else "reconcile")
        assert "runs-on:" in preflight
        assert "actions/checkout" not in preflight
        assert "create-github-app-token" not in preflight
        assert "[0-9a-f]{40}" in preflight
        assert "github.event_name" in preflight
        assert "github.sha" in preflight
        assert "workflow_dispatch" in preflight
        assert "needs: preflight" in discover


def test_workflows_pin_actions_and_explicitly_set_up_python():
    setup_python = re.compile(r"actions/setup-python@[0-9a-f]{40}", re.MULTILINE)
    for path in (CENTRAL, RECONCILE, SYNC):
        text = workflow_text(path)
        uses_lines = [line for line in text.splitlines() if "uses:" in line]
        assert uses_lines
        assert all(FULL_SHA_USE.search(line) for line in uses_lines), uses_lines
        if "python3 -m scripts.cherry_pick" in text:
            assert setup_python.search(text), path


def test_privileged_workflows_never_reference_or_execute_pr_head_code():
    for path in (CENTRAL, TEMPLATE):
        text = workflow_text(path)
        assert "pull_request.head" not in text
        assert "github.head_ref" not in text
        assert "ref: ${{ github.event.pull_request" not in text
        assert "actions/checkout" not in workflow_text(TEMPLATE)


def test_builtin_tokens_are_read_only_and_named_secrets_only():
    for path in (CENTRAL, RECONCILE, SYNC, TEMPLATE):
        text = workflow_text(path)
        header = text.split("jobs:", 1)[0]
        assert "permissions:\n  contents: read" in header
        assert "secrets: inherit" not in text
        assert "github.token" not in text


def test_app_never_requests_administration_permission():
    for path in (CENTRAL, RECONCILE, SYNC):
        text = workflow_text(path)
        assert "permission-administration" not in text
    central = workflow_text(CENTRAL)
    plan = job(central, "plan", "feedback")
    assert f"actions/create-github-app-token@{APP_TOKEN_SHA}" in plan
    assert "permission-contents: read" in plan
    assert "permission-issues: read" in plan
    assert "permission-pull-requests: read" in plan
    assert "permission-checks: read" in plan
    assert "permission-contents: write" not in plan


def test_all_remote_write_jobs_are_literal_disabled_for_local_review():
    central = workflow_text(CENTRAL)
    feedback = job(central, "feedback", "create-draft")
    create = job(central, "create-draft")
    reconcile = workflow_text(RECONCILE)
    sync = workflow_text(SYNC)

    for write_job in (feedback, create, job(reconcile, "create-drafts"), sync):
        assert (
            "if: ${{ github.repository == 'LOCAL_REVIEW_REMOTE_WRITES_DISABLED' }}"
            in write_job
        )
    assert "permission-issues: write" in feedback
    assert "permission-contents: write" in create
    assert "permission-pull-requests: write" in create
    assert "permission-checks: write" in feedback


def test_workflows_and_template_have_no_jira_contract_or_secret():
    for path in (CENTRAL, RECONCILE, SYNC, TEMPLATE):
        text = workflow_text(path).lower()
        assert "jira" not in text


def test_plan_explicitly_hydrates_pull_and_destination_refs_before_offline_core():
    text = job(workflow_text(CENTRAL), "plan", "feedback")
    assert "refs/pull/" in text
    assert 'GIT_NO_LAZY_FETCH: "1"' in text
    assert 'GIT_TERMINAL_PROMPT: "0"' in text
    assert "scripts.cherry_pick.core_cli" in text
    assert text.index("refs/pull/") < text.index("scripts.cherry_pick.core_cli")
    assert "--config-snapshot" in text
    assert "--expected-config-sha256" in text
    assert f"actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}" in text
    assert "cherry-pick-plan-${{ matrix.train }}" in text


def test_feedback_uses_check_and_comment_but_write_jobs_remain_disabled():
    central = workflow_text(CENTRAL)
    feedback = job(central, "feedback", "create-draft")
    assert "permission-checks: write" in feedback
    assert "permission-issues: write" in feedback
    assert f"actions/download-artifact@{DOWNLOAD_ARTIFACT_SHA}" in feedback
    assert "action-publish-result" in feedback
    assert "--result-file" in feedback
    assert "publish-result --help" not in feedback
    assert "LOCAL_REVIEW_REMOTE_WRITES_DISABLED" in feedback


def test_write_job_gets_a_distinct_short_lived_token_and_revalidates():
    central = workflow_text(CENTRAL)
    plan = job(central, "plan", "feedback")
    create = job(central, "create-draft")
    assert "steps.read-token.outputs.token" in plan
    assert "steps.read-token.outputs.token" not in create
    assert "Create draft-only App token" in create
    assert "permission-checks: read" in create
    assert "revalidate" in create.lower()
    assert "managed_dependency_frontier" in create
    assert f"actions/download-artifact@{DOWNLOAD_ARTIFACT_SHA}" in create
    assert "action-create-draft" in create
    assert "--expected-result-file" in create
    assert "--config-snapshot" in create
    assert "--expected-config-sha256" in create
    assert "--scratch-root" in create
    assert "action-create-draft --help" not in create
    for repository in ("TheRock", "rocm-systems", "rocm-libraries"):
        assert f"repository: ROCm/{repository}" in create


def test_plan_and_shadow_cannot_receive_write_credentials():
    text = workflow_text(CENTRAL)
    plan = job(text, "plan", "feedback")
    assert "permission-contents: write" not in plan
    assert "permission-issues: write" not in plan
    assert "permission-pull-requests: write" not in plan
    assert "--publish-status" not in plan
    assert "needs.plan.outputs.train_mode == 'create-draft'" in text
    assert "managed_dependency_frontier" in text


def test_reconciliation_defaults_read_only_and_serializes_identity():
    text = workflow_text(RECONCILE)
    assert "schedule:" in text
    assert "--create-drafts" not in job(text, "reconcile", "create-drafts")
    assert "cancel-in-progress: false" in text
    read_job = job(text, "reconcile", "create-drafts")
    assert "permission-contents: write" not in read_job
    assert "permission-issues: write" not in read_job
    assert "permission-pull-requests: write" not in read_job
    assert "python3 -m scripts.cherry_pick" in read_job
    assert " reconcile " in read_job.replace("\\\n", " ")
    assert "--config-snapshot" in read_job
    assert "--expected-config-sha256" in read_job
    assert f"actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}" in read_job
    for repository in ("TheRock", "rocm-systems", "rocm-libraries"):
        assert f"repository: ROCm/{repository}" in read_job

    write_job = job(text, "create-drafts", "feedback")
    assert "permission-checks: read" in write_job
    assert f"actions/download-artifact@{DOWNLOAD_ARTIFACT_SHA}" in write_job
    assert "action-reconcile" in write_job
    assert "--expected-results-file" in write_job
    assert "--scratch-root" in write_job
    assert "reconcile --create-drafts --help" not in write_job

    feedback = job(text, "feedback")
    assert "action-publish-reconciliation" in feedback
    assert "permission-checks: write" in feedback
    assert "permission-issues: write" in feedback
    assert "LOCAL_REVIEW_REMOTE_WRITES_DISABLED" in feedback


def test_local_review_config_has_no_create_draft_train():
    text = (ROOT / "config/cherry-pick-trains.json").read_text()
    assert '"mode": "create-draft"' not in text


def test_label_sync_is_disabled_but_calls_the_action_only_entrypoint():
    text = workflow_text(SYNC)
    assert "LOCAL_REVIEW_REMOTE_WRITES_DISABLED" in text
    assert "action-sync-labels" in text
    assert "sync-labels --help" not in text
    assert "permission-issues: write" in text
    for repository in ("TheRock", "rocm-systems", "rocm-libraries"):
        assert repository in text
