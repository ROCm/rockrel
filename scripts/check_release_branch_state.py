#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Read-only preflight check for release branching.

Run this BEFORE create_release_branches.py to verify that the current
machine is ready to create a new release branch. Mutates nothing —
no local clone needed.

Checks (in order):
  1. SSH authentication to github.com succeeds
  2. GitHub token has push/admin access to all ROCm repos in the plan
  3. For each repo, verifies the remote is reachable over SSH and
     reports whether the release branch already exists:
       [OK]   ready to branch (branch does not exist yet)
       [SKIP] branch already exists on this repo (will be skipped by
              create_release_branches.py)
       [FAIL] remote unreachable or SSH error

Usage:
    python check_release_branch_state.py \\
        --branch-name <release-branch> \\
        --commitid <rock-git-ref> \\
        [--exclude-list repo1 repo2]

Arguments:
    --branch-name   The new release branch name to check.
                    This branch should NOT exist yet — [OK] means it is safe to create.
    --commitid      TheRock git ref (branch, tag, or SHA) to read the repo list from.
    --exclude-list  Repo names to skip.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from release_utils import convert_to_ssh, fetch_repo_map, get_gh_token, run_command_output, TIMEOUT_SHORT_SECONDS
from check_github_permissions import check_permissions

def check_ssh_auth() -> bool:
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "StrictHostKeyChecking=no", "git@github.com"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
        output = result.stdout.decode(errors="ignore")
        if "successfully authenticated" in output:
            print("[OK]  SSH authentication to github.com succeeded")
            return True
        print(f"[FAIL] SSH authentication failed: {output.strip()}")
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[FAIL] SSH check error: {exc}")
        return False

def check_repo(repo_name: str, url: str, branch_name: str) -> dict:
    ssh_url = convert_to_ssh(url)
    result = {"repo": repo_name, "reachable": False, "branch_exists": False, "error": None}
    try:
        output = run_command_output(
            ["git", "ls-remote", "--heads", ssh_url, branch_name],
            cwd=Path.cwd(),
            timeout=TIMEOUT_SHORT_SECONDS,
        )
        result["reachable"] = True
        result["branch_exists"] = bool(output)
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out reaching remote"
    except subprocess.CalledProcessError as exc:
        result["error"] = f"Remote unreachable: {exc}"
    return result

def run_checks(branch_name: str, commitid: str, exclude_list: list[str]) -> int:
    print(f"Checking release branch state for: {branch_name} @ {commitid}\n")

    if not check_ssh_auth():
        print("\nAborting: SSH auth failed. Configure SSH key for git@github.com.")
        return 1

    print()
    token = get_gh_token()
    repo_map = fetch_repo_map(token, commitid, set(exclude_list))
    if check_permissions(token, repo_map) != 0:
        return 1

    print()
    issues = []
    already_exist = []

    for repo_name, url in repo_map.items():
        result = check_repo(repo_name, url, branch_name)
        if result["error"]:
            issues.append(f"  {repo_name}: {result['error']}")
            print(f"[FAIL] {repo_name}: {result['error']}")
        elif result["branch_exists"]:
            already_exist.append(repo_name)
            print(f"[SKIP] {repo_name}: branch '{branch_name}' already exists")
        else:
            print(f"[OK]  {repo_name}: ready to branch")

    print()
    if already_exist:
        print(f"Branches already exist ({len(already_exist)}): {', '.join(already_exist)}")
    if issues:
        print(f"\n{len(issues)} issue(s) found:")
        for issue in issues:
            print(issue)
        return 1

    print("All checks passed.")
    return 0

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check release branch state (read-only)")
    parser.add_argument("-B", "--branch-name", required=True, help="Release branch name")
    parser.add_argument("-C", "--commitid", required=True, help="TheRock git ref (branch, tag, or SHA)")
    parser.add_argument("--exclude-list", nargs="*", default=[], help="Repo names to skip")
    args = parser.parse_args(argv)

    return run_checks(args.branch_name, args.commitid, args.exclude_list)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
