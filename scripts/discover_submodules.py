#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Repo discovery and plan building for ROCm release scripts.

Read-only: assumes TheRock is already checked out at the desired ref (see
checkout_therock.py) and only inspects submodule status / .gitmodules.
"""

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat

from release_utils import ROCK_URL, log, run_command

@dataclass
class RepoInfo:
    url: str
    commit: str
    path: Path

def get_submodule_url_map(repo_dir: Path) -> dict[str, str]:
    gitmodules = repo_dir / ".gitmodules"
    if not gitmodules.exists():
        return {}
    try:
        path_entries = run_command(
            ["git", "config", "--file", str(gitmodules), "--get-regexp", r"submodule\..*\.path"],
            cwd=repo_dir,
            capture=True,
        )
    except subprocess.CalledProcessError:
        return {}

    url_map: dict[str, str] = {}
    for line in path_entries.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        section = parts[0].rsplit(".", 1)[0]
        path_value = parts[1].strip()
        try:
            url = run_command(
                ["git", "config", "--file", str(gitmodules), "--get", f"{section}.url"],
                cwd=repo_dir,
                capture=True,
            )
            url_map[path_value] = url
        except subprocess.CalledProcessError:
            log.info("No URL entry for %s; skipping", section)
    return url_map

def _collect_repos(
    clone_dir: Path, commit_ref: str, exclude: set[str], include: set[str] | None = None
) -> dict[str, RepoInfo]:
    """Parse submodule status and .gitmodules into a repo plan.

    If `include` is non-empty, only repos named in it are considered (bypassing
    the ROCm org filter and applying to TheRock as well); otherwise all ROCm
    org repos, plus TheRock, are considered. In both cases, any repo named in
    `exclude` is then removed from the result.
    """
    try:
        status_output = run_command(["git", "submodule", "status"], cwd=clone_dir, capture=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to read submodule status: {exc}") from exc

    url_map = get_submodule_url_map(clone_dir)
    plan: dict[str, RepoInfo] = {}

    for line in (status_output.splitlines() if status_output else []):
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, path = parts[0].lstrip("-+"), parts[1]
        repo_name = Path(path).name
        repo_url = url_map.get(path)
        if not repo_url:
            log.info("No URL for submodule %s; skipping", path)
            continue
        if include:
            if repo_name not in include:
                log.info("Skipping %s (not in include list)", repo_name)
                continue
        else:
            url_lower = repo_url.lower()
            if "github.com/rocm/" not in url_lower and "github.com:rocm/" not in url_lower:
                log.info("Skipping %s (not a ROCm org repo)", repo_name)
                continue
        if repo_name in exclude:
            log.info("Skipping %s (excluded)", repo_name)
            continue
        plan[repo_name] = RepoInfo(url=repo_url, commit=sha, path=clone_dir / path)

    if (not include or "TheRock" in include) and "TheRock" not in exclude:
        plan["TheRock"] = RepoInfo(url=ROCK_URL, commit=commit_ref, path=clone_dir)
    return plan

def build_plan(
    clone_dir: Path,
    commit_ref: str,
    exclude_list: set[str] | None = None,
    include_list: set[str] | None = None,
) -> dict[str, RepoInfo]:
    """Discover the repo plan from an already-checked-out TheRock clone.

    Read-only: does not clone, checkout, or modify clone_dir in any way.
    """
    exclude = exclude_list or set()
    include = include_list or set()
    plan = _collect_repos(clone_dir, commit_ref, exclude, include)
    log.info("Execution plan:\n%s", pformat(plan))
    return plan


def _format_plan(plan: dict[str, RepoInfo]) -> str:
    col_repo = max(len(name) for name in plan)
    lines = [
        f"Repo Plan ({len(plan)} repos)",
        "=" * (col_repo + 54),
        f"{'repo':<{col_repo}}   commit     url",
        "-" * (col_repo + 54),
    ]
    for name, info in plan.items():
        lines.append(f"{name:<{col_repo}}   {info.commit[:8]}   {info.url}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Build and print the ROCm release repo plan from an already-checked-out TheRock clone."
    )
    parser.add_argument("--clone-dir", required=True, type=Path,
                        help="Path to an existing TheRock clone (see checkout_therock.py)")
    parser.add_argument("-C", "--commit-ref", required=True,
                        help="Resolved TheRock commit (used as the reported TheRock commit in the plan)")
    parser.add_argument("--exclude-list", nargs="*", default=[],
                        help="Repo names to exclude from the plan, applied after --include-list")
    parser.add_argument("--include-list", nargs="*", default=[],
                        help="If given, only these repo names are considered for the plan "
                             "(bypasses the ROCm org filter); this also applies to TheRock, "
                             "so name it explicitly to keep it in the plan")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        plan = build_plan(
            clone_dir=args.clone_dir,
            commit_ref=args.commit_ref,
            exclude_list=set(args.exclude_list),
            include_list=set(args.include_list),
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    print(_format_plan(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
