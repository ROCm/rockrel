#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Clone/reuse TheRock, check it out at a ref, and populate its submodules."""

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from release_utils import (
    ROCK_URL,
    TIMEOUT_LONG_SECONDS,
    log,
    resolve_git_ref,
    run_command,
)

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
        run_command(["git", "clone", ROCK_URL, str(clone_dir)], cwd=cache_root, timeout=TIMEOUT_LONG_SECONDS)
    else:
        log.info("Reusing existing TheRock clone at %s", clone_dir)
        try:
            remote_url = run_command(["git", "remote", "get-url", "origin"], cwd=clone_dir, capture=True)
            if "TheRock" not in remote_url:
                raise RuntimeError(f"Repo at {clone_dir} does not look like TheRock (origin={remote_url})")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to inspect repo at {clone_dir}: {exc}") from exc
        log.info("Fetching latest changes...")
        run_command(
            ["git", "fetch", "origin", "--prune", "--recurse-submodules=on-demand"],
            cwd=clone_dir, timeout=TIMEOUT_LONG_SECONDS,
        )

def _ensure_clean_worktree(repo_dir: Path) -> None:
    """Raise if repo_dir has local modifications that 'git reset --hard' would destroy."""
    status = run_command(["git", "status", "--porcelain"], cwd=repo_dir, capture=True)
    if status:
        raise RuntimeError(
            f"Repo at {repo_dir} has uncommitted changes; refusing to run "
            "'git reset --hard'. Commit or stash your changes, use a different "
            "--cache-dir, or pass --force-clone to start from a fresh clone."
        )

def update_submodules(clone_dir: Path) -> None:
    """Populate submodules via fetch_sources.py."""
    fetch_script = clone_dir / "build_tools" / "fetch_sources.py"
    if not fetch_script.exists():
        raise RuntimeError(f"fetch_sources.py not found at {fetch_script}")
    log.info("Updating submodules via fetch_sources.py...")
    run_command(
        [sys.executable, str(fetch_script), "--jobs", "10", "--no-apply-patches"],
        cwd=clone_dir, timeout=TIMEOUT_LONG_SECONDS,
    )

def default_clone_dir(cache_dir: Path | None = None) -> Path:
    """Return the TheRock clone path for a given cache dir (without creating it)."""
    cache_root = (cache_dir or Path(tempfile.gettempdir()) / "rock-branching-cache").resolve()
    return cache_root / "TheRock"

def checkout_therock(
    commit_ref: str,
    cache_dir: Path | None = None,
    force_clone: bool = False,
) -> tuple[Path, str]:
    """Clone/reuse TheRock, check out commit_ref, and populate submodules.

    Mutates the local cache: clones or fetches, checks out the resolved
    commit, and runs fetch_sources.py. Returns (clone_dir, canonical_sha).
    """
    clone_dir = default_clone_dir(cache_dir)
    cache_root = clone_dir.parent
    cache_root.mkdir(parents=True, exist_ok=True)

    _ensure_clone(clone_dir, cache_root, force_clone)
    _ensure_clean_worktree(clone_dir)

    canonical_sha = resolve_git_ref(commit_ref, clone_dir)
    log.info("Checking out TheRock at %s", canonical_sha)
    run_command(["git", "checkout", canonical_sha], cwd=clone_dir)

    update_submodules(clone_dir)
    return clone_dir, canonical_sha

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Clone/checkout TheRock and populate submodules.")
    parser.add_argument("-C", "--commit-ref", required=True, help="TheRock git ref (branch, tag, or SHA)")
    parser.add_argument("--cache-dir", default=None,
                        help="Directory to cache the TheRock clone (default: system temp dir)")
    parser.add_argument("--force-clone", action="store_true", default=False,
                        help="Delete and reclone if the cache dir exists but is not a valid git repo")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    try:
        clone_dir, canonical_sha = checkout_therock(
            commit_ref=args.commit_ref,
            cache_dir=cache_dir,
            force_clone=args.force_clone,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    print(f"clone_dir={clone_dir}")
    print(f"commit={canonical_sha}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
