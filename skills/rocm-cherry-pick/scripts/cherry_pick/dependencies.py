# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Git-trailer parsing and deterministic dependency DAG validation."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

from .config import SUPPORTED_REPOSITORIES


PR_URL_RE = re.compile(
    r"https://github\.com/(ROCm)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)\Z"
)
COMMIT_URL_RE = re.compile(
    r"https://github\.com/(ROCm)/([A-Za-z0-9_.-]+)/commit/([0-9a-f]{40})\Z"
)


class DependencyError(ValueError):
    """Report dependency validation or execution failures."""

    def __init__(self, reason_code: str, message: str) -> None:
        """Initialize a dependency-graph failure with stable operator-facing detail."""

        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class DependencyRef:
    """Represent dependency ref in the dependencies contract."""

    kind: str
    url: str
    repository: str
    number: int | None = None
    commit_sha: str | None = None


@dataclass(frozen=True)
class DependencyGraph:
    """Represent dependency graph in the dependencies contract."""

    root: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    topological_order: tuple[str, ...]


def parse_dependency_url(value: str) -> DependencyRef:
    """Return a typed canonical ROCm pull-request or full-commit dependency."""

    match = PR_URL_RE.fullmatch(value)
    kind = "pull_request"
    if match is None:
        match = COMMIT_URL_RE.fullmatch(value)
        kind = "commit"
    if match is None:
        raise DependencyError(
            "invalid_dependency_url",
            "dependency must be a canonical ROCm pull request or full commit "
            f"URL: {value!r}",
        )
    repository = f"{match.group(1)}/{match.group(2)}"
    if repository not in SUPPORTED_REPOSITORIES:
        raise DependencyError(
            "invalid_dependency_url",
            "dependency must be a canonical ROCm pull request or full commit "
            f"URL: {value!r}",
        )
    if kind == "pull_request":
        number = int(match.group(3))
        return DependencyRef(
            kind=kind,
            url=f"https://github.com/{repository}/pull/{number}",
            repository=repository,
            number=number,
        )
    commit_sha = match.group(3)
    return DependencyRef(
        kind=kind,
        url=f"https://github.com/{repository}/commit/{commit_sha}",
        repository=repository,
        commit_sha=commit_sha,
    )


def parse_dependency_trailers(body: str) -> tuple[DependencyRef, ...]:
    """Parse unique Depends-On trailers with Git and validate every canonical URL."""

    if not body.strip():
        return ()
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=body,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        stdin=None,
    )
    if result.returncode != 0:
        raise DependencyError(
            "dependency_trailer_parse_failed",
            result.stderr.strip() or "Git could not parse dependency trailers",
        )
    refs: list[DependencyRef] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key.strip().casefold() != "depends-on":
            continue
        ref = parse_dependency_url(value.strip())
        if ref.url not in seen:
            refs.append(ref)
            seen.add(ref.url)
    return tuple(refs)


def _validate_limit(value: int, name: str) -> None:
    """Reject non-positive or non-integer dependency graph limits."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def build_dependency_graph(
    root: str,
    adjacency: Mapping[str, Sequence[str]],
    *,
    max_nodes: int,
    max_depth: int,
) -> DependencyGraph:
    """Return deterministic topology after enforcing evidence, cycle, and size invariants."""

    _validate_limit(max_nodes, "max_nodes")
    _validate_limit(max_depth, "max_depth")
    root_ref = parse_dependency_url(root)
    if root_ref.kind != "pull_request":
        raise DependencyError(
            "invalid_dependency_root", "dependency graph root must be a pull request"
        )

    reachable: set[str] = set()
    edges: set[tuple[str, str]] = set()
    visiting: set[str] = set()
    visited: set[str] = set()
    topological: list[str] = []

    def visit(node: str, depth: int) -> None:
        """Visit one dependency node while enforcing limits and cycle checks."""

        if node in visiting:
            raise DependencyError(
                "dependency_cycle", f"dependency graph contains a cycle at {node}"
            )
        if node in visited:
            return
        if node not in adjacency:
            raise DependencyError(
                "dependency_evidence_missing",
                f"dependency adjacency is missing for {node}",
            )
        visiting.add(node)
        raw_targets = adjacency[node]
        if isinstance(raw_targets, (str, bytes)):
            raise DependencyError(
                "dependency_evidence_missing",
                f"dependency edges for {node} are invalid",
            )
        node_ref = parse_dependency_url(node)
        targets: set[str] = set()
        for target in raw_targets:
            parse_dependency_url(target)
            targets.add(target)
        if node_ref.kind == "commit" and targets:
            raise DependencyError(
                "commit_prerequisite_not_leaf",
                f"commit prerequisite must be a leaf: {node}",
            )
        for target in sorted(targets):
            next_depth = depth + 1
            if next_depth > max_depth:
                raise DependencyError(
                    "dependency_depth_limit",
                    f"dependency graph exceeds maximum depth {max_depth}",
                )
            edges.add((node, target))
            if target != root:
                reachable.add(target)
                if len(reachable) > max_nodes:
                    raise DependencyError(
                        "dependency_node_limit",
                        f"dependency graph exceeds maximum node count {max_nodes}",
                    )
            visit(target, next_depth)
        visiting.remove(node)
        visited.add(node)
        if node != root:
            topological.append(node)

    visit(root, 0)
    return DependencyGraph(
        root=root,
        nodes=tuple(sorted(reachable)),
        edges=tuple(sorted(edges)),
        topological_order=tuple(topological),
    )
