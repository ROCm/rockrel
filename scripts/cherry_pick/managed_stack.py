# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Turn pure-core dependency frontiers into complete draft-plan artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from .core import (
    CommitNode,
    CoreRequest,
    PrerequisiteNode,
    PullRequestNode,
)
from .models import Result, Status


SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
FRONTIER_FIELDS = {
    "kind",
    "url",
    "repository",
    "destination_branch",
    "destination_head",
    "status",
    "reason_code",
    "evidence",
}


class ManagedFrontierError(ValueError):
    """A managed dependency frontier cannot be bound to write evidence."""


def _validated_frontier(
    request: CoreRequest,
    core_result: Result,
    root_plan_fingerprint: str,
    core_request_fingerprint: str,
) -> list[object]:
    """Validate the root contract and return its non-empty frontier array."""

    if (
        request.dependency_mode != "managed_stack"
        or core_result.status is not Status.AWAITING_DEPENDENCIES
        or core_result.reason_code != "managed_dependency_frontier"
    ):
        raise ManagedFrontierError("result is not a managed dependency frontier")
    if (
        DIGEST_RE.fullmatch(root_plan_fingerprint) is None
        or DIGEST_RE.fullmatch(core_request_fingerprint) is None
    ):
        raise ManagedFrontierError("managed frontier fingerprints are malformed")
    raw_frontier = core_result.evidence.get("dependency_frontier")
    if not isinstance(raw_frontier, list) or not raw_frontier:
        raise ManagedFrontierError("managed dependency frontier must be non-empty")
    return raw_frontier


def _frontier_indexes(
    request: CoreRequest,
) -> tuple[dict[str, PrerequisiteNode], dict[str, list[str]]]:
    """Index request nodes and their direct dependency targets."""

    nodes = {node.url: node for node in request.prerequisites}
    direct_targets: dict[str, list[str]] = {}
    for edge in request.prerequisite_edges:
        direct_targets.setdefault(edge.source, []).append(edge.target)
    return nodes, direct_targets


def _validated_frontier_item(
    index: int,
    raw: object,
    nodes: Mapping[str, PrerequisiteNode],
    seen: set[str],
) -> tuple[PrerequisiteNode, dict[str, object], str]:
    """Bind one core frontier record to its exact immutable request node."""

    if not isinstance(raw, dict) or set(raw) != FRONTIER_FIELDS:
        raise ManagedFrontierError(
            f"managed dependency frontier[{index}] fields are invalid"
        )
    url = raw.get("url")
    if not isinstance(url, str) or url in seen or url not in nodes:
        raise ManagedFrontierError(
            f"managed dependency frontier[{index}] identity is invalid"
        )
    seen.add(url)
    node = nodes[url]
    reason_code = raw.get("reason_code")
    if (
        raw.get("kind") != node.kind
        or raw.get("repository") != node.repository
        or raw.get("destination_branch") != node.destination.branch
        or raw.get("destination_head") != node.destination.head_sha
        or raw.get("status") != Status.DRAFT_PLANNED.value
        or not isinstance(reason_code, str)
        or not reason_code
    ):
        raise ManagedFrontierError(
            f"managed dependency frontier[{index}] does not match its request node"
        )
    core_evidence = raw.get("evidence")
    if not isinstance(core_evidence, dict):
        raise ManagedFrontierError(
            f"managed dependency frontier[{index}] evidence is invalid"
        )
    _validate_core_evidence(core_evidence, index)
    return node, core_evidence, reason_code


def _coverage_digest(request: CoreRequest, node: PrerequisiteNode) -> str:
    """Hash the canonical open-PR snapshot for one frontier node."""

    candidates = (
        request.coverage_candidates_for(node)
        if isinstance(node, PullRequestNode)
        else ()
    )
    return hashlib.sha256(
        json.dumps(
            [item.as_dict() for item in candidates],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _frontier_fingerprint(
    *,
    root_plan_fingerprint: str,
    node: PrerequisiteNode,
    core_evidence: Mapping[str, object],
    coverage_sha256: str,
) -> str:
    """Bind one frontier result to its root plan, node, proof, and coverage."""

    payload = {
        "root_plan_fingerprint": root_plan_fingerprint,
        "node": node.as_dict(),
        "core_evidence": core_evidence,
        "coverage_snapshot_sha256": coverage_sha256,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _source_write_evidence(
    node: PrerequisiteNode,
    records: Mapping[str, Mapping[str, object]],
    index: int,
) -> dict[str, object]:
    """Return the source metadata required by the draft writer."""

    if isinstance(node, PullRequestNode):
        record = records.get(node.url)
        if not isinstance(record, Mapping):
            raise ManagedFrontierError(
                f"managed dependency frontier[{index}] pull metadata is unavailable"
            )
        title = record.get("title")
        body = record.get("body")
        if not isinstance(title, str) or not isinstance(body, str):
            raise ManagedFrontierError(
                f"managed dependency frontier[{index}] pull metadata is malformed"
            )
        return {
            "source_number": node.number,
            "source_title": title,
            "source_body": body,
            "source_head": node.head_sha,
            "source_merge_commit": node.merge_sha,
        }
    if isinstance(node, CommitNode):
        return {
            "source_commit": node.commit_sha,
            "source_title": f"Cherry-pick {node.commit_sha[:12]}",
            "source_body": "",
            "source_head": node.commit_sha,
            "source_merge_commit": node.commit_sha,
        }
    raise ManagedFrontierError(
        f"managed dependency frontier[{index}] node kind is unsupported"
    )


def build_frontier_results(
    *,
    request: CoreRequest,
    core_result: Result,
    records: Mapping[str, Mapping[str, object]],
    authorization: Mapping[str, object],
    execution_context: str,
    train_mode: str,
    root_plan_fingerprint: str,
    core_request_fingerprint: str,
) -> tuple[Result, ...]:
    """Build writer-ready results for the exact next managed dependency wave.

    Raises:
        ManagedFrontierError: If the core frontier, proof evidence, source
            metadata, or fingerprint binding is malformed.
    """

    raw_frontier = _validated_frontier(
        request,
        core_result,
        root_plan_fingerprint,
        core_request_fingerprint,
    )
    nodes, direct_targets = _frontier_indexes(request)
    results: list[Result] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_frontier):
        node, core_evidence, reason_code = _validated_frontier_item(
            index, raw, nodes, seen
        )
        coverage_sha256 = _coverage_digest(request, node)
        plan_fingerprint = _frontier_fingerprint(
            root_plan_fingerprint=root_plan_fingerprint,
            node=node,
            core_evidence=core_evidence,
            coverage_sha256=coverage_sha256,
        )
        evidence = dict(core_evidence)
        # Core evidence is untrusted at this boundary. Authoritative identity,
        # authorization, and fingerprints must overwrite any colliding keys.
        evidence.update(
            {
                "authorization": json.loads(json.dumps(authorization)),
                "authorization_root_source": request.source.url,
                "execution_context": execution_context,
                "train_mode": train_mode,
                "source_kind": node.kind,
                "destination_head": node.destination.head_sha,
                "prerequisites": sorted(direct_targets.get(node.url, [])),
                "dependencies": sorted(direct_targets.get(node.url, [])),
                "dependency_status": "frontier",
                "coverage_snapshot_sha256": coverage_sha256,
                "core_request_fingerprint": core_request_fingerprint,
                "root_plan_fingerprint": root_plan_fingerprint,
                "plan_fingerprint": plan_fingerprint,
                "request_manifest": request.as_dict(),
            }
        )
        evidence.update(_source_write_evidence(node, records, index))
        results.append(
            Result(
                status=Status.DRAFT_PLANNED,
                reason_code=reason_code,
                message="This prerequisite is in the next managed draft wave.",
                evidence=evidence,
                source_pr=node.url,
                source_repository=node.repository,
                train_id=request.train_id,
                destination_branch=node.destination.branch,
            )
        )
    return tuple(results)


def _validate_core_evidence(value: Mapping[str, object], index: int) -> None:
    """Require complete Git materialization evidence for one frontier item."""
    planned_tree = value.get("planned_tree")
    commits = value.get("ordered_commits")
    changeset_kind = value.get("changeset_kind")
    proof_method = value.get("proof_method")
    mainline = value.get("mainline")
    if (
        not isinstance(planned_tree, str)
        or SHA_RE.fullmatch(planned_tree) is None
        or not isinstance(commits, list)
        or not commits
        or any(
            not isinstance(item, str) or SHA_RE.fullmatch(item) is None
            for item in commits
        )
        or not isinstance(changeset_kind, str)
        or not changeset_kind
        or not isinstance(proof_method, str)
        or not proof_method
        or (
            mainline is not None
            and (
                isinstance(mainline, bool)
                or not isinstance(mainline, int)
                or mainline < 1
            )
        )
    ):
        raise ManagedFrontierError(
            f"managed dependency frontier[{index}] core evidence is incomplete"
        )
