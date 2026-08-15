# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_generic_cherry_pick_components_replace_express_train_components():
    required = [
        "scripts/cherry_pick/__main__.py",
        "scripts/render_cherry_pick_workflow.py",
        "config/cherry-pick-trains.json",
        "config/cherry-pick-github-app-manifest.json",
        ".github/workflows/cherry_pick.yml",
        ".github/workflows/cherry_pick_reconcile.yml",
        ".github/workflows/cherry_pick_sync_labels.yml",
        "templates/cherry_pick_request.yml",
    ]
    removed = [
        "scripts/express_train",
        "scripts/render_express_train_workflow.py",
        "config/express-trains.json",
        "config/express-train-github-app-manifest.json",
        ".github/workflows/express_train_cherry_pick.yml",
        ".github/workflows/express_train_reconcile.yml",
        ".github/workflows/express_train_sync_labels.yml",
        "templates/express_train_request.yml",
    ]
    assert [path for path in required if not (ROOT / path).exists()] == []
    assert [path for path in removed if (ROOT / path).exists()] == []


def test_production_files_do_not_use_legacy_express_train_identity():
    production = [
        ROOT / "scripts/cherry_pick",
        ROOT / "scripts/render_cherry_pick_workflow.py",
        ROOT / "config/cherry-pick-trains.json",
        ROOT / "config/cherry-pick-github-app-manifest.json",
        ROOT / ".github/workflows/cherry_pick.yml",
        ROOT / ".github/workflows/cherry_pick_reconcile.yml",
        ROOT / ".github/workflows/cherry_pick_sync_labels.yml",
        ROOT / "templates/cherry_pick_request.yml",
    ]
    legacy = ("Express Train", "express-train", "express_train")
    matches = []
    for path in production:
        if not path.exists():
            continue
        files = path.rglob("*.py") if path.is_dir() else [path]
        for file in files:
            text = file.read_text()
            for token in legacy:
                if token in text:
                    matches.append(f"{file.relative_to(ROOT)}:{token}")
    assert matches == []
