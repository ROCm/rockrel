#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Check GitHub push/admin permissions for all ROCm repos in a release plan.

Works for both branching and tagging — push access is the minimum required
for either operation. Reads the repo list from .gitmodules via the GitHub API
(no local clone needed).

Can be run standalone or imported by other scripts.

Usage:
    python check_github_permissions.py \\
        --commitid <rock-commit-sha> \\
        [--action branches|tags] \\
        [--exclude-list repo1 repo2]
"""
import argparse
import base64
import json
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROCK_URL = "https://github.com/ROCm/TheRock.git"
GITHUB_API = "https://api.github.com"

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
    """
    api_url = f"{GITHUB_API}/repos/ROCm/TheRock/contents/.gitmodules?ref={commitid}"
    try:
        data = _api_request(api_url, token)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"ERROR: Failed to fetch .gitmodules (HTTP {exc.code}). "
            f"Check that commit {commitid!r} exists and the token has repo read access."
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


def check_permissions(token: str, repo_map: dict[str, str], action: str = "branches") -> int:
    """Check push/admin access for every repo in repo_map.

    action: "branches" or "tags" — used only in the abort message.
    Returns 0 if all pass, 1 if any fail.
    """
    print("=" * 60)
    print(f"  GitHub Permission Check ({action})")
    print(f"  Verifying push/admin access for {len(repo_map)} repo(s)")
    print("=" * 60)

    failed: dict[str, str] = {}

    for repo_name, url in repo_map.items():
        try:
            owner, repo = extract_owner_repo(url)
        except ValueError as exc:
            failed[repo_name] = f"URL parse error: {exc}"
            continue

        api_url = f"{GITHUB_API}/repos/{owner}/{repo}"
        try:
            data = _api_request(api_url, token)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                failed[repo_name] = f"HTTP 403 Forbidden — token lacks access to {owner}/{repo}"
            elif exc.code == 404:
                failed[repo_name] = f"HTTP 404 — {owner}/{repo} not found (token may lack visibility)"
            else:
                failed[repo_name] = f"HTTP {exc.code} from GitHub API for {owner}/{repo}"
            continue
        except urllib.error.URLError as exc:
            failed[repo_name] = f"Network error checking {owner}/{repo}: {exc.reason}"
            continue

        perms = data.get("permissions", {})
        if perms.get("push", False) or perms.get("admin", False):
            print(f"[OK]  {repo_name}: push/admin access confirmed")
        else:
            failed[repo_name] = (
                f"Insufficient permissions for {owner}/{repo}: "
                f"push={perms.get('push')}, admin={perms.get('admin')}"
            )

    total = len(repo_map)
    passed = total - len(failed)
    print("=" * 60)
    print(f"  Passed : {passed} / {total} repo(s)")
    print(f"  Failed : {len(failed)} / {total} repo(s)")
    print("=" * 60)

    if failed:
        print(f"\nERROR: Permission check failed for {len(failed)} repo(s). "
              f"Aborting before any {action} are created.")
        for name, reason in failed.items():
            print(f"  {name}: {reason}")
        return 1

    print(f"\nAll permission checks passed for {action}.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check GitHub push/admin permissions for a release plan")
    parser.add_argument("-C", "--commitid", required=True, help="TheRock commit SHA")
    parser.add_argument(
        "--action",
        choices=["branches", "tags"],
        default="branches",
        help="Operation being checked (used in output messages)",
    )
    parser.add_argument("--exclude-list", nargs="*", default=[], help="Repo names to skip")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[0-9a-f]{40}", args.commitid):
        print(f"ERROR: --commitid must be a full 40-char lowercase SHA-1, got: {args.commitid!r}")
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = get_gh_token()
    repo_map = fetch_repo_map(token, args.commitid, set(args.exclude_list))
    return check_permissions(token, repo_map, action=args.action)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
