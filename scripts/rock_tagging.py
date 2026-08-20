#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
ROCm TheRock – Release Tag Automation Tool
---------------------------------------------

Automates tag and GitHub release creation for TheRock plus every tracked
submodule. Authentication is done via SSH.

High-level workflow:
1. Verify GitHub push/admin permissions for all repos (via GitHub API, no
   clone required).
2. Reuse (or populate) a cached clone under a configurable directory
   (default: `/tmp/rock-tagging-cache`, overridable via `--cache-dir`),
   fetch the latest refs, and hard-reset to the user-specified commit.
3. Update submodules via `fetch_sources.py` when available (fallback to
   `git submodule update`) and build a plan by combining `.gitmodules`
   metadata with `git submodule status` output. Repos listed in
   `--exclude-list` and repos outside the ROCm GitHub org are skipped.
4. For each component:
   a. Configure an SSH `rocm-github` remote.
   b. Create an annotated tag (`therock-<version>`) at the recorded
      commit, skipping components where the tag already exists.
   c. For mono-repos (`rocm-libraries`, `rocm-systems`), generate
      tarballs for the `projects/` and `shared/` directories.
   d. When not in dry-run mode, push the tag and invoke
      `gh release create` with the appropriate notes and tarball assets.

Dry-run mode (the default) lets you preview the plan without touching remotes.

Usage:
        python rock_tagging.py \\
                --branch-name <release-branch> \\
                --release-version <version> \\
                --commitid <rock-commit-sha> \\
                [--no-dry-run]

Options:
        --branch-name    Name of the release branch (required)
        --release-version
                         Release version string, used for tag names (required)
        --commitid       Commit SHA of TheRock to tag from (required)
        --dry-run/--no-dry-run
                         Log actions without pushing to remotes (default: enabled)
        --exclude-list   Submodule repo names to skip (space-separated)
        --force-clone    Delete and reclone if cache dir is not a valid git repo
        --cache-dir      Directory to cache the TheRock clone
                         (default: /tmp/rock-tagging-cache)
"""
import argparse
import logging
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from pprint import pformat

from release_utils import (
    RockBase,
    RepoInfo,
    TIMEOUT_LONG,
    check_permissions,
    fetch_lightweight_plan,
    get_gh_token,
)


class RockTagging(RockBase):
    """Automates tagging and release uploading for TheRock."""

    _cache_dir_name = "rock-tagging-cache"

    MONO_REPOS = frozenset({"rocm-libraries", "rocm-systems"})

    def __init__(self, cli_args: argparse.Namespace) -> None:
        super().__init__(cli_args)
        self.release_version: str = cli_args.release_version

        self.log("Authentication Mode: SSH")
        self.log(f"Dry run mode = {self.dry_run}")
        if self.exclude_list:
            self.log(f"Exclude list: {self.exclude_list}")

    def _checkout_and_update_submodules(self, clone_dir: Path) -> None:
        """Fetch the release branch then delegate to the base checkout logic."""
        rock_commit = self.commitid
        self.log(
            f"Fetching release branch '{self.release_branch}' to ensure "
            f"commit {rock_commit} is reachable..."
        )
        self.run_command(
            [
                "git", "fetch", "origin",
                f"refs/heads/{self.release_branch}:"
                f"refs/remotes/origin/{self.release_branch}",
            ],
            cwd=clone_dir,
            stream=True,
            timeout=TIMEOUT_LONG,
        )
        super()._checkout_and_update_submodules(clone_dir)

    def _create_tarballs(
        self,
        root_dir: Path,
        source_dir: Path,
        tarball_paths: list[Path],
        label: str,
    ) -> None:
        """Create per-subdirectory tarballs from source_dir."""
        if not source_dir.is_dir():
            self.log(f"Source directory not found for {label}: {source_dir}")
            return

        self.log(f"Creating tarballs for {label}")
        for entry in sorted(source_dir.iterdir()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            tarball_path = root_dir / f"{entry.name}.tar.gz"
            if tarball_path in tarball_paths:
                continue
            with tarfile.open(tarball_path, "w:gz") as tf:
                tf.add(str(entry), arcname=entry.name)
            tarball_paths.append(tarball_path)
            self.log(f"Tarball created: {tarball_path}")

    def execute_plan(self, plan: dict[str, RepoInfo]) -> None:
        """Create and push tags, and publish GitHub releases, for every repo."""
        successful_components: dict[str, RepoInfo] = {}
        failed_components: dict[str, str] = {}
        work_dir = self.cache_root or Path(tempfile.gettempdir())
        work_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"Working directory: {work_dir}")

        tag_name = f"therock-{self.release_version}"

        for comp, info in plan.items():
            try:
                self._setup_remote(info.url, info.path)
            except subprocess.CalledProcessError as exc:
                failed_components[comp] = f"Remote setup failed: {exc}"
                continue

            tag_exists = subprocess.run(
                ["git", "rev-parse", "-q", "--verify", tag_name],
                cwd=info.path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0

            if tag_exists:
                self.log(f"Tag {tag_name} already exists for {comp}; skipping creation")
                successful_components[comp] = info
                continue

            try:
                self.run_command(
                    [
                        "git", "tag", "-a", tag_name, info.commit,
                        "-m", f"therock release v{self.release_version}",
                    ],
                    cwd=info.path,
                )
                if not self.dry_run:
                    self.run_command(
                        ["git", "push", "rocm-github", f"{tag_name}:refs/tags/{tag_name}"],
                        cwd=info.path,
                    )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                failed_components[comp] = f"Tag failed: {exc}"
                continue

            tarballs: list[Path] = []
            if comp in self.MONO_REPOS:
                self._create_tarballs(
                    info.path, info.path / "projects", tarballs, "projects"
                )
                self._create_tarballs(
                    info.path, info.path / "shared", tarballs, "shared"
                )

            if self.dry_run:
                self.log(f"[DRY RUN] Would create release with: {tarballs}")
            else:
                try:
                    self.run_command(
                        [
                            "gh", "release", "create", tag_name,
                            "--notes", f"therock release v{self.release_version}",
                            *[str(p) for p in tarballs],
                        ],
                        cwd=info.path,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    failed_components[comp] = f"Release creation failed: {exc}"
                    continue

            successful_components[comp] = info

        self.log(
            f"Summary: {len(successful_components)} succeeded, "
            f"{len(failed_components)} failed out of {len(plan)} repos"
        )
        if successful_components:
            self.log(f"Successful components: {pformat(successful_components)}")
        if failed_components:
            self.log(f"Failed components: {pformat(failed_components)}")

    def run(self) -> None:
        """Check permissions, build the plan, and execute it."""
        token = get_gh_token()
        repo_map = fetch_lightweight_plan(token, self.commitid, self.exclude_list)
        check_permissions(token, repo_map, self._logger, action="tags")

        plan = self.build_plan()
        self.log(f"Execution plan: {pformat(plan)}")
        self.execute_plan(plan)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Rock Tagging Automation Tool")
    parser.add_argument(
        "-B", "--branch-name", required=True,
        help="Name of the release branch",
    )
    parser.add_argument(
        "-V", "--release-version", required=True,
        help="Release version string (used for tag names)",
    )
    parser.add_argument(
        "-C", "--commitid", required=True,
        help="Commit SHA of TheRock to tag from",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Log actions without pushing to remotes (default: enabled)",
    )
    parser.add_argument(
        "--exclude-list", nargs="*", default=[],
        help="Submodule repo names to exclude from tagging",
    )
    parser.add_argument(
        "--force-clone", action="store_true", default=False,
        help="Delete and reclone if cache dir exists but is not a valid git repo",
    )
    parser.add_argument(
        "--cache-dir", default=None,
        help="Directory to cache the TheRock clone (default: /tmp/rock-tagging-cache)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    try:
        RockTagging(args).run()
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
