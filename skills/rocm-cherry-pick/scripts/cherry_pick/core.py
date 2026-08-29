# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Deterministic, network-free Git cherry-pick planning core."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .config import SUPPORTED_REPOSITORIES, valid_branch_name
from .dependencies import DependencyError, build_dependency_graph, parse_dependency_url
from .git import (
    ChangesetError,
    CommitIdentity,
    GitEvidenceError,
    SourceIdentity,
    evaluate_changeset,
    evaluate_existing_pull_coverage,
    prove_changeset,
    prove_commit_changeset,
)
from .models import Result, Status


SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
ASSURANCE = {
    "scope": "git_only",
    "ci_checks": "not_evaluated",
    "semantic_readiness": "human_review_required",
}


class ManifestError(ValueError):
    """An immutable core request is malformed or internally inconsistent."""


def _object(value: object, context: str) -> dict[str, object]:
    """Require and return a JSON object at the named manifest boundary."""
    if not isinstance(value, dict):
        raise ManifestError(f"{context} must be an object")
    return value


def _exact_fields(value: dict[str, object], fields: set[str], context: str) -> None:
    """Require the exact supported field set at one manifest boundary."""
    unsupported = set(value) - fields
    if unsupported:
        raise ManifestError(
            f"{context} contains unsupported field {sorted(unsupported)[0]}"
        )
    missing = fields - set(value)
    if missing:
        raise ManifestError(f"{context} omitted {sorted(missing)[0]}")


def _string(value: dict[str, object], key: str, context: str) -> str:
    """Read one required non-empty string from a manifest object."""
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ManifestError(f"{context}.{key} must be a non-empty string")
    return item


def _sha(value: dict[str, object], key: str, context: str) -> str:
    """Read one canonical full lowercase Git SHA from a manifest object."""
    item = _string(value, key, context)
    if SHA_RE.fullmatch(item) is None:
        raise ManifestError(f"{context}.{key} must be a full lowercase SHA")
    return item


@dataclass(frozen=True)
class DestinationNode:
    """Immutable identity of one reviewed destination branch head."""

    repository: str
    branch: str
    head_sha: str

    @classmethod
    def from_dict(cls, value: object, context: str) -> "DestinationNode":
        """Parse one exact destination identity from manifest data."""
        raw = _object(value, context)
        _exact_fields(raw, {"repository", "branch", "head_sha"}, context)
        repository = _string(raw, "repository", context)
        if repository not in SUPPORTED_REPOSITORIES:
            raise ManifestError(f"{context}.repository is unsupported")
        branch = _string(raw, "branch", context)
        if not valid_branch_name(branch):
            raise ManifestError(f"{context}.branch is invalid")
        return cls(repository, branch, _sha(raw, "head_sha", context))

    def as_dict(self) -> dict[str, object]:
        """Serialize this destination identity into canonical manifest data."""
        return {
            "repository": self.repository,
            "branch": self.branch,
            "head_sha": self.head_sha,
        }


@dataclass(frozen=True)
class PullRequestNode:
    """Immutable source pull request and destination application identity."""

    kind: str
    url: str
    repository: str
    number: int
    base_branch: str
    head_sha: str
    merge_sha: str
    ordered_commits: tuple[str, ...]
    body_sha256: str
    destination: DestinationNode

    @classmethod
    def from_dict(cls, value: object, context: str) -> "PullRequestNode":
        """Parse and validate one pull request node from manifest data."""
        raw = _object(value, context)
        fields = {
            "kind",
            "url",
            "repository",
            "number",
            "base_branch",
            "head_sha",
            "merge_sha",
            "ordered_commits",
            "body_sha256",
            "destination",
        }
        _exact_fields(raw, fields, context)
        if raw.get("kind") != "pull_request":
            raise ManifestError(f"{context}.kind must be pull_request")
        url = _string(raw, "url", context)
        try:
            ref = parse_dependency_url(url)
        except DependencyError as exc:
            raise ManifestError(f"{context}.url is invalid") from exc
        if ref.kind != "pull_request":
            raise ManifestError(f"{context}.url must identify a pull request")
        repository = _string(raw, "repository", context)
        number = raw.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ManifestError(f"{context}.number must be positive")
        if repository != ref.repository or number != ref.number:
            raise ManifestError(
                f"{context}.url identity does not match repository/number"
            )
        base_branch = _string(raw, "base_branch", context)
        if not valid_branch_name(base_branch):
            raise ManifestError(f"{context}.base_branch is invalid")
        commits = raw.get("ordered_commits")
        if not isinstance(commits, list) or not commits:
            raise ManifestError(f"{context}.ordered_commits must be a non-empty array")
        if any(
            not isinstance(item, str) or SHA_RE.fullmatch(item) is None
            for item in commits
        ):
            raise ManifestError(
                f"{context}.ordered_commits must contain full lowercase SHAs"
            )
        body_digest = _string(raw, "body_sha256", context)
        if DIGEST_RE.fullmatch(body_digest) is None:
            raise ManifestError(f"{context}.body_sha256 must be a SHA-256 digest")
        destination = DestinationNode.from_dict(
            raw.get("destination"), f"{context}.destination"
        )
        if destination.repository != repository:
            raise ManifestError(
                f"{context} destination repository must match source repository"
            )
        return cls(
            kind="pull_request",
            url=url,
            repository=repository,
            number=number,
            base_branch=base_branch,
            head_sha=_sha(raw, "head_sha", context),
            merge_sha=_sha(raw, "merge_sha", context),
            ordered_commits=tuple(commits),
            body_sha256=body_digest,
            destination=destination,
        )

    def as_dict(self) -> dict[str, object]:
        """Serialize this pull request node into canonical manifest data."""
        return {
            "kind": self.kind,
            "url": self.url,
            "repository": self.repository,
            "number": self.number,
            "base_branch": self.base_branch,
            "head_sha": self.head_sha,
            "merge_sha": self.merge_sha,
            "ordered_commits": list(self.ordered_commits),
            "body_sha256": self.body_sha256,
            "destination": self.destination.as_dict(),
        }


@dataclass(frozen=True)
class CommitNode:
    """Immutable standalone commit prerequisite and destination identity."""

    kind: str
    url: str
    repository: str
    commit_sha: str
    destination: DestinationNode

    @classmethod
    def from_dict(cls, value: object, context: str) -> "CommitNode":
        """Parse and validate one standalone commit node from manifest data."""
        raw = _object(value, context)
        _exact_fields(
            raw,
            {"kind", "url", "repository", "commit_sha", "destination"},
            context,
        )
        if raw.get("kind") != "commit":
            raise ManifestError(f"{context}.kind must be commit")
        url = _string(raw, "url", context)
        try:
            ref = parse_dependency_url(url)
        except DependencyError as exc:
            raise ManifestError(f"{context}.url is invalid") from exc
        repository = _string(raw, "repository", context)
        commit_sha = _sha(raw, "commit_sha", context)
        if (
            ref.kind != "commit"
            or ref.repository != repository
            or ref.commit_sha != commit_sha
        ):
            raise ManifestError(f"{context}.url identity does not match commit")
        destination = DestinationNode.from_dict(
            raw.get("destination"), f"{context}.destination"
        )
        if destination.repository != repository:
            raise ManifestError(
                f"{context} destination repository must match source repository"
            )
        return cls("commit", url, repository, commit_sha, destination)

    def as_dict(self) -> dict[str, object]:
        """Serialize this standalone commit node into canonical manifest data."""
        return {
            "kind": self.kind,
            "url": self.url,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "destination": self.destination.as_dict(),
        }


PrerequisiteNode = PullRequestNode | CommitNode
# Transitional import compatibility for code that names the PR node directly.
ChangeNode = PullRequestNode


def _prerequisite_node(value: object, context: str) -> PrerequisiteNode:
    """Parse one closed-union pull request or commit prerequisite."""
    raw = _object(value, context)
    kind = raw.get("kind")
    if kind == "pull_request":
        return PullRequestNode.from_dict(raw, context)
    if kind == "commit":
        return CommitNode.from_dict(raw, context)
    raise ManifestError(f"{context}.kind must be pull_request or commit")


@dataclass(frozen=True)
class PrerequisiteEdge:
    """Immutable directed dependency edge between canonical source URLs."""

    source: str
    target: str

    @classmethod
    def from_dict(cls, value: object, context: str) -> "PrerequisiteEdge":
        """Parse one directed prerequisite edge from manifest data."""
        raw = _object(value, context)
        _exact_fields(raw, {"from", "to"}, context)
        return cls(_string(raw, "from", context), _string(raw, "to", context))

    def as_dict(self) -> dict[str, str]:
        """Serialize this prerequisite edge into canonical manifest data."""
        return {"from": self.source, "to": self.target}


DependencyEdge = PrerequisiteEdge


@dataclass(frozen=True)
class CoverageCandidate:
    """Immutable open pull request candidate for exact coverage proof."""

    url: str
    repository: str
    number: int
    state: str
    draft: bool
    base_branch: str
    base_sha: str
    head_repository: str
    head_sha: str

    @classmethod
    def from_dict(cls, value: object, context: str) -> "CoverageCandidate":
        """Parse and validate one open pull request coverage candidate."""
        raw = _object(value, context)
        fields = {
            "url",
            "repository",
            "number",
            "state",
            "draft",
            "base_branch",
            "base_sha",
            "head_repository",
            "head_sha",
        }
        _exact_fields(raw, fields, context)
        url = _string(raw, "url", context)
        try:
            ref = parse_dependency_url(url)
        except DependencyError as exc:
            raise ManifestError(f"{context}.url is invalid") from exc
        repository = _string(raw, "repository", context)
        number = raw.get("number")
        if (
            ref.kind != "pull_request"
            or repository != ref.repository
            or isinstance(number, bool)
            or not isinstance(number, int)
            or number != ref.number
        ):
            raise ManifestError(
                f"{context}.url identity does not match repository/number"
            )
        if raw.get("state") != "open":
            raise ManifestError(f"{context}.state must be open")
        draft = raw.get("draft")
        if not isinstance(draft, bool):
            raise ManifestError(f"{context}.draft must be a boolean")
        base_branch = _string(raw, "base_branch", context)
        if not valid_branch_name(base_branch):
            raise ManifestError(f"{context}.base_branch is invalid")
        head_repository = _string(raw, "head_repository", context)
        if head_repository != repository:
            raise ManifestError(
                f"{context}.head_repository identity must match repository"
            )
        return cls(
            url=url,
            repository=repository,
            number=number,
            state="open",
            draft=draft,
            base_branch=base_branch,
            base_sha=_sha(raw, "base_sha", context),
            head_repository=head_repository,
            head_sha=_sha(raw, "head_sha", context),
        )

    def as_dict(self) -> dict[str, object]:
        """Serialize this coverage candidate into canonical manifest data."""
        return {
            "url": self.url,
            "repository": self.repository,
            "number": self.number,
            "state": self.state,
            "draft": self.draft,
            "base_branch": self.base_branch,
            "base_sha": self.base_sha,
            "head_repository": self.head_repository,
            "head_sha": self.head_sha,
        }


def _manifest_arrays(
    raw: dict[str, object],
) -> tuple[list[object], list[object], list[object]]:
    """Return the three manifest arrays after preserving validation precedence."""

    prerequisites = raw.get("prerequisites")
    edges = raw.get("prerequisite_edges")
    candidates = raw.get("coverage_candidates")
    if not isinstance(prerequisites, list):
        raise ManifestError("manifest.prerequisites must be an array")
    if not isinstance(edges, list):
        raise ManifestError("manifest.prerequisite_edges must be an array")
    if not isinstance(candidates, list):
        raise ManifestError("manifest.coverage_candidates must be an array")
    return prerequisites, edges, candidates


def _validate_unique_prerequisites(
    prerequisites: tuple[PrerequisiteNode, ...],
) -> None:
    """Reject duplicate dependency identities before parsing later structures."""

    if len({node.url for node in prerequisites}) != len(prerequisites):
        raise ManifestError("manifest contains a duplicate dependency node")


def _normalize_prerequisite_graph(
    source: PullRequestNode,
    prerequisites: tuple[PrerequisiteNode, ...],
    edges: tuple[PrerequisiteEdge, ...],
) -> tuple[tuple[PrerequisiteNode, ...], tuple[PrerequisiteEdge, ...]]:
    """Validate reachability and return canonical node and edge ordering.

    Invariants:
        Every declared prerequisite is reachable from the source, and every
        edge endpoint names the source or one declared prerequisite.
    """

    urls = [node.url for node in prerequisites]
    if len(set(edges)) != len(edges):
        raise ManifestError("manifest contains a duplicate dependency edge")
    known = {source.url, *urls}
    for edge in edges:
        if edge.source not in known or edge.target not in known:
            raise ManifestError("dependency edge references an unknown dependency node")
    adjacency: dict[str, list[str]] = {url: [] for url in known}
    for edge in edges:
        adjacency[edge.source].append(edge.target)
    try:
        graph = build_dependency_graph(
            source.url,
            adjacency,
            max_nodes=max(len(prerequisites), 1),
            max_depth=max(len(prerequisites) + 1, 1),
        )
    except DependencyError as exc:
        raise ManifestError(f"dependency graph {exc.reason_code}: {exc}") from exc
    if set(graph.nodes) != set(urls):
        raise ManifestError("manifest contains an unreachable dependency node")
    by_url = {node.url: node for node in prerequisites}
    ordered_nodes = tuple(by_url[url] for url in graph.topological_order)
    ordered_edges = tuple(sorted(edges, key=lambda item: (item.source, item.target)))
    return ordered_nodes, ordered_edges


def _normalize_coverage_candidates(
    source: PullRequestNode,
    prerequisites: tuple[PrerequisiteNode, ...],
    candidates: tuple[CoverageCandidate, ...],
) -> tuple[CoverageCandidate, ...]:
    """Bind open-PR candidates to exact request destinations and sort them."""

    candidate_urls = [candidate.url for candidate in candidates]
    if len(set(candidate_urls)) != len(candidate_urls):
        raise ManifestError("manifest contains a duplicate coverage candidate")
    destinations = {
        (node.repository, node.destination.branch): node.destination.head_sha
        for node in (source, *prerequisites)
        if isinstance(node, PullRequestNode)
    }
    for candidate in candidates:
        destination_head = destinations.get(
            (candidate.repository, candidate.base_branch)
        )
        if destination_head is None:
            raise ManifestError(
                "coverage candidate identity must match a request pull destination"
            )
        if candidate.base_sha != destination_head:
            raise ManifestError(
                "coverage candidate base_sha must match its exact destination"
            )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.repository,
                item.base_branch,
                item.number,
                item.url,
            ),
        )
    )


@dataclass(frozen=True)
class CoreRequest:
    """Canonical network-free request consumed by the Git planning core."""

    schema_version: int
    train_id: str
    dependency_mode: str
    source: PullRequestNode
    prerequisites: tuple[PrerequisiteNode, ...]
    prerequisite_edges: tuple[PrerequisiteEdge, ...]
    coverage_candidates: tuple[CoverageCandidate, ...]

    @classmethod
    def from_dict(cls, value: object) -> "CoreRequest":
        """Parse and canonicalize one immutable version-3 core request.

        Raises:
            ManifestError: If any field, graph edge, or coverage binding is
                malformed or internally inconsistent.
        """

        raw = _object(value, "manifest")
        if raw.get("schema_version") != 3:
            raise ManifestError("manifest.schema_version must be 3")
        _exact_fields(
            raw,
            {
                "schema_version",
                "train_id",
                "dependency_mode",
                "source",
                "prerequisites",
                "prerequisite_edges",
                "coverage_candidates",
            },
            "manifest",
        )
        train_id = _string(raw, "train_id", "manifest")
        dependency_mode = _string(raw, "dependency_mode", "manifest")
        if dependency_mode not in {"gate", "managed_stack"}:
            raise ManifestError(
                "manifest.dependency_mode must be gate or managed_stack"
            )
        source = PullRequestNode.from_dict(raw.get("source"), "manifest.source")
        prerequisites_raw, edges_raw, candidates_raw = _manifest_arrays(raw)
        prerequisites = tuple(
            _prerequisite_node(item, f"manifest.prerequisites[{index}]")
            for index, item in enumerate(prerequisites_raw)
        )
        _validate_unique_prerequisites(prerequisites)
        edges = tuple(
            PrerequisiteEdge.from_dict(item, f"manifest.prerequisite_edges[{index}]")
            for index, item in enumerate(edges_raw)
        )
        ordered, ordered_edges = _normalize_prerequisite_graph(
            source, prerequisites, edges
        )
        candidates = tuple(
            CoverageCandidate.from_dict(item, f"manifest.coverage_candidates[{index}]")
            for index, item in enumerate(candidates_raw)
        )
        candidates = _normalize_coverage_candidates(source, ordered, candidates)
        return cls(
            schema_version=3,
            train_id=train_id,
            dependency_mode=dependency_mode,
            source=source,
            prerequisites=ordered,
            prerequisite_edges=ordered_edges,
            coverage_candidates=candidates,
        )

    @property
    def dependencies(self) -> tuple[PrerequisiteNode, ...]:
        """Compatibility view; new callers should use ``prerequisites``."""

        return self.prerequisites

    @property
    def dependency_edges(self) -> tuple[PrerequisiteEdge, ...]:
        """Return the compatibility view of canonical prerequisite edges."""
        return self.prerequisite_edges

    def as_dict(self) -> dict[str, object]:
        """Serialize the complete request into canonical version-3 manifest data."""
        return {
            "schema_version": self.schema_version,
            "train_id": self.train_id,
            "dependency_mode": self.dependency_mode,
            "source": self.source.as_dict(),
            "prerequisites": [item.as_dict() for item in self.prerequisites],
            "prerequisite_edges": [item.as_dict() for item in self.prerequisite_edges],
            "coverage_candidates": [
                item.as_dict() for item in self.coverage_candidates
            ],
        }

    def fingerprint(self) -> str:
        """Hash the canonical request representation for immutable plan binding."""
        payload = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def coverage_candidates_for(
        self, node: PullRequestNode
    ) -> tuple[CoverageCandidate, ...]:
        """Return candidates bound to the exact destination of one pull node."""
        return tuple(
            item
            for item in self.coverage_candidates
            if item.repository == node.repository
            and item.base_branch == node.destination.branch
            and item.base_sha == node.destination.head_sha
        )

    def coverage_snapshot_sha256_for(self, node: PullRequestNode) -> str:
        """Hash candidates bound to the exact destination of one pull node."""
        payload = json.dumps(
            [item.as_dict() for item in self.coverage_candidates_for(node)],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def coverage_snapshot_sha256(self) -> str:
        """Hash every canonical coverage candidate in this request."""
        payload = json.dumps(
            [item.as_dict() for item in self.coverage_candidates],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _default_builder(repo: Path, node: PrerequisiteNode):
    """Build the proven Git changeset for one typed request node."""
    if isinstance(node, CommitNode):
        return prove_commit_changeset(repo, node.commit_sha)
    return prove_changeset(repo, node.merge_sha, node.head_sha, node.ordered_commits)


def _default_evaluator(
    repo: Path,
    changeset,
    destination: str,
    identity: SourceIdentity | CommitIdentity,
    scratch_root: Path | None,
):
    """Evaluate one proven changeset against its exact destination head."""
    return evaluate_changeset(
        repo,
        changeset,
        destination,
        source_identity=identity,
        scratch_root=scratch_root,
    )


def _default_coverage_evaluator(
    repo: Path,
    changeset,
    destination: str,
    candidate: CoverageCandidate,
    identity: SourceIdentity,
    planned_tree: str,
    scratch_root: Path | None,
):
    """Evaluate whether one open pull exactly covers the planned changeset."""
    return evaluate_existing_pull_coverage(
        repo,
        changeset,
        destination,
        candidate.head_sha,
        source_identity=identity,
        planned_tree=planned_tree,
        scratch_root=scratch_root,
    )


@dataclass(frozen=True)
class _PrerequisiteEvaluation:
    """Immutable summary of the ordered prerequisite evaluation pass."""

    records: tuple[dict[str, object], ...]
    satisfied: frozenset[str]
    frontier: tuple[dict[str, object], ...]
    terminal: Result | None


class CorePlanner:
    """Evaluate an immutable core request using local Git evidence only."""

    def __init__(
        self,
        *,
        changeset_builder: Callable[
            [Path, PrerequisiteNode], object
        ] = _default_builder,
        evaluator: Callable[
            [Path, object, str, SourceIdentity | CommitIdentity, Path | None], Result
        ] = _default_evaluator,
        coverage_evaluator: Callable[
            [
                Path,
                object,
                str,
                CoverageCandidate,
                SourceIdentity,
                str,
                Path | None,
            ],
            Result | None,
        ] = _default_coverage_evaluator,
    ) -> None:
        """Configure injectable local Git proof and coverage evaluators."""
        self.changeset_builder = changeset_builder
        self.evaluator = evaluator
        self.coverage_evaluator = coverage_evaluator

    @staticmethod
    def _context(
        result: Result, request: CoreRequest, evidence: dict[str, object]
    ) -> Result:
        """Attach canonical request identity and evidence to a core result."""
        combined = dict(result.evidence)
        combined.update(evidence)
        return Result(
            status=result.status,
            reason_code=result.reason_code,
            message=result.message,
            evidence=combined,
            source_pr=request.source.url,
            source_repository=request.source.repository,
            train_id=request.train_id,
            destination_branch=request.source.destination.branch,
            pull_request_url=result.pull_request_url,
        )

    def _evaluate(
        self,
        node: PrerequisiteNode,
        repositories: Mapping[str, Path],
        scratch_root: Path | None,
    ) -> tuple[Result, object | None, SourceIdentity | CommitIdentity | None]:
        """Build and evaluate one node while sanitizing local Git failures."""
        repo = repositories.get(node.repository)
        if repo is None:
            return (
                Result(
                    status=Status.BLOCKED_EVIDENCE,
                    reason_code="local_repository_missing",
                    message=f"No local repository was supplied for {node.repository}.",
                    evidence={"repository": node.repository},
                ),
                None,
                None,
            )
        try:
            changeset = self.changeset_builder(Path(repo), node)
            identity: SourceIdentity | CommitIdentity
            if isinstance(node, CommitNode):
                identity = CommitIdentity(node.repository, node.commit_sha)
            else:
                identity = SourceIdentity(node.repository, node.number, node.merge_sha)
            evaluated = self.evaluator(
                Path(repo),
                changeset,
                node.destination.head_sha,
                identity,
                scratch_root,
            )
            if (
                evaluated.status is Status.ALREADY_CONTAINED
                and evaluated.reason_code == "complete_changeset_already_applied"
            ):
                evaluated = Result(
                    status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
                    reason_code="patch_equivalent_review_required",
                    message=(
                        "The complete application is empty, but no exact source "
                        "ancestry or attributed destination application proves "
                        "automatic containment. Manual semantic review is required."
                    ),
                    evidence=dict(evaluated.evidence),
                )
            return evaluated, changeset, identity
        except GitEvidenceError as exc:
            return (
                Result(
                    status=Status.BLOCKED_EVIDENCE,
                    reason_code=exc.reason_code,
                    message=str(exc),
                    evidence={
                        "source_prerequisite": node.url,
                        "git_stderr": exc.stderr,
                    },
                ),
                None,
                None,
            )
        except ChangesetError as exc:
            return (
                Result(
                    status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
                    reason_code="changeset_proof_failed",
                    message=str(exc),
                    evidence={"source_prerequisite": node.url},
                ),
                None,
                None,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return (
                Result(
                    status=Status.BLOCKED_EVIDENCE,
                    reason_code="git_evidence_unavailable",
                    message="Required local Git evidence is unavailable.",
                    evidence={
                        "source_prerequisite": node.url,
                        "error_type": type(exc).__name__,
                    },
                ),
                None,
                None,
            )

    @staticmethod
    def _base_evidence(
        request: CoreRequest, prerequisite_results: list[dict[str, object]]
    ) -> dict[str, object]:
        """Build stable request and prerequisite evidence shared by every result."""
        return {
            "plan_fingerprint": request.fingerprint(),
            "dependency_mode": request.dependency_mode,
            "prerequisite_results": prerequisite_results,
            "prerequisite_order": [item.url for item in request.prerequisites],
            "prerequisite_edges": [
                edge.as_dict() for edge in request.prerequisite_edges
            ],
            "coverage_snapshot_sha256": request.coverage_snapshot_sha256_for(
                request.source
            ),
            "coverage_manifest_sha256": request.coverage_snapshot_sha256(),
            "assurance": dict(ASSURANCE),
        }

    @staticmethod
    def _prerequisite_targets(request: CoreRequest) -> dict[str, set[str]]:
        """Index each prerequisite by its direct dependency targets."""

        targets_by_source: dict[str, set[str]] = {}
        for edge in request.prerequisite_edges:
            targets_by_source.setdefault(edge.source, set()).add(edge.target)
        return targets_by_source

    @staticmethod
    def _prerequisite_record(
        prerequisite: PrerequisiteNode, result: Result
    ) -> dict[str, object]:
        """Serialize one prerequisite result for stable plan evidence."""

        return {
            "kind": prerequisite.kind,
            "url": prerequisite.url,
            "repository": prerequisite.repository,
            "destination_branch": prerequisite.destination.branch,
            "destination_head": prerequisite.destination.head_sha,
            "status": result.status.value,
            "reason_code": result.reason_code,
            "evidence": dict(result.evidence),
        }

    def _evaluate_prerequisites(
        self,
        request: CoreRequest,
        repositories: Mapping[str, Path],
        scratch_root: Path | None,
    ) -> _PrerequisiteEvaluation:
        """Evaluate prerequisites in canonical order and identify a safe frontier."""

        records: list[dict[str, object]] = []
        satisfied: set[str] = set()
        frontier: list[dict[str, object]] = []
        targets_by_source = self._prerequisite_targets(request)

        for prerequisite in request.prerequisites:
            result, _changeset, _identity = self._evaluate(
                prerequisite, repositories, scratch_root
            )
            record = self._prerequisite_record(prerequisite, result)
            records.append(record)
            evidence = self._base_evidence(request, records)
            if result.status is Status.ALREADY_CONTAINED:
                satisfied.add(prerequisite.url)
                continue
            if result.status is Status.DRAFT_PLANNED:
                if request.dependency_mode == "gate":
                    terminal = self._context(
                        Result(
                            status=Status.AWAITING_DEPENDENCIES,
                            reason_code="dependencies_not_contained",
                            message=(
                                "One or more prerequisites are not contained "
                                "in their destinations."
                            ),
                        ),
                        request,
                        evidence,
                    )
                    return _PrerequisiteEvaluation(
                        tuple(records), frozenset(satisfied), tuple(frontier), terminal
                    )
                direct_targets = targets_by_source.get(prerequisite.url, set())
                # A managed node is writable only after every direct target
                # earlier in the topological order has proven contained.
                if direct_targets <= satisfied:
                    frontier.append(dict(record))
                continue
            if result.status is Status.BLOCKED_EVIDENCE:
                terminal = self._context(result, request, evidence)
                return _PrerequisiteEvaluation(
                    tuple(records),
                    frozenset(satisfied),
                    tuple(frontier),
                    terminal,
                )
            terminal = self._context(
                Result(
                    status=Status.BLOCKED_DEPENDENCY,
                    reason_code="dependency_evaluation_blocked",
                    message="A prerequisite could not be proven safe.",
                ),
                request,
                evidence,
            )
            return _PrerequisiteEvaluation(
                tuple(records), frozenset(satisfied), tuple(frontier), terminal
            )
        return _PrerequisiteEvaluation(
            tuple(records), frozenset(satisfied), tuple(frontier), None
        )

    def _dependency_result(
        self, request: CoreRequest, evaluation: _PrerequisiteEvaluation
    ) -> Result | None:
        """Return the dependency gate result, or None when all are contained."""

        if len(evaluation.satisfied) == len(request.prerequisites):
            return None
        evidence = self._base_evidence(request, list(evaluation.records))
        if request.dependency_mode == "managed_stack" and evaluation.frontier:
            evidence["dependency_frontier"] = list(evaluation.frontier)
            return self._context(
                Result(
                    status=Status.AWAITING_DEPENDENCIES,
                    reason_code="managed_dependency_frontier",
                    message=(
                        "The next deterministic prerequisite wave is ready "
                        "for draft planning."
                    ),
                ),
                request,
                evidence,
            )
        reason = (
            "managed_dependency_frontier_empty"
            if request.dependency_mode == "managed_stack"
            else "dependencies_not_contained"
        )
        status = (
            Status.BLOCKED_DEPENDENCY
            if request.dependency_mode == "managed_stack"
            else Status.AWAITING_DEPENDENCIES
        )
        return self._context(
            Result(
                status=status,
                reason_code=reason,
                message="No safe prerequisite frontier could be established.",
            ),
            request,
            evidence,
        )

    def _evaluate_root_and_coverage(
        self,
        request: CoreRequest,
        repositories: Mapping[str, Path],
        scratch_root: Path | None,
        prerequisite_records: tuple[dict[str, object], ...],
    ) -> Result:
        """Evaluate the root and classify every exact open-PR coverage candidate."""

        root_result, changeset, identity = self._evaluate(
            request.source, repositories, scratch_root
        )
        evidence = self._base_evidence(request, list(prerequisite_records))
        if request.dependency_mode == "managed_stack":
            evidence["dependency_frontier"] = []
        if root_result.status is not Status.DRAFT_PLANNED:
            return self._context(root_result, request, evidence)
        coverage_candidates = request.coverage_candidates_for(request.source)
        if not coverage_candidates:
            return self._context(root_result, request, evidence)
        planned_tree = root_result.evidence.get("planned_tree")
        repo = repositories.get(request.source.repository)
        if (
            not isinstance(planned_tree, str)
            or SHA_RE.fullmatch(planned_tree) is None
            or repo is None
            or changeset is None
            or not isinstance(identity, SourceIdentity)
        ):
            return self._context(
                Result(
                    status=Status.BLOCKED_EVIDENCE,
                    reason_code="coverage_proof_input_missing",
                    message="Exact existing-pull coverage inputs are unavailable.",
                ),
                request,
                evidence,
            )

        coverage_results: list[dict[str, object]] = []
        exact: list[tuple[CoverageCandidate, Result]] = []
        blocked: list[tuple[CoverageCandidate, Result]] = []
        for candidate in coverage_candidates:
            evaluated = self.coverage_evaluator(
                Path(repo),
                changeset,
                request.source.destination.head_sha,
                candidate,
                identity,
                planned_tree,
                scratch_root,
            )
            if evaluated is None:
                coverage_results.append(
                    {
                        "url": candidate.url,
                        "head_sha": candidate.head_sha,
                        "outcome": "unrelated",
                    }
                )
                continue
            outcome = (
                "exact"
                if evaluated.status is Status.COVERED_BY_EXISTING_PR
                else "ambiguous"
            )
            coverage_results.append(
                {
                    "url": candidate.url,
                    "head_sha": candidate.head_sha,
                    "outcome": outcome,
                    "reason_code": evaluated.reason_code,
                    "evidence": evaluated.evidence,
                }
            )
            if evaluated.status is Status.COVERED_BY_EXISTING_PR:
                exact.append((candidate, evaluated))
            else:
                blocked.append((candidate, evaluated))
        evidence["coverage_results"] = coverage_results
        if blocked:
            return self._context(
                Result(
                    status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
                    reason_code="existing_pull_coverage_ambiguous",
                    message="An open destination pull request could not be classified safely.",
                ),
                request,
                evidence,
            )
        if len(exact) > 1:
            return self._context(
                Result(
                    status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
                    reason_code="multiple_existing_pull_coverage",
                    message="Multiple open pull candidates exactly cover this change.",
                ),
                request,
                evidence,
            )
        if exact:
            candidate, evaluated = exact[0]
            return self._context(
                Result(
                    status=Status.COVERED_BY_EXISTING_PR,
                    reason_code="covered_by_existing_pr",
                    message=(
                        "An existing open pull request exactly covers the planned "
                        "Git change; no automation branch or pull request is needed."
                    ),
                    evidence=dict(evaluated.evidence),
                    pull_request_url=candidate.url,
                ),
                request,
                evidence,
            )
        return self._context(root_result, request, evidence)

    def plan(
        self,
        request: CoreRequest,
        repositories: Mapping[str, Path],
        *,
        scratch_root: Path | None = None,
    ) -> Result:
        """Plan one request without network or destination-repository writes.

        Args:
            request: Canonical request manifest and dependency graph.
            repositories: Local repository paths keyed by OWNER/REPO.
            scratch_root: Optional disk-backed root for disposable Git worktrees.

        Returns:
            A deterministic result containing the complete decision evidence.
        """

        prerequisite_evaluation = self._evaluate_prerequisites(
            request, repositories, scratch_root
        )
        if prerequisite_evaluation.terminal is not None:
            return prerequisite_evaluation.terminal
        dependency_result = self._dependency_result(request, prerequisite_evaluation)
        if dependency_result is not None:
            return dependency_result
        return self._evaluate_root_and_coverage(
            request,
            repositories,
            scratch_root,
            prerequisite_evaluation.records,
        )
