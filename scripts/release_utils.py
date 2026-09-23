# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Shared utilities for ROCm release scripts."""

import base64
import json
import logging
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROCK_URL = "https://github.com/ROCm/TheRock.git"
GITHUB_API = "https://api.github.com"
TIMEOUT_LONG_SECONDS = 1800
TIMEOUT_SHORT_SECONDS = 60

log = logging.getLogger("rock_release")

def get_gh_token() -> str:
    """Return the GitHub token from the active gh CLI session."""
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
        raise SystemExit("ERROR: Not authenticated with gh CLI. Run: gh auth login")
    if not token:
        raise SystemExit("ERROR: gh auth token returned an empty token. Run: gh auth login")
    return token

def extract_owner_repo(url: str) -> tuple[str, str]:
    """Return (owner, repo) from a GitHub HTTPS or SSH URL."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"Cannot extract owner/repo from URL: {url!r}")

def _api_request(url: str, token: str) -> dict:
    """Make an authenticated GitHub API GET request and return parsed JSON."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def fetch_repo_map(token: str, commitid: str, exclude_list: set[str]) -> dict[str, str]:
    """Return repo-name → URL map by reading .gitmodules from the GitHub API.

    No local clone required. Filters to ROCm org repos only.
    Accepts any valid git ref (branch, tag, or SHA).
    """
    api_url = f"{GITHUB_API}/repos/ROCm/TheRock/contents/.gitmodules?ref={commitid}"
    try:
        data = _api_request(api_url, token)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"ERROR: Failed to fetch .gitmodules (HTTP {exc.code}). "
            f"Check that ref {commitid!r} exists and the token has repo read access."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: Network error fetching .gitmodules: {exc.reason}")

    raw = base64.b64decode(data["content"]).decode()
    repo_map: dict[str, str] = {}
    current_path: str | None = None
    current_url: str | None = None

    def _flush(path: str | None, url: str | None) -> None:
        if not path or not url:
            return
        repo_name = Path(path).name
        url_lower = url.lower()
        is_rocm = "github.com/rocm/" in url_lower or "github.com:rocm/" in url_lower
        if is_rocm and repo_name not in exclude_list:
            repo_map[repo_name] = url

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("[submodule"):
            _flush(current_path, current_url)
            current_path = current_url = None
        elif "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key == "path":
                current_path = value
            elif key == "url":
                current_url = value
    _flush(current_path, current_url)

    if "TheRock" not in exclude_list:
        repo_map["TheRock"] = ROCK_URL
    return repo_map

def run_command(
    args: list,
    cwd: Path,
    *,
    timeout: int | None = TIMEOUT_SHORT_SECONDS,
) -> None:
    """Run a command, raising CalledProcessError on failure."""
    cmd = [str(a) for a in args]
    log.info("++ Exec [%s]$ %s", cwd, shlex.join(cmd))
    subprocess.run(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        check=True,
        timeout=timeout,
    )

def run_command_output(args: list, cwd: Path, timeout: int | None = TIMEOUT_SHORT_SECONDS) -> str:
    """Run a command and return its stdout as a stripped string."""
    cmd = [str(a) for a in args]
    log.info("++ Exec [%s]$ %s", cwd, shlex.join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout.strip()

def resolve_git_ref(ref: str, repo_dir: Path) -> str:
    """Resolve any git ref (branch, tag, SHA) to a full 40-char commit SHA."""
    return run_command_output(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=repo_dir,
    )

def convert_to_ssh(url: str) -> str:
    if url.startswith("https://github.com/"):
        return "git@github.com:" + url.replace("https://github.com/", "")
    return url

def setup_remote(url: str, repo_dir: Path) -> None:
    ssh_url = convert_to_ssh(url)
    try:
        run_command(["git", "remote", "set-url", "rocm-github", ssh_url], cwd=repo_dir)
    except subprocess.CalledProcessError:
        run_command(["git", "remote", "add", "rocm-github", ssh_url], cwd=repo_dir)
