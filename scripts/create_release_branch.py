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
1. Reuse (or populate) the cached TheRock clone; reclone only when the cache
    is missing or corrupt (`--force-clone` deletes and reclones when the cache
    directory exists but is not a valid git repo). Otherwise fetch/prune to
    pick up new commits.
2. Hard-reset to the requested commit and populate submodules via
    `fetch_sources.py` when available (fallback to `git submodule update`).
3. Build an execution plan from `.gitmodules` + `git submodule status`,
    capturing repo URL, commit SHA, and working tree path for each component
    plus TheRock itself. Repos listed in `--exclude-list` and repos outside
    the ROCm GitHub org are filtered out.
4. For each component:
    a. Set up the SSH `rocm-github` remote.
    b. Check if the release branch already exists on the remote; if so, skip
       the repo entirely (recorded as a failure).
    c. Create (or reset) the branch at the recorded commit.
    d. Push to `rocm-github` (skipped in dry-run mode).
5. Log a summary of successful and failed repos.

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
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat


@dataclass
class RepoInfo:
    """Information about a repository to branch."""

    url: str
    commit: str
    path: Path


class RockBranchingAutomation:
    """Automates creation of release branches for TheRock and its ROCm submodules."""

    def __init__(self, cli_args: argparse.Namespace) -> None:
        self.release_branch: str = cli_args.branch_name
        self.dry_run: bool = cli_args.dry_run
        self.commitid: str = cli_args.commitid

        if not re.fullmatch(r"[0-9a-f]{40}", self.commitid):
            raise SystemExit(
                f"ERROR: --commitid must be a full 40-character lowercase hex "
                f"SHA-1 hash, got: {self.commitid!r}"
            )

        self.exclude_list: set[str] = set(cli_args.exclude_list or [])
        self.force_clone: bool = cli_args.force_clone
        self.cache_dir: Path | None = (
            Path(cli_args.cache_dir) if cli_args.cache_dir else None
        )
        self.rock_url: str = "https://github.com/ROCm/TheRock.git"
        self.cache_root: Path | None = None

        self._logger = logging.getLogger("rock_branching")

        self.log("Authentication Mode: SSH")
        self.log(f"Dry run mode = {self.dry_run}")
        if self.exclude_list:
            self.log(f"Exclude list: {self.exclude_list}")

    def log(self, msg: str) -> None:
        """Log an info-level message."""
        self._logger.info(msg)

    def run_command(
        self,
        args: list[str | Path],
        cwd: Path,
        *,
        input_data: bytes | None = None,
        stream: bool = False,
        timeout: int | None = None,
    ) -> None:
        """Execute a subprocess command, raising CalledProcessError on failure.

        Args:
            args:       Command and arguments to execute.
            cwd:        Working directory for the command.
            input_data: Optional bytes piped to stdin.
            stream:     If True, print stdout/stderr line-by-line as it arrives
                        (useful for long-running operations like clone/fetch).
                        If False, buffer output and log after completion.
            timeout:    Maximum seconds to wait before raising TimeoutExpired.
        """
        cmd = args if isinstance(args, list) else [args]
        self.log(f"++ Exec [{cwd}]$ {shlex.join(map(str, cmd))}")
        sys.stdout.flush()

        if stream:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            for line in process.stdout:
                self.log(line.rstrip())

            ret = process.wait()
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)

            return

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                shell=False,
                input=input_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                stdin=None if input_data else subprocess.DEVNULL,
                text=False,
                timeout=timeout,
            )

            if result.stdout:
                self.log(
                    result.stdout
                    if isinstance(result.stdout, str)
                    else result.stdout.decode(errors="ignore")
                )
            if result.stderr:
                self.log(
                    result.stderr
                    if isinstance(result.stderr, str)
                    else result.stderr.decode(errors="ignore")
                )

        except subprocess.CalledProcessError as exc:
            self.log(
                (exc.stdout or b"").decode(errors="ignore")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            self.log(
                (exc.stderr or b"").decode(errors="ignore")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            raise

    def run_command_output(
        self, args: list[str | Path], cwd: Path, timeout: int | None = None
    ) -> str:
        """Run a command and return its stripped stdout as a string.

        Raises CalledProcessError on non-zero exit.
        Raises subprocess.TimeoutExpired when *timeout* seconds elapse.
        """
        cmd = args if isinstance(args, list) else [args]
        self.log(f"++ Exec [{cwd}]$ {shlex.join(map(str, cmd))}")

        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.stdout.strip()

    def _setup_remote(self, url: str, repo_dir: Path) -> None:
        """Add or update the rocm-github remote for a repo."""
        remote_url = self.convert_to_ssh(url)
        try:
            self.run_command(
                ["git", "remote", "set-url", "rocm-github", remote_url],
                cwd=repo_dir,
            )
        except subprocess.CalledProcessError:
            self.run_command(
                ["git", "remote", "add", "rocm-github", remote_url],
                cwd=repo_dir,
            )

    def _remote_branch_exists(self, repo_dir: Path) -> bool:
        """Return True if the release branch already exists on rocm-github.

        Raises CalledProcessError if the remote check itself fails.
        Raises subprocess.TimeoutExpired if the network call hangs (60 s).
        """
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
                f"[DRY RUN] Skipping push of {self.release_branch} "
                f"for {repo_name}"
            )
        else:
            self.run_command(
                ["git", "push", "rocm-github", self.release_branch],
                cwd=repo_dir,
                timeout=120,
            )

    def execute_plan(self, plan: dict[str, RepoInfo]) -> None:
        """Execute the branching plan for every repo in *plan*.

        For each repo:
        1. Set up the ``rocm-github`` remote with the SSH URL.
        2. Guard against a pre-existing remote branch (skip if found).
        3. Create (or reset) the release branch at the recorded commit SHA.
        4. Push to ``rocm-github`` (skipped in dry-run mode).
        """
        successful_repos: dict[str, RepoInfo] = {}
        skipped_repos: dict[str, str] = {}
        failed_repos: dict[str, str] = {}

        for repo_name, info in plan.items():
            self.log(f"Processing {repo_name} at {info.path}")

            if not info.path.exists():
                failed_repos[repo_name] = (
                    f"Repo path does not exist: {info.path}"
                )
                continue

            try:
                self._setup_remote(info.url, info.path)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = f"Remote setup failed: {exc}"
                continue

            try:
                branch_exists = self._remote_branch_exists(info.path)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = (
                    f"Remote branch check failed: {exc}"
                )
                continue

            if branch_exists:
                msg = (
                    f"Remote branch {self.release_branch} already exists "
                    "on rocm-github"
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
            except subprocess.CalledProcessError as exc:
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

    def convert_to_ssh(self, url: str) -> str:
        """Convert https://github.com/X/Y.git to git@github.com:X/Y.git."""
        if url.startswith("https://github.com/"):
            path = url.replace("https://github.com/", "")
            return f"git@github.com:{path}"
        return url

    def get_submodule_url_map(self, repo_dir: Path) -> dict[str, str]:
        """Return mapping of submodule working-tree paths to remote URLs."""
        gitmodules_path = repo_dir / ".gitmodules"
        if not gitmodules_path.exists():
            return {}

        try:
            path_entries = self.run_command_output(
                [
                    "git",
                    "config",
                    "--file",
                    str(gitmodules_path),
                    "--get-regexp",
                    r"submodule\..*\.path",
                ],
                cwd=repo_dir,
            )
        except subprocess.CalledProcessError:
            return {}

        url_map: dict[str, str] = {}
        for line in path_entries.splitlines():
            # Each line looks like:
            #   "submodule.external/hipcc.path external/hipcc"
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, path_value = parts
            section = key.rsplit(".", 1)[0]
            try:
                url = self.run_command_output(
                    [
                        "git",
                        "config",
                        "--file",
                        str(gitmodules_path),
                        "--get",
                        f"{section}.url",
                    ],
                    cwd=repo_dir,
                )
            except subprocess.CalledProcessError:
                self.log(f"No URL entry for {section}; skipping")
                continue
            url_map[path_value.strip()] = url

        return url_map

    def build_plan(self) -> dict[str, RepoInfo]:
        """Build the branching execution plan.

        1. Clone (or reuse cached clone of) TheRock.
        2. Check out and hard-reset to ``self.commitid``.
        3. Populate submodules via ``fetch_sources.py`` (or ``git submodule update``).
        4. Read ``git submodule status`` and ``.gitmodules`` to collect each
           submodule's commit SHA, remote URL, and local path.
        5. Return a dict keyed by repo name, including TheRock itself.
        """
        cache_root = (
            self.cache_dir
            or Path(tempfile.gettempdir()) / "rock-branching-cache"
        )
        cache_root.mkdir(parents=True, exist_ok=True)
        clone_dir = cache_root / "TheRock"
        self.cache_root = cache_root

        needs_clone = not clone_dir.exists()
        if not needs_clone and not (clone_dir / ".git").exists():
            if not self.force_clone:
                raise RuntimeError(
                    f"Cache directory {clone_dir} exists but is not a git "
                    "repo. Use --force-clone to delete it and reclone."
                )
            self.log(
                f"Cache directory {clone_dir} is not a git repo; "
                "removing before reclone (--force-clone)"
            )
            shutil.rmtree(clone_dir)
            needs_clone = True

        if needs_clone:
            self.log(
                f"Cloning TheRock repo from {self.rock_url} into {clone_dir}"
            )
            self.run_command(
                ["git", "clone", str(self.rock_url), str(clone_dir)],
                cwd=cache_root,
                stream=True,
            )
        else:
            self.log(f"Reusing existing TheRock repo at {clone_dir}")

            try:
                remote_url = self.run_command_output(
                    ["git", "remote", "get-url", "origin"],
                    cwd=clone_dir,
                )
                if "TheRock" not in remote_url:
                    raise RuntimeError(
                        f"Existing repo at {clone_dir} does not look like "
                        f"TheRock (origin={remote_url})"
                    )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    f"Failed to inspect existing repo at {clone_dir}: {exc}"
                ) from exc

            self.log("Fetching latest changes for existing TheRock clone...")
            self.run_command(
                [
                    "git",
                    "fetch",
                    "origin",
                    "--prune",
                    "--recurse-submodules=on-demand",
                ],
                cwd=clone_dir,
                stream=True,
            )

        fetch_script = clone_dir / "build_tools" / "fetch_sources.py"
        rock_commit = self.commitid

        self.log(f"Checking out TheRock at commit {rock_commit}")
        self.run_command(["git", "checkout", rock_commit], cwd=clone_dir)
        self.run_command(
            ["git", "reset", "--hard", rock_commit], cwd=clone_dir
        )

        if fetch_script.exists():
            self.log(
                "Updating submodules via fetch_sources.py "
                "(jobs=10, no patches)..."
            )
            self.run_command(
                [
                    "python3",
                    str(fetch_script),
                    "--jobs",
                    "10",
                    "--no-apply-patches",
                ],
                cwd=clone_dir,
                stream=True,
            )
        else:
            self.log(
                "fetch_sources.py not found; "
                "falling back to git submodule update"
            )
            self.run_command(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=clone_dir,
                stream=True,
            )

        self.log("Reading submodule status...")
        try:
            status_output = self.run_command_output(
                ["git", "submodule", "status"],
                cwd=clone_dir,
            )
            lines = status_output.split("\n") if status_output else []
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to read submodule status: {exc}"
            ) from exc

        url_map = self.get_submodule_url_map(clone_dir)

        plan: dict[str, RepoInfo] = {}

        # Each line from `git submodule status` looks like:
        #   " <sha> <path> (<describe>)"  or  "-<sha> <path>" (not initialized)
        for line in lines:
            if not line:
                continue

            parts = line.split()
            if len(parts) < 2:
                continue
            sha = parts[0].lstrip("-+")
            path = parts[1]

            repo_name = Path(path).name
            repo_url = url_map.get(path)

            if not repo_url:
                self.log(
                    f"No URL found for submodule {path} in .gitmodules"
                )
                continue

            if repo_name in self.exclude_list:
                self.log(f"Skipping {repo_name} (in exclude list)")
                continue

            url_lower = repo_url.lower()
            if (
                "github.com/rocm/" not in url_lower
                and "github.com:rocm/" not in url_lower
            ):
                self.log(
                    f"Skipping {repo_name} "
                    f"(not a ROCm org repo: {repo_url})"
                )
                continue

            plan[repo_name] = RepoInfo(
                url=repo_url,
                commit=sha,
                path=clone_dir / path,
            )

        plan["TheRock"] = RepoInfo(
            url=self.rock_url,
            commit=rock_commit,
            path=clone_dir,
        )

        return plan

    def run(self) -> None:
        """Build the execution plan and execute it."""
        plan = self.build_plan()
        self.log(f"Execution plan:\n{pformat(plan)}")
        self.execute_plan(plan)


def main(argv: list[str]) -> int:
    """Parse arguments and run the branching automation."""
    parser = argparse.ArgumentParser(
        description="Rock Branching Automation Tool",
    )
    parser.add_argument(
        "-B",
        "--branch-name",
        required=True,
        help="Name of the release branch to create",
    )
    parser.add_argument(
        "-C",
        "--commitid",
        required=True,
        help="Commit ID of TheRock to branch from",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Log actions without pushing to remotes (default: enabled)",
    )
    parser.add_argument(
        "--exclude-list",
        nargs="*",
        default=[],
        help="List of submodule repo names to exclude from branching",
    )
    parser.add_argument(
        "--force-clone",
        action="store_true",
        default=False,
        help="Delete and reclone if cache directory exists but is not a "
        "valid git repo",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directory to cache the TheRock clone "
        "(default: /tmp/rock-branching-cache)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    RockBranchingAutomation(args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))