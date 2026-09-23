#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Create release branches for TheRock and all tracked ROCm submodules.

Dry-run mode (default) logs all planned actions without making any local or
remote git changes.

Usage:
    python create_release_branches.py \\
        --branch-name <release-branch> \\
        --commitid <rock-git-ref> \\
        [--no-dry-run]
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path
from pprint import pformat

from release_utils import run_command, run_command_output, setup_remote, TIMEOUT_SHORT_SECONDS
from repo_plan import RepoInfo, build_plan, update_submodules

log = logging.getLogger("rock_release")

def remote_branch_exists(repo_dir: Path, branch_name: str) -> bool:
    output = run_command_output(
        ["git", "ls-remote", "--heads", "rocm-github", branch_name],
        cwd=repo_dir,
        timeout=TIMEOUT_SHORT_SECONDS,
    )
    return bool(output)

def create_branch(repo_dir: Path, branch_name: str, commit: str) -> None:
    run_command(["git", "checkout", "-B", branch_name, commit], cwd=repo_dir)

def push_branch(repo_dir: Path, branch_name: str) -> None:
    run_command(["git", "push", "rocm-github", branch_name], cwd=repo_dir, timeout=120)

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

        if dry_run:
            log.info("[DRY RUN] Would create and push branch %s for %s @ %s",
                     branch_name, repo_name, info.commit)
            successful[repo_name] = info
            continue

        try:
            create_branch(info.path, branch_name, info.commit)
        except subprocess.CalledProcessError as exc:
            failed[repo_name] = f"Branch creation failed: {exc}"
            continue

        try:
            push_branch(info.path, branch_name)
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
    parser.add_argument("-C", "--commitid", required=True, help="TheRock git ref (branch, tag, or SHA)")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exclude-list", nargs="*", default=[])
    parser.add_argument("--force-clone", action="store_true", default=False)
    parser.add_argument("--cache-dir", default=None)
    args = parser.parse_args(argv)

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
        update_submodules(plan["TheRock"].path)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    return execute_plan(plan, args.branch_name, args.dry_run)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
