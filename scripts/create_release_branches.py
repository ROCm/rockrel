#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Create release branches for TheRock and all tracked ROCm submodules.

Dry-run mode (default) never creates or pushes a branch. It does NOT control
whether TheRock is checked out/updated: by default this script always clones
or fetches TheRock, resets it to --commitid, and runs fetch_sources.py to
populate submodules, since discovering the submodule plan requires that repo
state to exist somewhere. Pass --skip-checkout (with --cache-dir pointing at
an existing, already-checked-out clone) to make dry-run fully read-only.

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

from checkout_therock import checkout_therock, default_clone_dir
from release_utils import resolve_git_ref, run_command, setup_remote, TIMEOUT_SHORT_SECONDS
from discover_submodules import RepoInfo, build_plan

log = logging.getLogger("rock_release")

def remote_branch_exists(repo_dir: Path, branch_name: str) -> bool:
    output = run_command(
        ["git", "ls-remote", "--heads", "rocm-github", branch_name],
        cwd=repo_dir,
        timeout=TIMEOUT_SHORT_SECONDS,
        capture=True,
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
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                        help="Log planned actions without making any git changes (default: enabled)")
    parser.add_argument("--exclude-list", nargs="*", default=[])
    parser.add_argument("--include-list", nargs="*", default=[])
    parser.add_argument("--force-clone", action="store_true", default=False)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--skip-checkout", action="store_true", default=False,
        help="Do not clone/fetch/reset TheRock or run fetch_sources.py; assume "
             "--cache-dir already holds TheRock checked out at --commitid. "
             "Makes the full run (including dry-run) read-only.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Dry run mode = %s", args.dry_run)

    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    try:
        if args.skip_checkout:
            clone_dir = default_clone_dir(cache_dir)
            if not clone_dir.exists():
                raise RuntimeError(
                    f"--skip-checkout set but no clone found at {clone_dir}; "
                    "run checkout_therock.py first or omit --skip-checkout."
                )
            log.info("--skip-checkout set; using existing clone at %s as-is", clone_dir)
            try:
                canonical_sha = resolve_git_ref(args.commitid, clone_dir)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    f"Failed to resolve {args.commitid!r} in existing clone at {clone_dir}: {exc}"
                ) from exc
        else:
            clone_dir, canonical_sha = checkout_therock(
                commitid=args.commitid,
                cache_dir=cache_dir,
                force_clone=args.force_clone,
            )
        plan = build_plan(
            clone_dir=clone_dir,
            commitid=canonical_sha,
            exclude_list=set(args.exclude_list),
            include_list=set(args.include_list),
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    return execute_plan(plan, args.branch_name, args.dry_run)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
