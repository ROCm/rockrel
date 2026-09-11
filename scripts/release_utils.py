#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Shared constants and subprocess utilities for ROCm release scripts."""

import logging
import shlex
import subprocess
import sys
from pathlib import Path

ROCK_URL = "https://github.com/ROCm/TheRock.git"
TIMEOUT_LONG = 1800
TIMEOUT_SHORT = 60

log = logging.getLogger("rock_release")

def run_command(
    args: list,
    cwd: Path,
    *,
    stream: bool = False,
    timeout: int | None = TIMEOUT_SHORT,
) -> None:
    """Run a command, streaming output line-by-line when stream=True."""
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
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, check=True, timeout=timeout,
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
    """Run a command and return its stdout as a stripped string."""
    cmd = [str(a) for a in args]
    log.info("++ Exec [%s]$ %s", cwd, shlex.join(cmd))
    result = subprocess.run(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True, check=True, timeout=timeout,
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
