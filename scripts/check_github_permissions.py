#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Check GitHub push/admin permissions for all ROCm repos in a release plan.

Works for both branching and tagging — push access is the minimum required
for either operation. Reads the repo list from .gitmodules via the GitHub API
(no local clone needed). Accepts any valid git ref (branch, tag, or SHA).

Can be run standalone to verify that the current machine (developer or CI)
has the necessary permissions before performing release activities.

Usage:
    python check_github_permissions.py \\
        --commitid <rock-git-ref> \\
        [--action branches|tags] \\
        [--exclude-list repo1 repo2]
"""
import argparse
import logging
import sys
import urllib.error

from release_utils import (
    GITHUB_API,
    _api_request,
    extract_owner_repo,
    fetch_repo_map,
    get_gh_token,
)

def check_permissions(token: str, repo_map: dict[str, str]) -> int:
    """Check push/admin access for every repo in repo_map.

    Returns 0 if all pass, 1 if any fail.
    """
    print("=" * 60)
    print(f"  GitHub Permission Check")
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
              "Aborting — insufficient permissions to proceed.")
        for name, reason in failed.items():
            print(f"  {name}: {reason}")
        return 1

    print("\nAll permission checks passed.")
    return 0

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check GitHub push/admin permissions for all ROCm repos in a release plan. "
            "Run this before creating branches or tags to verify the current machine "
            "has the necessary access."
        )
    )
    parser.add_argument(
        "-C", "--commitid",
        required=True,
        help="TheRock git ref (branch, tag, or SHA) to read .gitmodules from",
    )
    parser.add_argument(
        "--exclude-list",
        nargs="*",
        default=[],
        help="Repo names to skip",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = get_gh_token()
    repo_map = fetch_repo_map(token, args.commitid, set(args.exclude_list))
    return check_permissions(token, repo_map)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
