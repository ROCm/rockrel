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

def fetch_repo_map(
    token: str, commit_ref: str, exclude_list: set[str], include_list: set[str] | None = None
) -> dict[str, str]:
    """Return repo-name → URL map by reading .gitmodules from the GitHub API.

    No local clone required. Accepts any valid git ref (branch, tag, or SHA).

    If `include_list` is non-empty, only repos named in it are considered
    (matched by short name or full submodule path, bypassing the ROCm org
    filter), and it is an error if any entry matches nothing; otherwise all
    ROCm org repos are considered. In both cases, TheRock is included unless
    excluded, and any repo named in `exclude_list` is removed from the result.
    """
    include = include_list or set()
    api_url = f"{GITHUB_API}/repos/ROCm/TheRock/contents/.gitmodules?ref={commit_ref}"
    try:
        data = _api_request(api_url, token)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"ERROR: Failed to fetch .gitmodules (HTTP {exc.code}). "
            f"Check that ref {commit_ref!r} exists and the token has repo read access."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: Network error fetching .gitmodules: {exc.reason}")

    raw = base64.b64decode(data["content"]).decode()
    repo_map: dict[str, str] = {}
    matched_include: set[str] = set()
    current_path: str | None = None
    current_url: str | None = None

    def _flush(path: str | None, url: str | None) -> None:
        if not path or not url:
            return
        repo_name = Path(path).name
        if include:
            matches = include & {repo_name, path}
            if not matches:
                return
            matched_include.update(matches)
        else:
            url_lower = url.lower()
            is_rocm = "github.com/rocm/" in url_lower or "github.com:rocm/" in url_lower
            if not is_rocm:
                return
        if repo_name not in exclude_list:
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

    if (not include or "TheRock" in include) and "TheRock" not in exclude_list:
        repo_map["TheRock"] = ROCK_URL
        if include:
            matched_include.add("TheRock")

    if include:
        unmatched = include - matched_include
        if unmatched:
            raise SystemExit(
                f"ERROR: --include-list entries matched no submodule path or name: {sorted(unmatched)}"
            )
    return repo_map

def run_command(
    args: list,
    cwd: Path | None = None,
    *,
    timeout: int | None = TIMEOUT_SHORT_SECONDS,
    capture: bool = False,
) -> str:
    """Run a command, raising CalledProcessError on failure.

    cwd=None lets the command inherit the caller's working directory, for
    commands (e.g. `git ls-remote`) that don't operate on a local repo and
    so have no real working tree to run in.

    Returns captured stdout as a stripped string when capture=True, else "".
    """
    cmd = [str(a) for a in args]
    log.info("++ Exec [%s]$ %s", cwd, shlex.join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else None,
        text=capture,
        check=True,
        timeout=timeout,
    )
    return result.stdout.strip() if capture else ""

def resolve_git_ref(ref: str, repo_dir: Path) -> str:
    """Resolve any git ref (branch, tag, SHA) to a full 40-char commit SHA."""
    return run_command(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=repo_dir,
        capture=True,
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
