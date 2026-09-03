#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Shared utilities for ROCm release branching and tagging scripts."""

import logging
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat

ROCK_URL = "https://github.com/ROCm/TheRock.git"
TIMEOUT_LONG = 1800
TIMEOUT_SHORT = 60

log = logging.getLogger("rock_release")


@dataclass
class RepoInfo:
    url: str
    commit: str
    path: Path


def run_command(
    args: list,
    cwd: Path,
    *,
    stream: bool = False,
    timeout: int | None = TIMEOUT_SHORT,
) -> None:
    cmd = [str(a) for a in args]
    log.info("++ Exec [%s]$ %s", cwd, shlex.join(cmd))
    sys.stdout.flush()

    if stream:
        process = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        try:
            for line in process.stdout:
                log.info(line.rstrip())
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=timeout,
        )
        if result.stdout:
            log.info(result.stdout.decode(errors="ignore"))
        if result.stderr:
            log.info(result.stderr.decode(errors="ignore"))
    except subprocess.CalledProcessError as exc:
        log.info((exc.stdout or b"").decode(errors="ignore"))
        log.info((exc.stderr or b"").decode(errors="ignore"))
        raise


def run_command_output(args: list, cwd: Path, timeout: int | None = TIMEOUT_SHORT) -> str:
    cmd = [str(a) for a in args]
    log.info("++ Exec [%s]$ %s", cwd, shlex.join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout.strip()


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


def get_submodule_url_map(repo_dir: Path) -> dict[str, str]:
    gitmodules = repo_dir / ".gitmodules"
    if not gitmodules.exists():
        return {}
    try:
        path_entries = run_command_output(
            ["git", "config", "--file", str(gitmodules), "--get-regexp", r"submodule\..*\.path"],
            cwd=repo_dir,
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
            url = run_command_output(
                ["git", "config", "--file", str(gitmodules), "--get", f"{section}.url"],
                cwd=repo_dir,
            )
            url_map[path_value] = url
        except subprocess.CalledProcessError:
            log.info("No URL entry for %s; skipping", section)
    return url_map


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
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to inspect repo at {clone_dir}: {exc}") from exc
        log.info("Fetching latest changes...")
        run_command(
            ["git", "fetch", "origin", "--prune", "--recurse-submodules=on-demand"],
            cwd=clone_dir, stream=True, timeout=TIMEOUT_LONG,
        )

    log.info("Checking out TheRock at %s", commitid)
    run_command(["git", "checkout", commitid], cwd=clone_dir)
    run_command(["git", "reset", "--hard", commitid], cwd=clone_dir)

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

    try:
        status_output = run_command_output(["git", "submodule", "status"], cwd=clone_dir)
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
        if repo_name in exclude:
            log.info("Skipping %s (excluded)", repo_name)
            continue
        url_lower = repo_url.lower()
        if "github.com/rocm/" not in url_lower and "github.com:rocm/" not in url_lower:
            log.info("Skipping %s (not a ROCm org repo)", repo_name)
            continue
        plan[repo_name] = RepoInfo(url=repo_url, commit=sha, path=clone_dir / path)

    plan["TheRock"] = RepoInfo(url=ROCK_URL, commit=commitid, path=clone_dir)
    log.info("Execution plan:\n%s", pformat(plan))
    return plan
