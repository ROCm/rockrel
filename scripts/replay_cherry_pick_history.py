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
    discover_corpus_pull_requests,
    load_manifest,
    refresh_mirror,
    run_replay_cases,
    write_replay_reports,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh or run offline historical cherry-pick replays."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh = subparsers.add_parser(
        "refresh", description="Read official Git refs into dedicated local mirrors."
    )
    refresh.add_argument("--data-root", type=Path, required=True)
    refresh.add_argument("--manifest", type=Path, required=True)
    refresh.add_argument("--allow-read-only-network", action="store_true")

    freeze = subparsers.add_parser(
        "freeze", description="Freeze a manifest from already hydrated local refs."
    )
    freeze.add_argument("--data-root", type=Path, required=True)
    freeze.add_argument("--manifest", type=Path, required=True)

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
    return _freeze(args, stdout, status="corpus_refreshed")


def _freeze(
    args: argparse.Namespace,
    stdout: TextIO,
    *,
    status: str = "corpus_frozen",
) -> int:
    manifest = build_corpus_manifest(DEFAULT_MIRROR_SPECS, args.data_root)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    audit = audit_manifest_inventory(manifest, args.data_root)
    print(
        json.dumps(
            {
                "status": status,
                "manifest": str(args.manifest),
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


def _run(args: argparse.Namespace, stdout: TextIO) -> int:
    manifest = load_manifest(args.manifest)
    inventory_audit = audit_manifest_inventory(manifest, args.data_root)
    outcomes = run_replay_cases(args.data_root, manifest.cases, jobs=args.jobs)
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
        if args.command == "freeze":
            return _freeze(args, stdout)
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
