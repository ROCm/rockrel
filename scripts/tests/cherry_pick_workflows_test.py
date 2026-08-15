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
    assert "types: [labeled, unlabeled, closed]" in text
    assert "  process:\n" in text
    assert "  discover:\n" not in text
    assert "run:" not in text
    assert "python" not in text.lower()
    assert "matrix:" not in text
    assert "labels_json: ${{ toJSON(github.event.pull_request.labels.*.name) }}" in text
    assert "event_label: ${{ github.event.label.name }}" in text
    assert "event_action: ${{ github.event.action }}" in text


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
    assert "python3 -m scripts.cherry_pick discover" in text
    assert "fromJSON(needs.discover.outputs.trains)" in text
    assert "matrix.train" in text


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


def test_plan_and_shadow_cannot_receive_write_credentials():
    text = workflow_text(CENTRAL)
    plan = job(text, "plan", "feedback")
    assert "permission-contents: write" not in plan
    assert "permission-issues: write" not in plan
    assert "permission-pull-requests: write" not in plan
    assert "--publish-status" not in plan
    assert "needs.plan.outputs.train_mode == 'create-draft'" in text
    assert "needs.plan.outputs.status == 'draft_planned'" in text


def test_reconciliation_defaults_read_only_and_serializes_identity():
    text = workflow_text(RECONCILE)
    assert "schedule:" in text
    assert "mode: plan" in text
    assert "cancel-in-progress: false" in text
    assert "--create-drafts" in job(text, "create-drafts")
    read_job = job(text, "reconcile", "create-drafts")
    assert "permission-contents: write" not in read_job
    assert "permission-issues: write" not in read_job
    assert "permission-pull-requests: write" not in read_job


def test_local_review_config_has_no_create_draft_train():
    text = (ROOT / "config/cherry-pick-trains.json").read_text()
    assert '"mode": "create-draft"' not in text
