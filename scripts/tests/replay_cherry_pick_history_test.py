# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/replay_cherry_pick_history.py"


def load_module():
    assert SCRIPT.exists(), "historical replay CLI must exist"
    spec = importlib.util.spec_from_file_location("replay_cherry_pick_history", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_has_explicit_refresh_and_offline_run_contract():
    module = load_module()
    parser = module.build_parser()

    refresh = parser.parse_args(
        [
            "refresh",
            "--data-root",
            "/tmp/replay-data",
            "--manifest",
            "/tmp/manifest.json",
            "--allow-read-only-network",
        ]
    )
    assert refresh.command == "refresh"
    assert refresh.allow_read_only_network is True

    run = parser.parse_args(
        [
            "run",
            "--data-root",
            "/tmp/replay-data",
            "--manifest",
            "/tmp/manifest.json",
            "--report-dir",
            "/tmp/reports",
        ]
    )
    assert run.command == "run"
    assert not hasattr(run, "allow_read_only_network")


def test_cli_source_contains_no_remote_write_or_pr_operations():
    text = SCRIPT.read_text()
    for forbidden in (
        "git push",
        "gh pr create",
        "create_pull",
        "DraftWriter",
        "mark-ready",
        "auto-merge",
    ):
        assert forbidden not in text
