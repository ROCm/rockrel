# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Sanitized GitHub Actions entry point for live Developer Central config."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .control_plane import fetch_action_config, write_config_snapshot
from .release_hub import ReleaseHubError, validate_api_origin


DEFAULT_API_ORIGIN = "https://developer-central.amd.com"


def build_parser() -> argparse.ArgumentParser:
    """Define the sanitized Actions command that fetches one config snapshot."""

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("action-fetch-config")
    fetch.add_argument("--api", default=DEFAULT_API_ORIGIN)
    fetch.add_argument("--output", required=True, type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    fetcher=fetch_action_config,
    snapshot_writer=write_config_snapshot,
) -> int:
    """Fetch and privately persist config, returning sanitized failures without tokens."""

    args = build_parser().parse_args(argv)
    if not args.output.is_absolute():
        print("error: --output must be an absolute path", file=stderr)
        return 2
    try:
        api_origin = validate_api_origin(args.api)
        snapshot = fetcher(api_origin, environ)
        snapshot_writer(args.output, snapshot)
    except (OSError, ReleaseHubError, ValueError):
        print(
            "error: Developer Central configuration fetch failed",
            file=stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "schema_version": "release-hub-config-snapshot.v1",
                "request_id": snapshot.request_id,
                "configuration_sha256": snapshot.configuration_sha256,
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess smoke test
    raise SystemExit(main())
