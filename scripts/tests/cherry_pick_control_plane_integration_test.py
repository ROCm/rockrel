# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

from scripts.build_cherry_pick_skill import build_skill


ROOT = Path(__file__).parents[2]
CENTRAL = ROOT / ".github/workflows/cherry_pick.yml"
RECONCILE = ROOT / ".github/workflows/cherry_pick_reconcile.yml"
SYNC = ROOT / ".github/workflows/cherry_pick_sync_labels.yml"


def test_reusable_workflow_uses_developer_central_oidc_for_complete_configuration():
    text = CENTRAL.read_text()

    assert "id-token: write" in text
    assert "https://developer-central.amd.com/api/v1/cherry-pick/config" in text
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in text
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in text
    assert "api://developer-central.amd.com/rocm-cherry-pick-config" in text
    assert "action-fetch-config" in text
    assert "--config-snapshot" in text
    assert "--expected-config-sha256" in text
    assert "config/cherry-pick-trains.json" not in text
    assert "ROCM_RELEASE_HUB_TOKEN" not in text
    assert "ROCM_CHERRYPICK_APP_PRIVATE_KEY" in text


def test_workflow_keeps_developer_central_identity_separate_from_github_app_tokens():
    text = CENTRAL.read_text()
    config_step = text.index("action-fetch-config")
    first_app_token = text.index("actions/create-github-app-token@")
    assert config_step < first_app_token

    read_token_step = text.index("Create scoped read token")
    write_token_step = text.index("Create draft-only App token")
    assert read_token_step < write_token_step
    assert (
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in text[read_token_step:write_token_step]
    )


def test_every_control_plane_workflow_has_no_bundled_train_catalog_dependency():
    for workflow in (CENTRAL, RECONCILE, SYNC):
        text = workflow.read_text()
        assert "config/cherry-pick-trains.json" not in text
        assert "--config-snapshot" in text
        assert "release-trains.v4" not in text


def test_skill_bundle_contains_no_active_or_fallback_train_catalog(tmp_path):
    output = tmp_path / "rocm-cherry-pick"
    build_skill(ROOT, output, allow_dirty_review=True)

    assert not (output / "assets/cherry-pick-trains.json").exists()
    assert not list(output.rglob("*cherry-pick-trains*.json"))
    manifest = json.loads((output / "bundle-manifest.json").read_text())
    assert manifest["contracts"]["core_request"] == "3"
    assert manifest["contracts"]["release_hub_config"] == "cherry-pick-config.v1"
    assert manifest["contracts"]["release_train_source"] == "release-trains.v5"
    assert "release_hub_train" not in manifest["contracts"]
    assert all("cherry-pick-trains" not in name for name in manifest["files"])


def test_skill_runtime_and_instructions_require_live_complete_configuration():
    for relative in (
        "skills/rocm-cherry-pick/SKILL.md",
        "skills/rocm-cherry-pick/DESCRIPTION.md",
        "skills/rocm-cherry-pick/references/operator-guide.md",
        "scripts/cherry_pick/marketplace_cli.py",
    ):
        text = (ROOT / relative).read_text()
        assert "/api/v1/cherry-pick/config" in text, relative
        assert "release-trains.v5" in text, relative
    runtime = (ROOT / "scripts/cherry_pick/marketplace_cli.py").read_text()
    assert "SOURCE_BRANCHES" not in runtime
    assert "catalog_for_snapshot" not in runtime
    assert "assets/cherry-pick-trains.json" not in runtime
    assert "load_config(" not in runtime
