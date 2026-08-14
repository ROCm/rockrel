"""Prove when an existing target pull request covers a source change."""

from __future__ import annotations

import configparser
import re
import subprocess
from pathlib import Path
from typing import Any

from .clients import GitHubClient
from .git import evaluate_cherry_pick
from .models import Status


CHERRY_PICK_ORIGIN_RE = re.compile(
    r"^\(cherry picked from commit ([0-9a-f]{40})\)$", re.MULTILINE
)
GITHUB_REPO_RE = re.compile(
    r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?\Z"
)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def extract_cherry_pick_origin(message: str) -> str | None:
    """Extract Git's full original-commit trailer from a commit message."""

    match = CHERRY_PICK_ORIGIN_RE.search(message)
    return match.group(1) if match else None


def _ensure_commit(repo: Path, sha: str) -> bool:
    if _run(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0:
        return True
    fetch = _run(repo, "fetch", "--no-tags", "origin", sha)
    return fetch.returncode == 0 and _run(
        repo, "cat-file", "-e", f"{sha}^{{commit}}"
    ).returncode == 0


def _gitlink_changes(repo: Path, source: str) -> list[dict[str, str]]:
    parents = _run(repo, "rev-list", "--parents", "-n", "1", source)
    fields = parents.stdout.split()
    if parents.returncode != 0 or len(fields) < 2:
        return []
    raw = _run(
        repo,
        "diff-tree",
        "--raw",
        "--no-commit-id",
        "-r",
        fields[1],
        source,
    )
    changes = []
    for line in raw.stdout.splitlines():
        metadata, separator, path = line.partition("\t")
        parts = metadata.split()
        if not separator or len(parts) < 5:
            continue
        old_mode = parts[0].removeprefix(":")
        new_mode = parts[1]
        if old_mode == "160000" and new_mode == "160000":
            changes.append(
                {
                    "path": path,
                    "old": parts[2],
                    "desired": parts[3],
                }
            )
    return changes


def _changed_paths(repo: Path, source: str) -> set[str]:
    result = _run(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        f"{source}^",
        source,
    )
    return {line for line in result.stdout.splitlines() if line}


def _gitlink_pin(repo: Path, revision: str, path: str) -> str | None:
    result = _run(repo, "ls-tree", revision, "--", path)
    fields = result.stdout.split()
    if result.returncode != 0 or len(fields) < 3 or fields[0] != "160000":
        return None
    return fields[2]


def _submodule_repositories(repo: Path, revision: str) -> dict[str, tuple[str, str]]:
    result = _run(repo, "show", f"{revision}:.gitmodules")
    if result.returncode != 0:
        return {}
    parser = configparser.ConfigParser()
    parser.read_string(result.stdout)
    repositories: dict[str, tuple[str, str]] = {}
    for section in parser.sections():
        if not parser.has_option(section, "path") or not parser.has_option(
            section, "url"
        ):
            continue
        match = GITHUB_REPO_RE.fullmatch(parser.get(section, "url"))
        if match:
            repositories[parser.get(section, "path")] = (
                match.group(1),
                match.group(2),
            )
    return repositories


def _commit_message(commit: dict[str, Any]) -> str:
    value = commit.get("commit", {}).get("message")
    return value if isinstance(value, str) else ""


def find_covering_pull(
    repo: str | Path,
    github: GitHubClient,
    source_sha: str,
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    """Return positive coverage evidence for an open target PR, otherwise None."""

    if str(candidate.get("state", "")).lower() != "open":
        return None
    candidate_sha = candidate.get("head", {}).get("sha")
    candidate_url = candidate.get("html_url")
    if not isinstance(candidate_sha, str) or not isinstance(candidate_url, str):
        return None
    repo_path = Path(repo)
    if not _ensure_commit(repo_path, source_sha) or not _ensure_commit(
        repo_path, candidate_sha
    ):
        return None

    gitlinks = _gitlink_changes(repo_path, source_sha)
    gitlink_paths = {item["path"] for item in gitlinks}
    if gitlinks and _changed_paths(repo_path, source_sha) == gitlink_paths:
        submodules = _submodule_repositories(repo_path, source_sha)
        evidence = []
        for change in gitlinks:
            path = change["path"]
            desired = change["desired"]
            candidate_pin = _gitlink_pin(repo_path, candidate_sha, path)
            component = submodules.get(path)
            if candidate_pin is None or component is None:
                return None
            if desired == candidate_pin:
                evidence.append(
                    {"path": path, "desired": desired, "candidate": candidate_pin}
                )
                continue
            owner, component_repo = component
            comparison = github.compare(
                owner, component_repo, desired, candidate_pin
            )
            if comparison.get("status") in {"ahead", "identical"}:
                evidence.append(
                    {"path": path, "desired": desired, "candidate": candidate_pin}
                )
                continue
            desired_commit = github.commit(owner, component_repo, desired)
            desired_origin = extract_cherry_pick_origin(
                _commit_message(desired_commit)
            )
            candidate_origins = {
                origin
                for item in comparison.get("commits", [])
                if isinstance(item, dict)
                for origin in [extract_cherry_pick_origin(_commit_message(item))]
                if origin is not None
            }
            if desired_origin is None or desired_origin not in candidate_origins:
                return None
            evidence.append(
                {
                    "path": path,
                    "desired": desired,
                    "candidate": candidate_pin,
                    "common_origin": desired_origin,
                }
            )
        return {
            "reason": "gitlink_cherry_pick_provenance",
            "paths": sorted(gitlink_paths),
            "pull_request_url": candidate_url,
            "gitlinks": evidence,
        }

    ordinary = evaluate_cherry_pick(repo_path, source_sha, candidate_sha)
    if ordinary.status is Status.ALREADY_CONTAINED:
        return {
            "reason": ordinary.reason_code,
            "paths": sorted(_changed_paths(repo_path, source_sha)),
            "pull_request_url": candidate_url,
            "git": ordinary.evidence,
        }
    return None
