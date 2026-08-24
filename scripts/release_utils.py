#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Shared utilities for ROCm release automation scripts.

Provides:
  - RepoInfo           dataclass describing a single repo in an execution plan
  - RockBase           base class with subprocess helpers, git helpers, and
                       build_plan() for cloning/reading TheRock submodules
  - extract_owner_repo parse (owner, repo) from a GitHub HTTPS or SSH URL
  - get_gh_token       retrieve the active gh CLI token
  - fetch_lightweight_plan  read .gitmodules from the GitHub API (no clone)
  - check_permissions  verify push/admin access for every repo in a plan
"""
import base64
import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat


ROCK_URL = "https://github.com/ROCm/TheRock.git"

TIMEOUT_LONG = 1800   # clone, fetch, submodule update
TIMEOUT_SHORT = 60    # tag, push, git config reads


@dataclass
class RepoInfo:
    """A single repository entry in an execution plan."""

    url: str
    commit: str
    path: Path


# ---------------------------------------------------------------------------
# Standalone GitHub helpers (no class state required)
# ---------------------------------------------------------------------------

def extract_owner_repo(url: str) -> tuple[str, str]:
    """Return (owner, repo) from a GitHub HTTPS or SSH URL.

    Accepts:
      https://github.com/ROCm/hip.git  →  ("ROCm", "hip")
      git@github.com:ROCm/hip.git      →  ("ROCm", "hip")

    The .git suffix is optional. Raises ValueError for non-matching URLs.
    """
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"Cannot extract owner/repo from URL: {url!r}")


def get_gh_token() -> str:
    """Return the GitHub token from the active gh CLI session.

    Raises SystemExit with a clear message if gh is missing or not logged in.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
    except FileNotFoundError:
        raise SystemExit(
            "ERROR: gh CLI not found. Install it from https://cli.github.com "
            "and run: gh auth login"
        )
    except subprocess.CalledProcessError:
        raise SystemExit(
            "ERROR: Not authenticated with gh CLI. Run: gh auth login"
        )
    if not token:
        raise SystemExit(
            "ERROR: gh auth token returned an empty token. Run: gh auth login"
        )
    return token


def fetch_lightweight_plan(
    token: str,
    commitid: str,
    exclude_list: set[str],
) -> dict[str, str]:
    """Return a repo-name → URL map by reading .gitmodules from the GitHub API.

    Fetches GET /repos/ROCm/TheRock/contents/.gitmodules?ref=<commitid> so the
    caller can build a repo list without cloning anything locally. Filters out
    repos outside the ROCm org and repos in exclude_list. TheRock itself is
    included unless it appears in exclude_list.
    """
    api_url = (
        f"https://api.github.com/repos/ROCm/TheRock/contents/.gitmodules"
        f"?ref={commitid}"
    )
    req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"ERROR: Failed to fetch .gitmodules from GitHub API "
            f"(HTTP {exc.code}). Check that commit {commitid!r} exists "
            f"and the token has repo read access."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"ERROR: Network error fetching .gitmodules: {exc.reason}"
        )

    raw = base64.b64decode(data["content"]).decode()

    repo_map: dict[str, str] = {}
    current_path: str | None = None
    current_url: str | None = None

    def _flush(path: str | None, url: str | None) -> None:
        if not path or not url:
            return
        repo_name = Path(path).name
        url_lower = url.lower()
        is_rocm = (
            "github.com/rocm/" in url_lower or "github.com:rocm/" in url_lower
        )
        if is_rocm and repo_name not in exclude_list:
            repo_map[repo_name] = url

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("[submodule"):
            _flush(current_path, current_url)
            current_path = None
            current_url = None
        elif "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key == "path":
                current_path = value
            elif key == "url":
                current_url = value

    _flush(current_path, current_url)

    if "TheRock" not in exclude_list:
        repo_map["TheRock"] = ROCK_URL
    return repo_map


def check_permissions(
    token: str,
    repo_map: dict[str, str],
    logger: logging.Logger,
    action: str = "branches",
) -> None:
    """Verify push/admin access for every repo in repo_map.

    Checks GET /repos/{owner}/{repo} for each entry. Collects all failures
    before raising so the caller sees the complete list in one run.
    Raises SystemExit if any repo lacks the required access.

    Args:
        token:    GitHub personal access token.
        repo_map: Mapping of repo name → GitHub URL.
        logger:   Logger to use for progress output.
        action:   Short noun used in the abort message ("branches" or "tags").
    """
    logger.info("=" * 60)
    logger.info("  GitHub Permission Check")
    logger.info("  Verifying push/admin access for %d repo(s)", len(repo_map))
    logger.info("=" * 60)

    failed: dict[str, str] = {}
    for repo_name, url in repo_map.items():
        try:
            owner, repo = extract_owner_repo(url)
        except ValueError as exc:
            failed[repo_name] = f"URL parse error: {exc}"
            continue

        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        req = urllib.request.Request(
            api_url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                failed[repo_name] = (
                    f"HTTP 403 Forbidden — token lacks access to "
                    f"{owner}/{repo}"
                )
            elif exc.code == 404:
                failed[repo_name] = (
                    f"HTTP 404 — repo {owner}/{repo} not found "
                    "(token may lack visibility)"
                )
            else:
                failed[repo_name] = (
                    f"HTTP {exc.code} from GitHub API for {owner}/{repo}"
                )
            continue
        except urllib.error.URLError as exc:
            failed[repo_name] = (
                f"Network error checking {owner}/{repo}: {exc.reason}"
            )
            continue

        perms = data.get("permissions", {})
        if perms.get("push", False) or perms.get("admin", False):
            logger.info("Permission check OK: %s/%s", owner, repo)
        else:
            failed[repo_name] = (
                f"Insufficient permissions for {owner}/{repo}: "
                f"push={perms.get('push')}, admin={perms.get('admin')}"
            )

    total = len(repo_map)
    passed = total - len(failed)
    logger.info("=" * 60)
    logger.info("  Permission Check Summary")
    logger.info("  Passed : %d / %d repo(s)", passed, total)
    logger.info("  Failed : %d / %d repo(s)", len(failed), total)
    logger.info("=" * 60)

    if failed:
        lines = [f"  {name}: {reason}" for name, reason in failed.items()]
        raise SystemExit(
            f"ERROR: Permission check failed for {len(failed)} repo(s). "
            f"Aborting before any {action} are created.\n" + "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class RockBase:
    """Shared subprocess helpers, git helpers, and plan builder.

    Subclasses set ``_cache_dir_name`` (used as the default cache directory
    suffix under /tmp) and call ``super().__init__()`` after setting their
    own attributes.
    """

    _cache_dir_name: str = "rock-cache"

    def __init__(self, cli_args) -> None:
        self.release_branch: str = cli_args.branch_name
        self.dry_run: bool = cli_args.dry_run
        self.commitid: str = cli_args.commitid
        self.exclude_list: set[str] = set(cli_args.exclude_list or [])
        self.force_clone: bool = cli_args.force_clone
        self.cache_dir: Path | None = (
            Path(cli_args.cache_dir) if cli_args.cache_dir else None
        )
        self.rock_url: str = ROCK_URL
        self.cache_root: Path | None = None
        self._logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, msg: str) -> None:
        self._logger.info(msg)

    # ------------------------------------------------------------------
    # Subprocess helpers
    # ------------------------------------------------------------------

    def run_command(
        self,
        args: list[str | Path],
        cwd: Path,
        *,
        input_data: bytes | None = None,
        stream: bool = False,
        timeout: int | None = None,
    ) -> None:
        """Execute a command, raising CalledProcessError on failure."""
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
            try:
                for line in process.stdout:
                    self.log(line.rstrip())
                ret = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
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
        self,
        args: list[str | Path],
        cwd: Path,
        timeout: int | None = None,
    ) -> str:
        """Run a command and return its stripped stdout."""
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

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def convert_to_ssh(self, url: str) -> str:
        """Convert https://github.com/X/Y.git to git@github.com:X/Y.git."""
        if url.startswith("https://github.com/"):
            return "git@github.com:" + url.replace("https://github.com/", "")
        return url

    def _setup_remote(self, url: str, repo_dir: Path) -> None:
        """Add or update the rocm-github remote."""
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

    def get_submodule_url_map(self, repo_dir: Path) -> dict[str, str]:
        """Return mapping of submodule working-tree paths to remote URLs."""
        gitmodules_path = repo_dir / ".gitmodules"
        if not gitmodules_path.exists():
            return {}

        try:
            path_entries = self.run_command_output(
                [
                    "git", "config",
                    "--file", str(gitmodules_path),
                    "--get-regexp", r"submodule\..*\.path",
                ],
                cwd=repo_dir,
            )
        except subprocess.CalledProcessError:
            return {}

        url_map: dict[str, str] = {}
        for line in path_entries.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, path_value = parts
            section = key.rsplit(".", 1)[0]
            try:
                url = self.run_command_output(
                    [
                        "git", "config",
                        "--file", str(gitmodules_path),
                        "--get", f"{section}.url",
                    ],
                    cwd=repo_dir,
                )
            except subprocess.CalledProcessError:
                self.log(f"No URL entry for {section}; skipping")
                continue
            url_map[path_value.strip()] = url

        return url_map

    # ------------------------------------------------------------------
    # Plan builder
    # ------------------------------------------------------------------

    def _prepare_clone(self, cache_root: Path) -> Path:
        """Ensure a valid TheRock clone exists under cache_root; return its path."""
        clone_dir = cache_root / "TheRock"
        needs_clone = not clone_dir.exists()

        if not needs_clone and not (clone_dir / ".git").exists():
            if not self.force_clone:
                raise RuntimeError(
                    f"Cache directory {clone_dir} exists but is not a git repo. "
                    "Use --force-clone to delete it and reclone."
                )
            self.log(
                f"Cache directory {clone_dir} is not a git repo; "
                "removing before reclone (--force-clone)"
            )
            shutil.rmtree(clone_dir)
            needs_clone = True

        if needs_clone:
            self.log(f"Cloning TheRock from {self.rock_url} into {clone_dir}")
            self.run_command(
                ["git", "clone", str(self.rock_url), str(clone_dir)],
                cwd=cache_root,
                stream=True,
                timeout=TIMEOUT_LONG,
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
                ["git", "fetch", "origin", "--prune", "--recurse-submodules=on-demand"],
                cwd=clone_dir,
                stream=True,
                timeout=TIMEOUT_LONG,
            )

        return clone_dir

    def _checkout_and_update_submodules(self, clone_dir: Path) -> None:
        """Hard-reset to self.commitid and populate submodules."""
        rock_commit = self.commitid
        self.log(f"Checking out TheRock at commit {rock_commit}")
        self.run_command(["git", "checkout", rock_commit], cwd=clone_dir)
        self.run_command(["git", "reset", "--hard", rock_commit], cwd=clone_dir)

        fetch_script = clone_dir / "build_tools" / "fetch_sources.py"
        if fetch_script.exists():
            self.log("Updating submodules via fetch_sources.py (jobs=10, no patches)...")
            self.run_command(
                ["python3", str(fetch_script), "--jobs", "10", "--no-apply-patches"],
                cwd=clone_dir,
                stream=True,
                timeout=TIMEOUT_LONG,
            )
        else:
            self.log("fetch_sources.py not found; falling back to git submodule update")
            self.run_command(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=clone_dir,
                stream=True,
                timeout=TIMEOUT_LONG,
            )

    def build_plan(self) -> dict[str, RepoInfo]:
        """Clone/reuse TheRock, populate submodules, and return the execution plan.

        Subclasses may call super().build_plan() and extend the result, or
        override _prepare_clone / _checkout_and_update_submodules for extra
        git steps (e.g. fetching a release branch before checkout).
        """
        cache_root = (
            self.cache_dir
            or Path(tempfile.gettempdir()) / self._cache_dir_name
        )
        cache_root.mkdir(parents=True, exist_ok=True)
        self.cache_root = cache_root

        clone_dir = self._prepare_clone(cache_root)
        self._checkout_and_update_submodules(clone_dir)

        self.log("Reading submodule status...")
        try:
            status_output = self.run_command_output(
                ["git", "submodule", "status"],
                cwd=clone_dir,
            )
            lines = status_output.split("\n") if status_output else []
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to read submodule status: {exc}") from exc

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
                self.log(f"No URL found for submodule {path} in .gitmodules")
                continue
            if repo_name in self.exclude_list:
                self.log(f"Skipping {repo_name} (in exclude list)")
                continue
            url_lower = repo_url.lower()
            if (
                "github.com/rocm/" not in url_lower
                and "github.com:rocm/" not in url_lower
            ):
                self.log(f"Skipping {repo_name} (not a ROCm org repo: {repo_url})")
                continue

            plan[repo_name] = RepoInfo(url=repo_url, commit=sha, path=clone_dir / path)

        if "TheRock" not in self.exclude_list:
            plan["TheRock"] = RepoInfo(
                url=self.rock_url,
                commit=self.commitid,
                path=clone_dir,
            )
        else:
            self.log("Skipping TheRock (in exclude list)")
        return plan
