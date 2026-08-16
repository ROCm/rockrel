# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import importlib.util
import io
import json
import subprocess
import sys
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

    freeze = parser.parse_args(
        [
            "freeze",
            "--data-root",
            "/tmp/replay-data",
            "--manifest",
            "/tmp/manifest.json",
        ]
    )
    assert freeze.command == "freeze"
    assert not hasattr(freeze, "allow_read_only_network")

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
    manifest.write_text(
        json.dumps(
            {
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
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--allow-read-only-network",
        ],
        stderr=stderr,
    )

    assert result == 2
    assert "git fetch" in stderr.getvalue()


def test_offline_freeze_writes_deterministic_manifest(monkeypatch, tmp_path):
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
    manifest = tmp_path / "manifest.json"

    result = module.main(
        [
            "freeze",
            "--data-root",
            str(tmp_path / "data"),
            "--manifest",
            str(manifest),
        ]
    )

    assert result == 0
    assert json.loads(manifest.read_text()) == payload
