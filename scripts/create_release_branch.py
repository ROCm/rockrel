#!/usr/bin/env python3
"""
ROCm TheRock – Release Branch Automation Tool
--------------------------------------------

Creates release branches for TheRock and every tracked ROCm submodule at a
caller-provided commit. The script maintains a cached clone (configurable via
`--cache-dir`, default: `/tmp/rock-branching-cache`), fetches the latest refs,
and hard-resets to the specified commit before creating release branches.

Authentication supports both API tokens and SSH. When a token is supplied we
call `gh auth login --with-token` so git operations transparently use the
credential helper. Remotes are configured via `git remote set-url` (with `add`
fallback).

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
    a. Set up the authenticated `rocm-github` remote (token or SSH).
    b. Check if the release branch already exists on the remote; if so, skip
       the repo entirely (recorded as a failure).
    c. Create (or reset) the branch at the recorded commit.
    d. Push to `rocm-github` (skipped in dry-run mode).
5. Log a summary of successful and failed repos.

Dry-run mode (the default) logs every action without touching remotes;
`--no-dry-run` enables actual pushes.

Usage:
        python create_release_branch.py \
                --branch_name <release-branch> \
                --commitid <rock-commit-sha> \
                [--apitoken <github-token>] \
                [--no-dry-run]

Options:
        --branch_name    Name of the release branch to create (required)
        --commitid       Commit SHA of TheRock to branch from (required)
        --apitoken       GitHub API token (optional; SSH is used if omitted)
        --no-dry-run     Actually push branches to remotes (default is dry-run)
        --exclude-list   Submodule repo names to skip (space-separated)
        --force-clone    Delete and reclone if cache dir is not a valid git repo
        --cache-dir      Directory to cache the TheRock clone
                         (default: /tmp/rock-branching-cache)
"""
import argparse
import logging
import sys
import subprocess
import tempfile
from pathlib import Path
import shlex
import shutil
from pprint import pformat
from typing import Dict, Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class RockBranchingAutomation:
    """Automates creation of release branches for TheRock and its ROCm submodules."""

    def __init__(self, cli_args: argparse.Namespace) -> None:
        # Collect CLI options
        self.release_branch: str | None = cli_args.branch_name
        self.api_token: str | None = cli_args.apitoken
        self.dry_run = cli_args.dry_run
        self.commitid: str | None = cli_args.commitid
        self.exclude_list: set[str] = set(cli_args.exclude_list or [])
        self.force_clone: bool = cli_args.force_clone
        self.cache_dir: Path | None = Path(cli_args.cache_dir) if cli_args.cache_dir else None
        self.rock_url: str = "https://github.com/ROCm/TheRock.git"
        self.cache_root: Path | None = None

        # Configure structured logging
        self._logger = logging.getLogger("rock_branching")

        mode = "API token mode" if self.api_token else "SSH mode"
        self.log(f"Authentication Mode: {mode}")
        self.log(f"Dry run mode = {self.dry_run}")
        if self.exclude_list:
            self.log(f"Exclude list: {self.exclude_list}")

    def log(self, msg: str) -> None:
        """Common method for logging info messages."""
        self._logger.info(msg)

    def run_command(
            self, args: list[str | Path], cwd: Path, *, input: bytes | None = None, stream: bool = False
            ) -> None:
        """
        Execute a subprocess command, raising CalledProcessError on failure.

        Args:
            args:   Command and arguments to execute.
            cwd:    Working directory for the command.
            input:  Optional bytes piped to stdin (e.g. for ``gh auth login --with-token``).
            stream: If True, print stdout/stderr line-by-line as it arrives
                    (useful for long-running operations like clone/fetch).
                    If False, buffer output and log after completion.
        """

        def mask(text: str | bytes) -> str:
            if isinstance(text, bytes):
                text = text.decode(errors="ignore")
            if not text or not self.api_token:
                return text or ""
            return text.replace(self.api_token, "***")

        cmd = args if isinstance(args, list) else [args]
        masked_cmd = mask(shlex.join(map(str, cmd)))
        self.log(f"++ Exec [{cwd}]$ {masked_cmd}")
        sys.stdout.flush()

        if stream:
            # STREAM OUTPUT LIVE
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            for line in process.stdout:
                self.log(mask(line.rstrip()))

            ret = process.wait()
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)

            return

        # NORMAL (buffered) MODE
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                shell=False,
                input=input,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                stdin=None if input else subprocess.DEVNULL,
                text=False,
            )

            if result.stdout:
                self.log(mask(result.stdout))
            if result.stderr:
                self.log(mask(result.stderr))

        except subprocess.CalledProcessError as exc:
            self.log(mask(exc.stdout or b""))
            self.log(mask(exc.stderr or b""))
            raise

    def run_command_output(
            self, args: list[str | Path], cwd: Path) -> str:
        """
        Run a command and return its stripped stdout as a string.
        Raises CalledProcessError on non-zero exit.
        """
        cmd = args if isinstance(args, list) else [args]
        masked_cmd = shlex.join(map(str, cmd))
        if self.api_token:
            masked_cmd = masked_cmd.replace(self.api_token, "***")
        self.log(f"++ Exec [{cwd}]$ {masked_cmd}")

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

    def _authenticate_gh(self) -> None:
        """Authenticate with GitHub CLI if an API token is available."""
        auth_dir = self.cache_root or Path(tempfile.gettempdir())
        auth_dir.mkdir(parents=True, exist_ok=True)
        if self.api_token:
            try:
                self.run_command(["gh", "--version"], cwd=auth_dir)
                self.run_command(
                    ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
                    cwd=auth_dir,
                    input=self.api_token.encode(),
                )
            except subprocess.CalledProcessError as exc:
                self.log(f"GitHub CLI authentication failed: {exc}")
                self.log("Continuing with git operations (may fail if not authenticated via SSH)...")
        else:
            self.log("Skipping gh auth login (SSH mode). Assuming user already authenticated.")

    def _setup_remote(self, url: str, repo_dir: Path) -> None:
        """Add or update the rocm-github remote for a repo."""
        remote_url = self.tokenize_url(url)
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
        """
        output = self.run_command_output(
            ["git", "ls-remote", "--heads", "rocm-github", self.release_branch],
            cwd=repo_dir,
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
            self.log(f"[DRY RUN] Skipping push of {self.release_branch} for {repo_name}")
        else:
            self.run_command(
                ["git", "push", "rocm-github", self.release_branch],
                cwd=repo_dir,
            )

    def execute_plan(
            self, plan: Dict[str, Dict[str, str]]) -> None:
        """
        Execute the branching plan for every repo in *plan*:
        1. Set up the ``rocm-github`` remote with the authenticated URL.
        2. Guard against a pre-existing remote branch (skip if found).
        3. Create (or reset) the release branch at the recorded commit SHA.
        4. Push to ``rocm-github`` (skipped in dry-run mode).
        """
        successful_repos: Dict[str, Dict[str, str]] = {}
        failed_repos: Dict[str, Dict[str, Any]] = {}

        self._authenticate_gh()

        for repo_name, meta in plan.items():
            url = meta.get("url")
            commit = meta.get("commit")
            repo_dir = Path(meta.get("path"))

            self.log(f"Processing {repo_name} at {repo_dir}")

            if not repo_dir.exists():
                failed_repos[repo_name] = {"error": f"Repo path does not exist: {repo_dir}"}
                continue

            try:
                self._setup_remote(url, repo_dir)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = {"error": f"Remote setup failed: {exc}"}
                continue

            try:
                branch_exists = self._remote_branch_exists(repo_dir)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = {"error": f"Remote branch check failed: {exc}"}
                continue

            if branch_exists:
                msg = f"Remote branch {self.release_branch} already exists on rocm-github"
                self.log(msg)
                failed_repos[repo_name] = {"error": msg}
                continue

            try:
                self._create_branch(commit, repo_dir)
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = {
                    "error": f"Branch creation failed at {commit}: {exc}"
                }
                continue

            try:
                self._push_branch(repo_name, repo_dir)
                successful_repos[repo_name] = {
                    "url": url,
                    "commit": commit,
                    "branch": self.release_branch,
                }
            except subprocess.CalledProcessError as exc:
                failed_repos[repo_name] = {"error": f"Branch push failed: {exc}"}

        self.log(f"Summary: {len(successful_repos)} succeeded, {len(failed_repos)} failed out of {len(plan)} repos")
        if successful_repos:
            self.log(f"Successful repos: {pformat(successful_repos)}")
        if failed_repos:
            self.log(f"Failed repos: {pformat(failed_repos)}")

    
    def convert_to_ssh(self, url: str) -> str:
        """Convert https://github.com/X/Y.git → git@github.com:X/Y.git"""
        if url.startswith("https://github.com/"):
            path = url.replace("https://github.com/", "")
            return f"git@github.com:{path}"
        return url

    def tokenize_url(self, url: str) -> str:
        """Return HTTPS+token URL if token provided, else SSH URL."""
        if self.api_token:
            return url.replace("https://", f"https://{self.api_token}@")
        else:
            # SSH mode → convert URL to SSH
            return self.convert_to_ssh(url)

    def get_submodule_url_map(self, repo_dir: Path) -> Dict[str, str]:
        """Return mapping of submodule working-tree paths → remote URLs from .gitmodules."""
        gitmodules_path = repo_dir / ".gitmodules"
        if not gitmodules_path.exists():
            return {}

        try:
            path_entries = self.run_command_output(
                [
                    "git", "config", "--file", str(gitmodules_path),
                    "--get-regexp", r"submodule\..*\.path",
                ],
                cwd=repo_dir,
            )
        except subprocess.CalledProcessError:
            return {}

        url_map: Dict[str, str] = {}
        for line in path_entries.splitlines():
            # Each line is from `git config --get-regexp` and looks like:
            #   "submodule.external/hipcc.path external/hipcc"
            # We split into key="submodule.external/hipcc.path" and
            # path_value="external/hipcc", then strip the trailing ".path"
            # to get section="submodule.external/hipcc" for the URL lookup.
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, path_value = parts
            section = key.rsplit(".", 1)[0]
            try:
                url = self.run_command_output(
                    [
                        "git", "config", "--file", str(gitmodules_path),
                        "--get", f"{section}.url",
                    ],
                    cwd=repo_dir,
                )
            except subprocess.CalledProcessError:
                self.log(f"No URL entry for {section}; skipping")
                continue
            url_map[path_value.strip()] = url

        return url_map

    def build_plan(self) -> Dict[str, Dict[str, str]]:
        """
        Build the branching execution plan.

        1. Clone (or reuse cached clone of) TheRock.
        2. Check out and hard-reset to ``self.commitid``.
        3. Populate submodules via ``fetch_sources.py`` (or ``git submodule update``).
        4. Read ``git submodule status`` and ``.gitmodules`` to collect each
           submodule's commit SHA, remote URL, and local path.
        5. Return a dict keyed by repo name, including TheRock itself.
        """
        cache_root = self.cache_dir or Path(tempfile.gettempdir()) / "rock-branching-cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        clone_dir = cache_root / "TheRock"
        self.cache_root = cache_root

        # Ensure cache directory contains a valid git repo; otherwise reclone
        needs_clone = not clone_dir.exists()
        if not needs_clone and not (clone_dir / ".git").exists():
            if not self.force_clone:
                raise RuntimeError(
                    f"Cache directory {clone_dir} exists but is not a git repo. "
                    "Use --force-clone to delete it and reclone."
                )
            self.log(f"Cache directory {clone_dir} is not a git repo; removing before reclone (--force-clone)")
            shutil.rmtree(clone_dir)
            needs_clone = True

        if needs_clone:
            self.log(f"Cloning TheRock repo from {self.rock_url} into {clone_dir}")
            self.run_command(
                ["git", "clone", str(self.rock_url), str(clone_dir)],
                cwd=cache_root,
                stream=True,
            )
        else:
            # Repo exists – ensure it's a valid git repo and has the right remote
            self.log(f"Reusing existing TheRock repo at {clone_dir}")

            # Optional: verify remote URL matches expected
            try:
                remote_url = self.run_command_output(
                    ["git", "remote", "get-url", "origin"],
                    cwd=clone_dir,
                )
                if "TheRock" not in remote_url:
                    raise RuntimeError(
                        f"Existing repo at {clone_dir} does not look like TheRock (origin={remote_url})"
                    )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"Failed to inspect existing repo at {clone_dir}: {exc}") from exc

            # Update refs from origin
            self.log("Fetching latest changes for existing TheRock clone...")
            self.run_command(["git", "fetch", "origin", "--prune", "--recurse-submodules=on-demand"], 
                             cwd=clone_dir, stream=True)

        fetch_script = clone_dir / "build_tools" / "fetch_sources.py"
        rock_commit = self.commitid

        # Hard-reset the working tree to the exact requested commit.
        self.log(f"Checking out TheRock at commit {rock_commit}")
        self.run_command(["git", "checkout", rock_commit], cwd=clone_dir)
        self.run_command(["git", "reset", "--hard", rock_commit], cwd=clone_dir)

        if fetch_script.exists():
            self.log("Updating submodules via fetch_sources.py (jobs=10, no patches)...")
            self.run_command(
                ["python3", str(fetch_script), "--jobs", "10", "--no-apply-patches"],
                cwd=clone_dir,
                stream=True,
            )
        else:
            self.log("fetch_sources.py not found; falling back to git submodule update")
            self.run_command(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=clone_dir,
                stream=True,
            )

        # Read submodule SHAs at this commit
        self.log("Reading submodule status...")
        try:
            status_output = self.run_command_output(
                ["git", "submodule", "status"],
                cwd=clone_dir,
            )
            lines = status_output.split("\n") if status_output else []
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to read submodule status: {exc}") from exc

        # Parse .gitmodules to map paths → URLs
        url_map = self.get_submodule_url_map(clone_dir)

        plan: Dict[str, Dict[str, str]] = {}

        # Each line from `git submodule status` looks like:
        #   " <sha> <path> (<describe>)"  or  "-<sha> <path>" (not initialized)
        # The leading character is ' ' (in-sync), '+' (changed), or '-' (not init).
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
                self.log(f"No URL found for submodule {path} in .gitmodules")
                continue

            # Skip repos in the exclude list
            if repo_name in self.exclude_list:
                self.log(f"Skipping {repo_name} (in exclude list)")
                continue

            # Skip repos not under the ROCm organization
            url_lower = repo_url.lower()
            if "github.com/rocm/" not in url_lower and "github.com:rocm/" not in url_lower:
                self.log(f"Skipping {repo_name} (not a ROCm org repo: {repo_url})")
                continue

            plan[repo_name] = {
                "url": repo_url,
                "commit": sha,
                "path": str(clone_dir / path),
            }

        # Add TheRock itself to the plan
        plan["TheRock"] = {
            "url": self.rock_url,
            "commit": rock_commit,
            "path": str(clone_dir),
        }

        return plan

    def main(self) -> None:
        plan = self.build_plan()
        self.log(f"Execution plan:\n{pformat(plan)}")
        self.execute_plan(plan)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Rock Branching Automation Tool"
        )
    parser.add_argument("-B", "--branch_name", required=True)
    parser.add_argument(
        "-C", "--commitid", required=True, help="Commit ID of TheRock"
        )
    parser.add_argument(
        "-A", "--apitoken", required=False, help="GitHub API token (optional)"
        )
    parser.add_argument(
        "--no-dry-run", action="store_false", dest="dry_run"
        )
    parser.add_argument(
        "--exclude-list", nargs="*", default=[],
        help="List of submodule repo names to exclude from branching"
        )
    parser.add_argument(
        "--force-clone", action="store_true", default=False,
        help="Delete and reclone if cache directory exists but is not a valid git repo"
        )
    parser.add_argument(
        "--cache-dir", default=None,
        help="Directory to cache the TheRock clone (default: /tmp/rock-branching-cache)"
        )
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    RockBranchingAutomation(args).main()
