# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Refresh and run the local ROCm historical cherry-pick replay corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.cherry_pick.replay import (
    DEFAULT_MIRROR_SPECS,
    ReplayReport,
    audit_manifest_inventory,
    build_corpus_manifest,
    compare_candidate_to_golden,
    discover_corpus_pull_requests,
    load_manifest,
    load_reviewed_corpus,
    refresh_mirror,
    rollback_replay_worktrees,
    run_reviewed_cases,
    write_replay_reports,
)

ROOT = Path(__file__).resolve().parents[1]
REVIEWED_GOLDEN = ROOT / "scripts/tests/fixtures/historical_cherry_picks.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh or run offline historical cherry-pick replays."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh = subparsers.add_parser(
        "refresh", description="Read official Git refs into dedicated local mirrors."
    )
    refresh.add_argument("--data-root", type=Path, required=True)
    refresh.add_argument("--candidate-out", type=Path, required=True)
    refresh.add_argument("--allow-read-only-network", action="store_true")

    inventory = subparsers.add_parser(
        "inventory",
        description="Write an unreviewed candidate from already hydrated local refs.",
    )
    inventory.add_argument("--data-root", type=Path, required=True)
    inventory.add_argument("--candidate-out", type=Path, required=True)

    compare = subparsers.add_parser(
        "compare", description="Compare a candidate inventory with reviewed goldens."
    )
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--golden", type=Path, required=True)

    run = subparsers.add_parser(
        "run", description="Replay a pinned corpus with all network hydration disabled."
    )
    run.add_argument("--data-root", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--report-dir", type=Path, required=True)
    run.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Maximum concurrent replay cases (default: 4).",
    )
    run.add_argument(
        "--tier",
        choices=("fast", "deep"),
        default="fast",
        help="Reviewed replay tier (default: fast).",
    )
    rollback = subparsers.add_parser(
        "rollback",
        description="Clean persistent replay worktrees without rebuilding indexes.",
    )
    rollback.add_argument("--data-root", type=Path, required=True)
    return parser


def _refresh(args: argparse.Namespace, stdout: TextIO) -> int:
    for spec in DEFAULT_MIRROR_SPECS:
        refresh_mirror(
            spec,
            args.data_root / f"{spec.repository.split('/', 1)[1]}.git",
            allow_read_only_network=args.allow_read_only_network,
        )
    pull_requests = discover_corpus_pull_requests(DEFAULT_MIRROR_SPECS, args.data_root)
    for spec in DEFAULT_MIRROR_SPECS:
        refresh_mirror(
            spec,
            args.data_root / f"{spec.repository.split('/', 1)[1]}.git",
            allow_read_only_network=args.allow_read_only_network,
            pull_requests=pull_requests[spec.repository],
        )
    return _inventory(args, stdout, status="corpus_refreshed")


def _candidate_path_is_tracked(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(ROOT)
    except ValueError:
        return False
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(relative)],
        cwd=ROOT,
        check=False,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _inventory(
    args: argparse.Namespace,
    stdout: TextIO,
    *,
    status: str = "candidate_inventory_written",
) -> int:
    if (
        args.candidate_out.resolve() == REVIEWED_GOLDEN.resolve()
        or _candidate_path_is_tracked(args.candidate_out)
    ):
        raise PermissionError(
            "candidate inventory cannot overwrite the reviewed golden or another tracked file"
        )
    manifest = build_corpus_manifest(DEFAULT_MIRROR_SPECS, args.data_root)
    args.candidate_out.parent.mkdir(parents=True, exist_ok=True)
    args.candidate_out.write_text(
        json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    audit = audit_manifest_inventory(manifest, args.data_root)
    print(
        json.dumps(
            {
                "status": status,
                "candidate": str(args.candidate_out),
                "cases": audit.total_count,
                "strict": audit.strict_count,
                "diagnostic": audit.diagnostic_count,
                "evidence_gaps": audit.evidence_gap_count,
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return audit.exit_code


def _compare(args: argparse.Namespace, stdout: TextIO) -> int:
    result = compare_candidate_to_golden(
        load_manifest(args.candidate),
        load_reviewed_corpus(args.golden),
    )
    print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
    return result.exit_code


def _run(args: argparse.Namespace, stdout: TextIO) -> int:
    corpus = load_reviewed_corpus(args.manifest)
    inventory_audit = audit_manifest_inventory(corpus.inventory, args.data_root)
    outcomes = run_reviewed_cases(
        args.data_root,
        corpus,
        tier=args.tier,
        jobs=args.jobs,
    )
    report = ReplayReport.from_outcomes(outcomes)
    write_replay_reports(report, args.report_dir)
    exit_code = 2 if inventory_audit.exit_code == 2 else report.exit_code
    print(
        json.dumps(
            {
                "status": "historical_replay_complete",
                "report_dir": str(args.report_dir),
                "cases": len(outcomes),
                "exit_code": exit_code,
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return exit_code


def _rollback(args: argparse.Namespace, stdout: TextIO) -> int:
    worktrees = rollback_replay_worktrees(args.data_root)
    print(
        json.dumps(
            {
                "status": "replay_worktrees_rolled_back",
                "worktrees": worktrees,
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "refresh":
            return _refresh(args, stdout)
        if args.command == "inventory":
            return _inventory(args, stdout)
        if args.command == "compare":
            return _compare(args, stdout)
        if args.command == "rollback":
            return _rollback(args, stdout)
        return _run(args, stdout)
    except subprocess.CalledProcessError as exc:
        command = exc.cmd if isinstance(exc.cmd, str) else " ".join(exc.cmd)
        print(
            f"error: {command} failed with exit status {exc.returncode}",
            file=stderr,
        )
        return 2
    except (OSError, PermissionError, ValueError) as exc:
        print(f"error: {exc}", file=stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
