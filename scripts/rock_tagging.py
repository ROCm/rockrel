#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
ROCm TheRock – Release Tag Automation Tool
---------------------------------------------

Automates tag and GitHub release creation for TheRock plus every tracked
submodule. Authentication is done via SSH.

High-level workflow:
1. Reuse (or populate) a cached clone under a configurable directory
    (default: `/tmp/rock-tagging-cache`, overridable via `--cache-dir`),
    fetch the latest refs, and hard-reset to the user-specified commit.
2. Update submodules via `fetch_sources.py` when available (fallback to
    `git submodule update`) and build a plan by combining `.gitmodules`
    metadata with `git submodule status` output. Repos listed in
    `--exclude-list` and repos outside the ROCm GitHub org are skipped.
3. For each component (inside a single loop):
    a. Configure an SSH `rocm-github` remote.
    b. Create an annotated tag (`therock-<version>`) at the recorded
       commit, skipping components where the tag already exists.
    c. For mono-repos (`rocm-libraries`, `rocm-systems`), generate
       tarballs for the `projects/` and `shared/` directories (tarballs
       are created even in dry-run mode).
    d. When not in dry-run mode, push the tag and invoke
       `gh release create` with the appropriate notes and tarball assets.

Use `--force-clone` to delete and reclone when the cache directory exists
but is not a valid git repo. All actions are logged for traceability, and
dry-run mode (the default) lets you preview the plan without touching
remotes.

Usage:
        python rock-tagging.py \\
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
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat


@dataclass
class RepoInfo:
    """Information about a repository to tag."""

    url: str
    commit: str
    path: Path


class RockTagging:
    """Automates tagging and release uploading for TheRock."""

    MONO_REPOS = frozenset({"rocm-libraries", "rocm-systems"})

    def __init__(self, cli_args: argparse.Namespace) -> None:
        self.release_branch: str = cli_args.branch_name
        self.release_version: str = cli_args.release_version
        self.dry_run: bool = cli_args.dry_run
        self.commitid: str = cli_args.commitid
        self.exclude_list: set[str] = set(cli_args.exclude_list or [])
        self.force_clone: bool = cli_args.force_clone
        self.cache_dir: Path | None = (
            Path(cli_args.cache_dir) if cli_args.cache_dir else None
        )
        self.rock_url: str = "https://github.com/ROCm/TheRock.git"
        self.cache_root: Path | None = None

        self._logger = logging.getLogger("rock_tagging")

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
    ) -> None:
        """Execute a subprocess command, raising CalledProcessError on failure.

        Args:
            args:       Command and arguments to execute.
            cwd:        Working directory for the command.
            input_data: Optional bytes piped to stdin.
            stream:     If True, print stdout/stderr line-by-line as it arrives
                        (useful for long-running operations like clone/fetch).
                        If False, buffer output and log after completion.
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

    def run_command_output(self, args: list[str | Path], cwd: Path) -> str:
        """Run a command and return its stripped stdout as a string.

        Raises CalledProcessError on non-zero exit.
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
        )
        return result.stdout.strip()

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
        """Build the tagging execution plan.

        1. Clone (or reuse cached clone of) TheRock.
        2. Check out and hard-reset to ``self.commitid``.
        3. Populate submodules via ``fetch_sources.py`` (or ``git submodule update``).
        4. Read ``git submodule status`` and ``.gitmodules`` to collect each
           submodule's commit SHA, remote URL, and local path.
        5. Return a dict keyed by repo name, including TheRock itself.
        """
        cache_root = (
            self.cache_dir
            or Path(tempfile.gettempdir()) / "rock-tagging-cache"
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

    def _create_tarballs(
        self,
        root_dir: Path,
        source_dir: Path,
        tarball_paths: list[Path],
        label: str,
    ) -> None:
        """Create per-subdirectory tarballs from *source_dir*."""
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

    def execute_plan(self, plan: dict[str, RepoInfo]) -> None:
        """Execute the tagging plan for every repo in *plan*.

        For each repo:
        1. Configure the SSH ``rocm-github`` remote.
        2. Create an annotated tag at the recorded commit.
        3. For mono-repos, generate tarballs.
        4. Push the tag and create a GitHub release (skipped in dry-run mode).
        """
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

            # Skip if tag already exists locally
            tag_exists = subprocess.run(
                ["git", "rev-parse", "-q", "--verify", tag_name],
                cwd=info.path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0

            if tag_exists:
                self.log(
                    f"Tag {tag_name} already exists for {comp}; "
                    "skipping creation"
                )
                successful_components[comp] = info
                continue

            try:
                self.run_command(
                    [
                        "git",
                        "tag",
                        "-a",
                        tag_name,
                        info.commit,
                        "-m",
                        f"therock release v{self.release_version}",
                    ],
                    cwd=info.path,
                )
                if not self.dry_run:
                    self.run_command(
                        [
                            "git",
                            "push",
                            "rocm-github",
                            f"{tag_name}:refs/tags/{tag_name}",
                        ],
                        cwd=info.path,
                    )
                successful_components[comp] = info
            except subprocess.CalledProcessError as exc:
                failed_components[comp] = f"Tag failed: {exc}"
                continue

            # Tarballs only for mono-repos
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
                    release_cmd = [
                        "gh",
                        "release",
                        "create",
                        tag_name,
                        "--notes",
                        f"therock release v{self.release_version}",
                        *[str(p) for p in tarballs],
                    ]
                    self.run_command(release_cmd, cwd=info.path)
                except subprocess.CalledProcessError as exc:
                    failed_components[comp] = (
                        f"Release creation failed: {exc}"
                    )

        self.log(
            f"Summary: {len(successful_components)} succeeded, "
            f"{len(failed_components)} failed out of {len(plan)} repos"
        )
        if successful_components:
            self.log(f"Successful components: {pformat(successful_components)}")
        if failed_components:
            self.log(f"Failed components: {pformat(failed_components)}")

    def run(self) -> None:
        """Build the execution plan and execute it."""
        plan = self.build_plan()
        self.log(f"Execution plan: {pformat(plan)}")
        self.execute_plan(plan)


def main(argv: list[str]) -> int:
    """Parse arguments and run the tagging automation."""
    parser = argparse.ArgumentParser(
        description="Rock Tagging Automation Tool",
    )
    parser.add_argument(
        "-B",
        "--branch-name",
        required=True,
        help="Name of the release branch",
    )
    parser.add_argument(
        "-V",
        "--release-version",
        required=True,
        help="Release version string (used for tag names)",
    )
    parser.add_argument(
        "-C",
        "--commitid",
        required=True,
        help="Commit ID of TheRock to tag from",
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
        help="List of submodule repo names to exclude from tagging",
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
        "(default: /tmp/rock-tagging-cache)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(message)s"
    )

    RockTagging(args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))