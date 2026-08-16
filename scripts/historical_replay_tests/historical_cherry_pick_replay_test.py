# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.cherry_pick.replay import CorpusManifest

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "scripts/tests/fixtures/historical_cherry_picks.json"
DATA_ENV = "ROCM_CHERRYPICK_REPLAY_DATA"
REPLAY_CLI = ROOT / "scripts/replay_cherry_pick_history.py"


def load_corpus():
    return CorpusManifest.from_dict(json.loads(MANIFEST.read_text()))


def test_pinned_manifest_is_exhaustive_and_has_no_unknown_cases():
    manifest = load_corpus()
    assert manifest.cases
    assert all(case.classification.value != "unresolved" for case in manifest.cases)
    assert all(snapshot.targets for snapshot in manifest.snapshots.values())


def test_pinned_manifest_has_positive_and_negative_historical_controls():
    classifications = {case.classification.value for case in load_corpus().cases}

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
        case.id for case in manifest.cases
    ]

    strict_ids = {
        case.id
        for case in manifest.cases
        if case.classification.value == "strict_exact"
    }
    assert strict_ids
    assert all(
        outcome["disposition"] == "passed"
        for outcome in outcomes
        if outcome["case_id"] in strict_ids
    )
    assert all(outcome["strict_failure"] is False for outcome in outcomes)

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
