#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Repo discovery and plan building for ROCm release scripts."""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat

from release_utils import ROCK_URL, TIMEOUT_LONG, log, run_command, run_command_output

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
        path_entries = run_command_output(
            ["git", "config", "--file", str(gitmodules), "--get-regexp", r"submodule\..*\.path"],
            cwd=repo_dir,
        )
    except Exception:
        return {}

    url_map: dict[str, str] = {}
    for line in path_entries.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        section = parts[0].rsplit(".", 1)[0]
        path_value = parts[1].strip()
        try:
            url = run_command_output(
                ["git", "config", "--file", str(gitmodules), "--get", f"{section}.url"],
                cwd=repo_dir,
            )
            url_map[path_value] = url
        except Exception:
            log.info("No URL entry for %s; skipping", section)
    return url_map

def _ensure_clone(clone_dir: Path, cache_root: Path, force_clone: bool) -> None:
    """Clone TheRock if needed, or fetch latest changes."""
    needs_clone = not clone_dir.exists()
    if not needs_clone and not (clone_dir / ".git").exists():
        if not force_clone:
            raise RuntimeError(
                f"Cache directory {clone_dir} exists but is not a git repo. "
                "Use --force-clone to delete it and reclone."
            )
        log.info("Removing invalid cache dir before reclone")
        shutil.rmtree(clone_dir)
        needs_clone = True

    if needs_clone:
        log.info("Cloning TheRock into %s", clone_dir)
        run_command(["git", "clone", ROCK_URL, str(clone_dir)], cwd=cache_root, stream=True, timeout=TIMEOUT_LONG)
    else:
        log.info("Reusing existing TheRock clone at %s", clone_dir)
        try:
            remote_url = run_command_output(["git", "remote", "get-url", "origin"], cwd=clone_dir)
            if "TheRock" not in remote_url:
                raise RuntimeError(f"Repo at {clone_dir} does not look like TheRock (origin={remote_url})")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to inspect repo at {clone_dir}: {exc}") from exc
        log.info("Fetching latest changes...")
        run_command(
            ["git", "fetch", "origin", "--prune", "--recurse-submodules=on-demand"],
            cwd=clone_dir, stream=True, timeout=TIMEOUT_LONG,
        )

def _update_submodules(clone_dir: Path) -> None:
    """Populate submodules via fetch_sources.py or git submodule update."""
    fetch_script = clone_dir / "build_tools" / "fetch_sources.py"
    if fetch_script.exists():
        log.info("Updating submodules via fetch_sources.py...")
        run_command(
            ["python3", str(fetch_script), "--jobs", "10", "--no-apply-patches"],
            cwd=clone_dir, stream=True, timeout=TIMEOUT_LONG,
        )
    else:
        log.info("fetch_sources.py not found; falling back to git submodule update")
        run_command(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=clone_dir, stream=True, timeout=TIMEOUT_LONG,
        )

def _collect_repos(clone_dir: Path, commitid: str, exclude: set[str]) -> dict[str, RepoInfo]:
    """Parse submodule status and .gitmodules into a repo plan."""
    try:
        status_output = run_command_output(["git", "submodule", "status"], cwd=clone_dir)
    except Exception as exc:
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
        if repo_name in exclude:
            log.info("Skipping %s (excluded)", repo_name)
            continue
        url_lower = repo_url.lower()
        if "github.com/rocm/" not in url_lower and "github.com:rocm/" not in url_lower:
            log.info("Skipping %s (not a ROCm org repo)", repo_name)
            continue
        plan[repo_name] = RepoInfo(url=repo_url, commit=sha, path=clone_dir / path)

    plan["TheRock"] = RepoInfo(url=ROCK_URL, commit=commitid, path=clone_dir)
    return plan

def build_plan(
    commitid: str,
    cache_dir: Path | None = None,
    force_clone: bool = False,
    exclude_list: set[str] | None = None,
) -> dict[str, RepoInfo]:
    """Clone/reuse TheRock at commitid, populate submodules, return repo plan."""
    exclude = exclude_list or set()
    cache_root = cache_dir or Path(tempfile.gettempdir()) / "rock-branching-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    clone_dir = cache_root / "TheRock"

    _ensure_clone(clone_dir, cache_root, force_clone)

    log.info("Checking out TheRock at %s", commitid)
    run_command(["git", "checkout", commitid], cwd=clone_dir)
    run_command(["git", "reset", "--hard", commitid], cwd=clone_dir)

    _update_submodules(clone_dir)

    plan = _collect_repos(clone_dir, commitid, exclude)
    log.info("Execution plan:\n%s", pformat(plan))
    return plan
