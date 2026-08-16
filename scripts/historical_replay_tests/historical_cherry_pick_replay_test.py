# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.cherry_pick.replay import ReviewedCorpus

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "scripts/tests/fixtures/historical_cherry_picks.json"
DATA_ENV = "ROCM_CHERRYPICK_REPLAY_DATA"
REPLAY_CLI = ROOT / "scripts/replay_cherry_pick_history.py"


def load_corpus():
    return ReviewedCorpus.from_dict(json.loads(MANIFEST.read_text()))


def test_pinned_manifest_is_exhaustive_and_has_no_unknown_cases():
    manifest = load_corpus()
    assert manifest.inventory.cases
    assert all(
        case.classification.value != "unresolved"
        for case in manifest.inventory.cases
    )
    assert all(snapshot.targets for snapshot in manifest.inventory.snapshots.values())
    assert set(manifest.expectations) == {
        case.id for case in manifest.inventory.cases
    }


def test_pinned_manifest_has_positive_and_negative_historical_controls():
    classifications = {
        case.classification.value for case in load_corpus().inventory.cases
    }

    assert {
        "strict_exact",
        "manual_resolution",
        "historical_adaptation",
        "multi_source_bundle",
        "gitlink_rollup",
        "release_native",
        "revert",
    } <= classifications


def test_standalone_cli_replays_full_corpus_in_parallel(tmp_path):
    data_root = os.environ.get(DATA_ENV)
    if not data_root:
        pytest.skip(f"set {DATA_ENV} to run historical integration replays")
    report_dir = tmp_path / "reports"
    result = subprocess.run(
        [
            sys.executable,
            str(REPLAY_CLI),
            "run",
            "--data-root",
            data_root,
            "--manifest",
            str(MANIFEST),
            "--report-dir",
            str(report_dir),
            "--jobs",
            "4",
            "--tier",
            "deep",
        ],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads((report_dir / "historical-replay.json").read_text())
    manifest = load_corpus()
    outcomes = report["outcomes"]
    assert [outcome["case_id"] for outcome in outcomes] == [
        case.id for case in manifest.inventory.cases
    ]

    strict_ids = {
        case.id
        for case in manifest.inventory.cases
        if case.classification.value == "strict_exact"
    }
    assert strict_ids
    assert all(
        outcome["disposition"] == "passed"
        for outcome in outcomes
        if outcome["case_id"] in strict_ids
    )
    assert all(outcome["strict_failure"] is False for outcome in outcomes)
    strict = [
        outcome for outcome in outcomes if outcome["classification"] == "strict_exact"
    ]
    assert len(strict) == 31
    assert all(outcome["postmerge_status"] == "already_contained" for outcome in strict)
    assert all(outcome["tip_status"] == "already_contained" for outcome in strict)
    assert all(not outcome["expectation_mismatches"] for outcome in outcomes)

    manual = [
        outcome
        for outcome in outcomes
        if outcome["classification"] == "manual_resolution"
    ]
    adaptations = [
        outcome
        for outcome in outcomes
        if outcome["classification"] == "historical_adaptation"
    ]
    assert manual and all(
        outcome["engine_status"] == "blocked_conflict" for outcome in manual
    )
    assert adaptations and all(
        outcome["engine_status"] == "draft_planned" for outcome in adaptations
    )
    assert any(
        outcome["planned_tree"] != outcome["historical_tree"] for outcome in adaptations
    )
    inventory_only = [
        outcome for outcome in outcomes if outcome["execution_phase"] == "inventory"
    ]
    core = [outcome for outcome in outcomes if outcome["execution_phase"] == "core"]
    assert len(inventory_only) == 38
    assert len(core) == 39
