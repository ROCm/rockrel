#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Read-only preflight check for release branching.

Verifies SSH auth, remote reachability, and whether the release branch
already exists for each repo in the plan. Mutates nothing.

Usage:
    python check_release_branch_state.py \\
        --branch-name <release-branch> \\
        --commitid <rock-commit-sha>
"""
import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

from release_utils import build_plan, convert_to_ssh, run_command_output, TIMEOUT_SHORT
from check_github_permissions import get_gh_token, fetch_repo_map, check_permissions


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

def check_repo(repo_name: str, url: str, repo_path: Path, branch_name: str) -> dict:
    ssh_url = convert_to_ssh(url)
    result = {"repo": repo_name, "reachable": False, "branch_exists": False, "error": None}

    try:
        run_command_output(
            ["git", "remote", "get-url", "rocm-github"],
            cwd=repo_path,
        )
    except subprocess.CalledProcessError:
        # Remote not set yet — check reachability via ls-remote directly
        pass

    try:
        output = run_command_output(
            ["git", "ls-remote", "--heads", ssh_url, branch_name],
            cwd=repo_path,
            timeout=TIMEOUT_SHORT,
        )
        result["reachable"] = True
        result["branch_exists"] = bool(output)
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out reaching remote"
    except subprocess.CalledProcessError as exc:
        result["error"] = f"Remote unreachable: {exc}"

    return result

def run_checks(branch_name: str, commitid: str, cache_dir: Path | None, force_clone: bool, exclude_list: list[str]) -> int:
    print(f"Checking release branch state for: {branch_name} @ {commitid}\n")

    if not check_ssh_auth():
        print("\nAborting: SSH auth failed. Configure SSH key for git@github.com.")
        return 1

    print()
    token = get_gh_token()
    repo_map = fetch_repo_map(token, commitid, set(exclude_list))
    if check_permissions(token, repo_map, action="branches") != 0:
        return 1

    print()
    try:
        plan = build_plan(
            commitid=commitid,
            cache_dir=cache_dir,
            force_clone=force_clone,
            exclude_list=set(exclude_list),
        )
    except RuntimeError as exc:
        print(f"[FAIL] Could not build repo plan: {exc}")
        return 1

    issues = []
    already_exist = []

    for repo_name, info in plan.items():
        result = check_repo(repo_name, info.url, info.path, branch_name)
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
    parser.add_argument("-C", "--commitid", required=True, help="TheRock commit SHA")
    parser.add_argument("--exclude-list", nargs="*", default=[], help="Submodule repo names to skip")
    parser.add_argument("--force-clone", action="store_true", default=False)
    parser.add_argument("--cache-dir", default=None)
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[0-9a-f]{40}", args.commitid):
        print(f"ERROR: --commitid must be a full 40-char lowercase SHA-1, got: {args.commitid!r}")
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    return run_checks(args.branch_name, args.commitid, cache_dir, args.force_clone, args.exclude_list)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
