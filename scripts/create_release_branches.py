#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Create release branches for TheRock and all tracked ROCm submodules.

Runs preflight checks first (via check_release_branch_state), then creates
and pushes the release branch for each repo in the plan.

Dry-run mode (default) logs all actions without pushing to remotes.

Usage:
    python create_release_branches.py \\
        --branch-name <release-branch> \\
        --commitid <rock-commit-sha> \\
        [--no-dry-run]
"""
import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path
from pprint import pformat

from release_utils import run_command, run_command_output, setup_remote, TIMEOUT_SHORT
from repo_plan import RepoInfo, build_plan

log = logging.getLogger("rock_release")

def remote_branch_exists(repo_dir: Path, branch_name: str) -> bool:
    output = run_command_output(
        ["git", "ls-remote", "--heads", "rocm-github", branch_name],
        cwd=repo_dir,
        timeout=TIMEOUT_SHORT,
    )
    return bool(output)

def execute_plan(plan: dict[str, RepoInfo], branch_name: str, dry_run: bool) -> int:
    successful, skipped, failed = {}, {}, {}

    for repo_name, info in plan.items():
        log.info("Processing %s", repo_name)

        if not info.path.exists():
            failed[repo_name] = f"Path does not exist: {info.path}"
            continue

        try:
            setup_remote(info.url, info.path)
        except subprocess.CalledProcessError as exc:
            failed[repo_name] = f"Remote setup failed: {exc}"
            continue

        try:
            if remote_branch_exists(info.path, branch_name):
                msg = f"Branch '{branch_name}' already exists on rocm-github"
                log.info(msg)
                skipped[repo_name] = msg
                continue
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            failed[repo_name] = f"Remote branch check failed: {exc}"
            continue

        try:
            run_command(["git", "checkout", "-B", branch_name, info.commit], cwd=info.path)
        except subprocess.CalledProcessError as exc:
            failed[repo_name] = f"Branch creation failed: {exc}"
            continue

        if dry_run:
            log.info("[DRY RUN] Skipping push of %s for %s", branch_name, repo_name)
            successful[repo_name] = info
        else:
            try:
                run_command(["git", "push", "rocm-github", branch_name], cwd=info.path, timeout=120)
                successful[repo_name] = info
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                failed[repo_name] = f"Push failed: {exc}"

    log.info(
        "Summary: %d succeeded, %d skipped, %d failed out of %d repos",
        len(successful), len(skipped), len(failed), len(plan),
    )
    if successful:
        log.info("Successful: %s", pformat(list(successful)))
    if skipped:
        log.info("Skipped (branch already exists): %s", pformat(list(skipped)))
    if failed:
        log.info("Failed: %s", pformat(failed))

    return 1 if failed else 0

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create ROCm release branches")
    parser.add_argument("-B", "--branch-name", required=True, help="Release branch name")
    parser.add_argument("-C", "--commitid", required=True, help="TheRock commit SHA")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exclude-list", nargs="*", default=[])
    parser.add_argument("--force-clone", action="store_true", default=False)
    parser.add_argument("--cache-dir", default=None)
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[0-9a-f]{40}", args.commitid):
        print(f"ERROR: --commitid must be a full 40-char lowercase SHA-1, got: {args.commitid!r}")
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Dry run mode = %s", args.dry_run)

    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    try:
        plan = build_plan(
            commitid=args.commitid,
            cache_dir=cache_dir,
            force_clone=args.force_clone,
            exclude_list=set(args.exclude_list),
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    return execute_plan(plan, args.branch_name, args.dry_run)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
