# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Standalone command-line interface for the offline Git core."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .config import SUPPORTED_REPOSITORIES, valid_branch_name
from .core import CorePlanner, CoreRequest, ManifestError
from .git import cherry_pick_command
from .models import Status

LOCAL_NAME = "ROCm Cherry-Pick Local Materialization"
LOCAL_EMAIL = "cherry-pick-local@users.noreply.github.com"


def build_parser() -> argparse.ArgumentParser:
    """Define offline planning and local-materialization command arguments."""

    parser = argparse.ArgumentParser(description="Run an offline cherry-pick plan")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "materialize"):
        operation = subparsers.add_parser(command)
        operation.add_argument("--manifest", type=Path, required=True)
        operation.add_argument(
            "--repo", action="append", required=True, metavar="OWNER/REPO=PATH"
        )
        operation.add_argument("--scratch-root", type=Path)
        if command == "materialize":
            operation.add_argument("--output-repo", type=Path, required=True)
            operation.add_argument("--branch", required=True)
    return parser


def _repository_map(values: Sequence[str]) -> dict[str, Path]:
    """Parse and validate repository-to-checkout command-line mappings."""

    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--repo must be OWNER/REPO=PATH, got {value!r}")
        repository, raw_path = value.split("=", 1)
        if repository not in SUPPORTED_REPOSITORIES or not raw_path:
            raise ValueError(f"--repo mapping is invalid: {value!r}")
        if repository in result:
            raise ValueError(f"--repo contains duplicate repository {repository}")
        result[repository] = Path(raw_path)
    return result


def _git(repo: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git without hooks or stdin, capturing output for sanitized handling."""

    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def materialize_local_checkout(
    *,
    request: CoreRequest,
    result,
    repositories: dict[str, Path],
    output_repo: Path,
    branch: str,
    stderr: TextIO,
) -> dict[str, object] | None:
    """Create a push-disabled checkout only when its tree matches the core plan."""

    if not valid_branch_name(branch):
        print("error: --branch is not a valid local branch name", file=stderr)
        return None
    if not output_repo.is_absolute():
        print("error: --output-repo must be an absolute path", file=stderr)
        return None
    if output_repo.exists():
        print("error: --output-repo already exists", file=stderr)
        return None
    if not output_repo.parent.is_dir():
        print("error: --output-repo parent directory does not exist", file=stderr)
        return None
    source_repo = repositories.get(request.source.repository)
    if source_repo is None or not source_repo.exists():
        print("error: source repository is unavailable", file=stderr)
        return None
    commits = result.evidence.get("ordered_commits")
    planned_tree = result.evidence.get("planned_tree")
    mainline = result.evidence.get("mainline")
    if (
        not isinstance(commits, list)
        or not commits
        or any(not isinstance(item, str) for item in commits)
        or not isinstance(planned_tree, str)
    ):
        print("error: core plan omitted materialization evidence", file=stderr)
        return None
    commands = [
        (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            *cherry_pick_command(commit, mainline, commit_result=True),
        )
        for commit in commits
    ]
    with tempfile.TemporaryDirectory(
        prefix="cherry-pick-local-", dir=output_repo.parent
    ) as temporary:
        checkout = Path(temporary) / "checkout"
        operations = (
            (
                None,
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(source_repo),
                str(checkout),
            ),
            (
                checkout,
                "remote",
                "set-url",
                "--push",
                "origin",
                "disabled://local-only",
            ),
            (checkout, "config", "user.name", LOCAL_NAME),
            (checkout, "config", "user.email", LOCAL_EMAIL),
            (
                checkout,
                "checkout",
                "-b",
                branch,
                request.source.destination.head_sha,
            ),
        )
        for repo, *operation in operations:
            completed = _git(repo, *operation)
            if completed.returncode != 0:
                print(
                    f"error: local Git setup failed during {operation[0]}",
                    file=stderr,
                )
                return None
        for command in commands:
            completed = _git(checkout, *command[3:])
            if completed.returncode != 0:
                print("error: local cherry-pick failed", file=stderr)
                return None
        tree_result = _git(checkout, "rev-parse", "HEAD^{tree}")
        head_result = _git(checkout, "rev-parse", "HEAD")
        tree = tree_result.stdout.strip()
        head = head_result.stdout.strip()
        if (
            tree_result.returncode != 0
            or head_result.returncode != 0
            or tree != planned_tree
        ):
            print("error: materialized tree does not match the core plan", file=stderr)
            return None
        checkout.rename(output_repo)
    return {
        "status": "local_materialized",
        "reason_code": "local_checkout_created",
        "source_pr": request.source.url,
        "source_repository": request.source.repository,
        "train_id": request.train_id,
        "destination_branch": request.source.destination.branch,
        "destination_head": request.source.destination.head_sha,
        "local_path": str(output_repo),
        "local_branch": branch,
        "head": head,
        "tree": tree,
        "planned_tree": planned_tree,
        "commands": [shlex.join(command) for command in commands],
        "core_result": result.as_dict(),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    planner_factory=CorePlanner,
) -> int:
    """Run offline planning or materialize only a clean draft-planned result."""

    args = build_parser().parse_args(argv)
    try:
        repositories = _repository_map(args.repo)
    except ValueError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
    try:
        payload = json.loads(args.manifest.read_text())
        request = CoreRequest.from_dict(payload)
    except (OSError, json.JSONDecodeError, ManifestError) as exc:
        print(f"error: invalid core manifest: {exc}", file=stderr)
        return 2
    planner = planner_factory()
    result = planner.plan(
        request,
        repositories,
        scratch_root=args.scratch_root,
    )
    if args.command == "materialize":
        if result.status is not Status.DRAFT_PLANNED:
            print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
            return 1
        materialized = materialize_local_checkout(
            request=request,
            result=result,
            repositories=repositories,
            output_repo=args.output_repo,
            branch=args.branch,
            stderr=stderr,
        )
        if materialized is None:
            return 2
        print(json.dumps(materialized, sort_keys=True), file=stdout)
        return 0
    print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
