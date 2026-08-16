# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
            "--candidate-out",
            "/tmp/candidate.json",
            "--allow-read-only-network",
        ]
    )
    assert refresh.command == "refresh"
    assert refresh.allow_read_only_network is True

    inventory = parser.parse_args(
        [
            "inventory",
            "--data-root",
            "/tmp/replay-data",
            "--candidate-out",
            "/tmp/candidate.json",
        ]
    )
    assert inventory.command == "inventory"
    assert not hasattr(inventory, "allow_read_only_network")

    compare = parser.parse_args(
        [
            "compare",
            "--candidate",
            "/tmp/candidate.json",
            "--golden",
            "/tmp/golden.json",
        ]
    )
    assert compare.command == "compare"

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
    assert run.jobs == 4
    assert run.tier == "fast"
    assert run.synthetic_coverage == module.SYNTHETIC_COVERAGE

    parallel_run = parser.parse_args(
        [
            "run",
            "--data-root",
            "/tmp/replay-data",
            "--manifest",
            "/tmp/manifest.json",
            "--report-dir",
            "/tmp/reports",
            "--jobs",
            "7",
            "--tier",
            "deep",
        ]
    )
    assert parallel_run.jobs == 7
    assert parallel_run.tier == "deep"

    rollback = parser.parse_args(
        [
            "rollback",
            "--data-root",
            "/tmp/replay-data",
        ]
    )
    assert rollback.command == "rollback"
    assert rollback.data_root == Path("/tmp/replay-data")


def test_inventory_refuses_to_overwrite_the_tracked_golden(tmp_path):
    module = load_module()
    stderr = io.StringIO()

    code = module.main(
        [
            "inventory",
            "--data-root",
            str(tmp_path),
            "--candidate-out",
            str(ROOT / "scripts/tests/fixtures/historical_cherry_picks.json"),
        ],
        stderr=stderr,
    )

    assert code == 2
    assert "reviewed golden" in stderr.getvalue().lower()


def test_cli_is_directly_executable_from_the_repository_root():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr
    assert "historical cherry-pick replays" in result.stdout


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


def test_offline_run_writes_both_reports_and_propagates_evidence_gap(tmp_path):
    module = load_module()
    data_root = tmp_path / "data"
    repo = data_root / "TheRock.git"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", str(repo)], check=True)
    manifest = tmp_path / "manifest.json"
    inventory = {
        "schema_version": 1,
        "snapshots": {
            "ROCm/TheRock": {
                "source_branch": "main",
                "source_tip": "f" * 40,
                "targets": {"release/therock-7.14": "1" * 40},
            }
        },
        "cases": [
            {
                "id": "missing-evidence",
                "repository": "ROCm/TheRock",
                "source_branch": "main",
                "target_branch": "release/therock-7.14",
                "source_prs": [],
                "source_merge_commit": None,
                "source_head": None,
                "source_commits": [],
                "target_before": "a" * 40,
                "target_after": "b" * 40,
                "target_after_tree": "c" * 40,
                "provenance_method": "none",
                "classification": "unresolved",
                "analysis_notes": "Needs provenance review.",
            }
        ],
    }
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "inventory": inventory,
                "expectations": {
                    "missing-evidence": {
                        "execution_phase": "inventory",
                        "expected_status": None,
                        "expected_reason": "unresolved_provenance",
                        "expected_planned_tree": None,
                        "expected_conflict_paths": [],
                        "expected_after_status": None,
                        "expected_after_reason": None,
                        "expected_tip_status": None,
                        "expected_tip_reason": None,
                        "tier": "fast",
                    }
                },
            }
        )
    )
    report_dir = tmp_path / "reports"

    result = module.main(
        [
            "run",
            "--data-root",
            str(data_root),
            "--manifest",
            str(manifest),
            "--report-dir",
            str(report_dir),
        ]
    )

    assert result == 2
    assert (report_dir / "historical-replay.json").exists()
    assert (report_dir / "historical-replay.md").exists()


def test_offline_run_fails_closed_when_required_coverage_is_missing(
    monkeypatch, tmp_path
):
    module = load_module()
    monkeypatch.setattr(
        module,
        "load_reviewed_corpus",
        lambda _path: SimpleNamespace(inventory=object()),
    )
    monkeypatch.setattr(
        module,
        "audit_manifest_inventory",
        lambda _corpus, _root: SimpleNamespace(exit_code=0),
    )
    monkeypatch.setattr(module, "run_reviewed_cases", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        module,
        "load_synthetic_coverage",
        lambda _path: SimpleNamespace(as_mapping=lambda: {}),
    )
    monkeypatch.setattr(
        module,
        "REQUIRED_REPLAY_COVERAGE",
        {"outcome": ("draft_planned",)},
    )
    report_dir = tmp_path / "reports"
    args = SimpleNamespace(
        manifest=tmp_path / "manifest.json",
        data_root=tmp_path / "data",
        tier="fast",
        jobs=1,
        synthetic_coverage=tmp_path / "synthetic.json",
        report_dir=report_dir,
    )

    result = module._run(args, io.StringIO())

    assert result == 2
    report = json.loads((report_dir / "historical-replay.json").read_text())
    assert report["coverage"]["gaps"] == ["outcome:draft_planned"]


def test_refresh_transport_failure_is_a_clean_evidence_exit(monkeypatch, tmp_path):
    module = load_module()

    def fail_refresh(_args, _stdout):
        raise subprocess.CalledProcessError(128, ["git", "fetch"])

    monkeypatch.setattr(module, "_refresh", fail_refresh)
    stderr = io.StringIO()

    result = module.main(
        [
            "refresh",
            "--data-root",
            str(tmp_path / "data"),
            "--candidate-out",
            str(tmp_path / "candidate.json"),
            "--allow-read-only-network",
        ],
        stderr=stderr,
    )

    assert result == 2
    assert "git fetch" in stderr.getvalue()


def test_offline_inventory_writes_deterministic_candidate(monkeypatch, tmp_path):
    module = load_module()
    payload = {"schema_version": 1, "snapshots": {}, "cases": []}

    class FakeManifest:
        def as_dict(self):
            return payload

    class FakeAudit:
        total_count = 0
        strict_count = 0
        diagnostic_count = 0
        evidence_gap_count = 0
        exit_code = 0

    monkeypatch.setattr(
        module, "build_corpus_manifest", lambda _specs, _root: FakeManifest()
    )
    monkeypatch.setattr(
        module, "audit_manifest_inventory", lambda _manifest, _root: FakeAudit()
    )
    candidate = tmp_path / "candidate.json"

    result = module.main(
        [
            "inventory",
            "--data-root",
            str(tmp_path / "data"),
            "--candidate-out",
            str(candidate),
        ]
    )

    assert result == 0
    assert json.loads(candidate.read_text()) == payload


def test_rollback_command_cleans_cached_worktrees(monkeypatch, tmp_path):
    module = load_module()
    calls = []

    def rollback(data_root):
        calls.append(data_root)
        return {"ROCm/TheRock": "rolled_back"}

    monkeypatch.setattr(module, "rollback_replay_worktrees", rollback)
    stdout = io.StringIO()

    result = module.main(
        ["rollback", "--data-root", str(tmp_path / "data")],
        stdout=stdout,
    )

    assert result == 0
    assert calls == [tmp_path / "data"]
    assert json.loads(stdout.getvalue()) == {
        "status": "replay_worktrees_rolled_back",
        "worktrees": {"ROCm/TheRock": "rolled_back"},
    }
