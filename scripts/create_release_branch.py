#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
ROCm TheRock – Release Branch Automation Tool
--------------------------------------------

Creates release branches for TheRock and every tracked ROCm submodule at a
caller-provided commit. The script maintains a cached clone (configurable via
`--cache-dir`, default: `/tmp/rock-branching-cache`), fetches the latest refs,
and hard-resets to the specified commit before creating release branches.

Authentication is done via SSH. Remotes are configured via
`git remote set-url` (with `add` fallback).

High-level workflow:
1. Verify GitHub push/admin permissions for all repos (via GitHub API, no
   clone required).
2. Reuse (or populate) the cached TheRock clone; reclone only when the cache
   is missing or corrupt (`--force-clone` deletes and reclones).
3. Hard-reset to the requested commit and populate submodules via
   `fetch_sources.py` when available (fallback to `git submodule update`).
4. Build an execution plan from `.gitmodules` + `git submodule status`.
5. For each component:
   a. Set up the SSH `rocm-github` remote.
   b. Check if the release branch already exists on the remote; skip if so.
   c. Create (or reset) the branch at the recorded commit.
   d. Push to `rocm-github` (skipped in dry-run mode).
6. Log a summary of successful, skipped, and failed repos.

Dry-run mode (the default) logs every action without touching remotes;
`--no-dry-run` enables actual pushes.

Usage:
        python create_release_branch.py \\
                --branch-name <release-branch> \\
                --commitid <rock-commit-sha> \\
                [--no-dry-run]

Options:
        --branch-name    Name of the release branch to create (required)
        --commitid       Commit SHA of TheRock to branch from (required)
        --dry-run/--no-dry-run
                         Log actions without pushing to remotes (default: enabled)
        --exclude-list   Submodule repo names to skip (space-separated)
        --force-clone    Delete and reclone if cache dir is not a valid git repo
        --cache-dir      Directory to cache the TheRock clone
                         (default: /tmp/rock-branching-cache)
"""
import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path
from pprint import pformat

from release_utils import (
    RockBase,
    RepoInfo,
    check_permissions,
    fetch_lightweight_plan,
    get_gh_token,
)


class RockBranchingAutomation(RockBase):
    """Automates creation of release branches for TheRock and its ROCm submodules."""

    _cache_dir_name = "rock-branching-cache"

    def __init__(self, cli_args: argparse.Namespace) -> None:
        super().__init__(cli_args)

        if not re.fullmatch(r"[0-9a-f]{40}", self.commitid):
            raise SystemExit(
                f"ERROR: --commitid must be a full 40-character lowercase hex "
                f"SHA-1 hash, got: {self.commitid!r}"
            )

        self.log("Authentication Mode: SSH")
        self.log(f"Dry run mode = {self.dry_run}")
        if self.exclude_list:
            self.log(f"Exclude list: {self.exclude_list}")

    def _remote_branch_exists(self, repo_dir: Path) -> bool:
        """Return True if the release branch already exists on rocm-github."""
        output = self.run_command_output(
            ["git", "ls-remote", "--heads", "rocm-github", self.release_branch],
            cwd=repo_dir,
            timeout=60,
        )
        return bool(output)

    def _create_branch(self, commit: str, repo_dir: Path) -> None:
        """Create (or reset) the release branch at the given commit."""
        self.run_command(
            ["git", "checkout", "-B", self.release_branch, commit],
            cwd=repo_dir,
        )

    def _push_branch(self, repo_name: str, repo_dir: Path) -> None:
        """Push the release branch to rocm-github, respecting dry-run mode."""
        if self.dry_run:
            self.log(
                f"[DRY RUN] Skipping push of {self.release_branch} for {repo_name}"
            )
        else:
            self.run_command(
                ["git", "push", "rocm-github", self.release_branch],
                cwd=repo_dir,
                timeout=120,
            )

    def execute_plan(self, plan: dict[str, RepoInfo]) -> None:
        """Create and push release branches for every repo in the plan."""
        successful_repos: dict[str, RepoInfo] = {}
        skipped_repos: dict[str, str] = {}
        failed_repos: dict[str, str] = {}

        for repo_name, info in plan.items():
            self.log(f"Processing {repo_name} at {info.path}")

            if not info.path.exists():
                failed_repos[repo_name] = f"Repo path does not exist: {info.path}"
                continue

            try:
                self._setup_remote(info.url, info.path)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = f"Remote setup failed: {exc}"
                continue

            try:
                branch_exists = self._remote_branch_exists(info.path)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                failed_repos[repo_name] = f"Remote branch check failed: {exc}"
                continue

            if branch_exists:
                msg = (
                    f"Remote branch {self.release_branch} already exists on rocm-github"
                )
                self.log(msg)
                skipped_repos[repo_name] = msg
                continue

            try:
                self._create_branch(info.commit, info.path)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = (
                    f"Branch creation failed at {info.commit}: {exc}"
                )
                continue

            try:
                self._push_branch(repo_name, info.path)
                successful_repos[repo_name] = info
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                failed_repos[repo_name] = f"Branch push failed: {exc}"

        self.log(
            f"Summary: {len(successful_repos)} succeeded, "
            f"{len(skipped_repos)} skipped, "
            f"{len(failed_repos)} failed out of {len(plan)} repos"
        )
        if successful_repos:
            self.log(f"Successful repos: {pformat(successful_repos)}")
        if skipped_repos:
            self.log(f"Skipped repos (branch already exists): {pformat(skipped_repos)}")
        if failed_repos:
            self.log(f"Failed repos: {pformat(failed_repos)}")

    def run(self) -> None:
        """Check permissions, build the plan, and execute it."""
        token = get_gh_token()
        repo_map = fetch_lightweight_plan(token, self.commitid, self.exclude_list)
        check_permissions(token, repo_map, self._logger, action="branches")

        plan = self.build_plan()
        self.log(f"Execution plan:\n{pformat(plan)}")
        self.execute_plan(plan)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Rock Branching Automation Tool")
    parser.add_argument(
        "-B", "--branch-name", required=True,
        help="Name of the release branch to create",
    )
    parser.add_argument(
        "-C", "--commitid", required=True,
        help="Commit SHA of TheRock to branch from",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Log actions without pushing to remotes (default: enabled)",
    )
    parser.add_argument(
        "--exclude-list", nargs="*", default=[],
        help="Submodule repo names to exclude from branching",
    )
    parser.add_argument(
        "--force-clone", action="store_true", default=False,
        help="Delete and reclone if cache dir exists but is not a valid git repo",
    )
    parser.add_argument(
        "--cache-dir", default=None,
        help="Directory to cache the TheRock clone (default: /tmp/rock-branching-cache)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        RockBranchingAutomation(args).run()
        return 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logging.error("Command failed: %s", exc)
        return 1
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1
    except Exception as exc:
        logging.error("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
