# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
from pathlib import Path

import pytest

from scripts.cherry_pick.replay import CorpusManifest, run_replay_case


ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "scripts/tests/fixtures/historical_cherry_picks.json"
DATA_ENV = "ROCM_CHERRYPICK_REPLAY_DATA"


def strict_cases():
    if not MANIFEST.exists():
        return ()
    manifest = CorpusManifest.from_dict(json.loads(MANIFEST.read_text()))
    return tuple(
        case for case in manifest.cases if case.classification.value == "strict_exact"
    )


def test_pinned_manifest_is_exhaustive_and_has_no_unknown_cases():
    manifest = CorpusManifest.from_dict(json.loads(MANIFEST.read_text()))
    assert manifest.cases
    assert all(case.classification.value != "unresolved" for case in manifest.cases)
    assert all(snapshot.targets for snapshot in manifest.snapshots.values())


@pytest.mark.parametrize("case", strict_cases(), ids=lambda case: case.id)
def test_strict_historical_replay(case):
    data_root = os.environ.get(DATA_ENV)
    if not data_root:
        pytest.skip(f"set {DATA_ENV} to run historical integration replays")
    repo = Path(data_root) / f"{case.repository.split('/', 1)[1]}.git"
    outcome = run_replay_case(repo, case)
    assert outcome.strict_failure is False, outcome.as_dict()
