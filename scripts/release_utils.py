# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Shared constants and subprocess utilities for ROCm release scripts."""

import logging
import shlex
import subprocess
from pathlib import Path

ROCK_URL = "https://github.com/ROCm/TheRock.git"
TIMEOUT_LONG_SECONDS = 1800
TIMEOUT_SHORT_SECONDS = 60

log = logging.getLogger("rock_release")

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
