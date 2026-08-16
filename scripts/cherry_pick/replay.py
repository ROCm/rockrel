# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Offline historical replay support for the cherry-pick planning engine."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .config import valid_branch_name
from .git import (
    ChangesetError,
    SourceIdentity,
    WorktreeStateError,
    evaluate_changeset,
    prove_changeset,
    rollback_replay_worktree,
)
from .models import Status

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TITLE_PR_PATTERN = re.compile(r"\(#(\d+)\)")
PR_URL_PATTERN = re.compile(
    r"https://github\.com/ROCm/[A-Za-z0-9_.-]+/pull/(\d+)", re.I
)
QUALIFIED_PR_URL_PATTERN = re.compile(
    r"(?is)\bcherry[ -]?pick(?:ed|ing|s)?\b"
    r"(?:(?!\n\s*\n).){0,240}?"
    r"https://github\.com/ROCm/[A-Za-z0-9_.-]+/pull/(\d+)"
)
DEPENDENCY_PR_URL_PATTERN = re.compile(
    r"(?is)\bfor\b(?:(?!\n\s*\n).){0,80}?"
    r"\bcherry[ -]?pick(?:ed|ing|s)?\b"
    r"(?:(?!\n\s*\n).){0,80}?"
    r"https://github\.com/ROCm/[A-Za-z0-9_.-]+/pull/(\d+)"
)
EXPLICIT_PR_PATTERN = re.compile(
    r"(?i)\b(?:cherry[ -]?pick(?:ed|s)?(?:\s+(?:commit|PR))?"
    r"|original\s+PR|source\s+PR)\s*:?\s*#(\d+)"
)
BACKPORT_PR_PATTERN = re.compile(r"(?i)\bbackport\b[^\n]{0,160}\bPR\s*#(\d+)")
EXPLICIT_COMMIT_PATTERN = re.compile(
    r"(?i)\b(?:cherry[ -]?pick(?:ed|s)?|original|source|upstream)"
    r"\s+commits?\s*:?\s*([0-9a-f]{40})"
)
QUALIFIED_COMMIT_PATTERN = re.compile(
    r"(?is)\bcherry[ -]?pick(?:ed|ing|s)?\b" r"(?:(?!\n\s*\n).){0,120}?([0-9a-f]{40})"
)
SUPPORTED_REPOSITORIES = {
    "ROCm/TheRock": ("main", "https://github.com/ROCm/TheRock.git"),
    "ROCm/rocm-systems": (
        "develop",
        "https://github.com/ROCm/rocm-systems.git",
    ),
    "ROCm/rocm-libraries": (
        "develop",
        "https://github.com/ROCm/rocm-libraries.git",
    ),
}


class ReplayClassification(StrEnum):
    """Reviewed historical-case taxonomy."""

    STRICT_EXACT = "strict_exact"
    MULTI_SOURCE_BUNDLE = "multi_source_bundle"
    HISTORICAL_ADAPTATION = "historical_adaptation"
    MANUAL_RESOLUTION = "manual_resolution"
    RELEASE_NATIVE = "release_native"
    REVERT = "revert"
    GITLINK_ROLLUP = "gitlink_rollup"
    UNRESOLVED = "unresolved"


class ReplayDisposition(StrEnum):
    """Machine-readable result of replaying one historical case."""

    PASSED = "passed"
    DIAGNOSTIC = "diagnostic"
    STRICT_FAILURE = "strict_failure"
    EVIDENCE_GAP = "evidence_gap"


class ReplayExecutionPhase(StrEnum):
    """Deepest production behavior a reviewed case is expected to exercise."""

    INVENTORY = "inventory"
    CORE = "core"
    COMPONENT = "component"


class ReplayTier(StrEnum):
    FAST = "fast"
    DEEP = "deep"


REQUIRED_REPLAY_COVERAGE: dict[str, tuple[str, ...]] = {
    "repository": tuple(SUPPORTED_REPOSITORIES),
    "destination_family": (
        "therock",
        "bkc",
        "rocm_rel",
        "release_staging",
        "arbitrary",
    ),
    "classification": (
        "strict_exact",
        "multi_source_bundle",
        "historical_adaptation",
        "manual_resolution",
        "release_native",
        "revert",
        "gitlink_rollup",
    ),
    "execution_phase": (
        "inventory",
        "core",
        "component",
        "planner",
        "writer",
        "postmerge",
    ),
    "changeset_kind": ("single", "squash", "merge_commit", "rebase_range"),
    "outcome": (
        "draft_planned",
        "already_contained",
        "blocked_conflict",
        "blocked_ambiguous_changeset",
        "blocked_evidence",
        "draft_created",
        "retryable_partial_write",
    ),
    "file_operation": (
        "add",
        "modify",
        "delete",
        "rename",
        "mode",
        "symlink",
        "binary",
        "gitlink",
    ),
    "change_size": ("small", "medium", "large"),
    "recovery_mode": (
        "fresh",
        "warm",
        "interrupted",
        "corrupt_index",
        "partial_write",
    ),
}

ENGINE_COVERAGE_DIMENSIONS = {
    "changeset_kind",
    "outcome",
    "file_operation",
    "change_size",
    "recovery_mode",
}


def destination_family(branch: str) -> str:
    """Classify branch names for reporting without restricting valid branches."""

    if branch.startswith("release/bkc/"):
        return "bkc"
    if branch.startswith("release-staging/rocm-rel-"):
        return "release_staging"
    if branch.startswith("release/rocm-rel-"):
        return "rocm_rel"
    if branch.startswith("release/therock-"):
        return "therock"
    return "arbitrary"


def classify_change_size(changed_lines: int) -> str:
    """Bucket textual additions plus deletions using stable review thresholds."""

    if changed_lines < 0:
        raise ValueError("changed lines cannot be negative")
    if changed_lines <= 20:
        return "small"
    if changed_lines <= 200:
        return "medium"
    return "large"


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _sha(value: object, context: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or SHA_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{context} must be a 40-character lowercase SHA")
    return value


def _integer_list(value: object, context: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item <= 0
        for item in value
    ):
        raise ValueError(f"{context} must be a list of positive integers")
    return tuple(value)


def _sha_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list of SHAs")
    return tuple(str(_sha(item, context)) for item in value)


@dataclass(frozen=True)
class HistoricalReplayCase:
    """One immutable release-branch transition and its source evidence."""

    id: str
    repository: str
    source_branch: str
    target_branch: str
    source_prs: tuple[int, ...]
    source_merge_commit: str | None
    source_head: str | None
    source_commits: tuple[str, ...]
    target_before: str
    target_after: str
    target_after_tree: str
    provenance_method: str
    classification: ReplayClassification
    analysis_notes: str

    @classmethod
    def from_dict(cls, raw: object) -> "HistoricalReplayCase":
        value = _object(raw, "historical replay case")
        repository = _string(value.get("repository"), "case repository")
        if repository not in SUPPORTED_REPOSITORIES:
            raise ValueError(f"case repository {repository!r} is not supported")
        source_branch = _string(value.get("source_branch"), "case source_branch")
        expected_source = SUPPORTED_REPOSITORIES[repository][0]
        if source_branch != expected_source:
            raise ValueError(
                f"case source_branch must be {expected_source!r} for {repository}"
            )
        try:
            classification = ReplayClassification(value.get("classification"))
        except (TypeError, ValueError) as exc:
            raise ValueError("case classification is unknown") from exc
        source_prs = _integer_list(value.get("source_prs"), "case source_prs")
        source_merge = _sha(
            value.get("source_merge_commit"),
            "case source_merge_commit SHA",
            optional=True,
        )
        source_head = _sha(
            value.get("source_head"), "case source_head SHA", optional=True
        )
        source_commits = _sha_list(
            value.get("source_commits"), "case source_commits SHA"
        )
        if classification is ReplayClassification.STRICT_EXACT and (
            len(source_prs) != 1
            or source_merge is None
            or source_head is None
            or not source_commits
        ):
            raise ValueError(
                "strict_exact case source_prs and complete source fields are required"
            )
        return cls(
            id=_string(value.get("id"), "case id"),
            repository=repository,
            source_branch=source_branch,
            target_branch=_string(value.get("target_branch"), "case target_branch"),
            source_prs=source_prs,
            source_merge_commit=source_merge,
            source_head=source_head,
            source_commits=source_commits,
            target_before=str(
                _sha(value.get("target_before"), "case target_before SHA")
            ),
            target_after=str(_sha(value.get("target_after"), "case target_after SHA")),
            target_after_tree=str(
                _sha(value.get("target_after_tree"), "case target_after_tree SHA")
            ),
            provenance_method=_string(
                value.get("provenance_method"), "case provenance_method"
            ),
            classification=classification,
            analysis_notes=_string(value.get("analysis_notes"), "case analysis_notes"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "repository": self.repository,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "source_prs": list(self.source_prs),
            "source_merge_commit": self.source_merge_commit,
            "source_head": self.source_head,
            "source_commits": list(self.source_commits),
            "target_before": self.target_before,
            "target_after": self.target_after,
            "target_after_tree": self.target_after_tree,
            "provenance_method": self.provenance_method,
            "classification": self.classification.value,
            "analysis_notes": self.analysis_notes,
        }


@dataclass(frozen=True)
class Snapshot:
    """Pinned source and release tips for one repository."""

    source_branch: str
    source_tip: str
    targets: dict[str, str]

    @classmethod
    def from_dict(cls, raw: object, *, repository: str) -> "Snapshot":
        value = _object(raw, f"snapshot {repository}")
        source_branch = _string(
            value.get("source_branch"), f"snapshot {repository} source_branch"
        )
        expected_source = SUPPORTED_REPOSITORIES[repository][0]
        if source_branch != expected_source:
            raise ValueError(
                f"snapshot source_branch must be {expected_source!r} for {repository}"
            )
        target_value = _object(value.get("targets"), f"snapshot {repository} targets")
        if not target_value:
            raise ValueError(f"snapshot {repository} targets must not be empty")
        targets: dict[str, str] = {}
        for branch, tip in target_value.items():
            branch_name = _string(branch, f"snapshot {repository} target branch")
            targets[branch_name] = str(
                _sha(tip, f"snapshot {repository} target {branch_name} SHA")
            )
        return cls(
            source_branch=source_branch,
            source_tip=str(
                _sha(value.get("source_tip"), f"snapshot {repository} source_tip SHA")
            ),
            targets=targets,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_branch": self.source_branch,
            "source_tip": self.source_tip,
            "targets": dict(self.targets),
        }


@dataclass(frozen=True)
class CorpusManifest:
    """Validated, deterministic schema-v1 replay corpus."""

    schema_version: int
    snapshots: dict[str, Snapshot]
    cases: tuple[HistoricalReplayCase, ...]

    @classmethod
    def from_dict(cls, raw: object) -> "CorpusManifest":
        value = _object(raw, "corpus manifest")
        if value.get("schema_version") != 1:
            raise ValueError("manifest schema_version must be 1")
        snapshot_value = _object(value.get("snapshots"), "manifest snapshots")
        snapshots: dict[str, Snapshot] = {}
        for repository, snapshot in snapshot_value.items():
            if repository not in SUPPORTED_REPOSITORIES:
                raise ValueError(f"snapshot repository {repository!r} is not supported")
            snapshots[repository] = Snapshot.from_dict(snapshot, repository=repository)
        case_value = value.get("cases")
        if not isinstance(case_value, list):
            raise ValueError("manifest cases must be a list")
        cases = tuple(HistoricalReplayCase.from_dict(item) for item in case_value)
        ids = [case.id for case in cases]
        target_afters = [case.target_after for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest contains a duplicate case id")
        if len(target_afters) != len(set(target_afters)):
            raise ValueError("manifest contains a duplicate target_after SHA")
        for case in cases:
            snapshot = snapshots.get(case.repository)
            if snapshot is None or case.target_branch not in snapshot.targets:
                raise ValueError(
                    f"case {case.id} does not map to a pinned snapshot target"
                )
        return cls(schema_version=1, snapshots=snapshots, cases=cases)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshots": {
                repository: snapshot.as_dict()
                for repository, snapshot in self.snapshots.items()
            },
            "cases": [case.as_dict() for case in self.cases],
        }


def _optional_string(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context)


def _string_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{context} must be a list of non-empty strings")
    return tuple(value)


@dataclass(frozen=True)
class SyntheticCoverageEvidence:
    """One reviewed unit-test claim about deterministic non-historical coverage."""

    test_id: str
    dimensions: dict[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, raw: object) -> "SyntheticCoverageEvidence":
        value = _object(raw, "synthetic coverage evidence")
        if set(value) != {"test_id", "dimensions"}:
            raise ValueError("synthetic coverage evidence fields are invalid")
        test_id = _string(value.get("test_id"), "synthetic coverage test_id")
        if (
            re.fullmatch(
                r"scripts/tests/[A-Za-z0-9_]+_test\.py::test_[^\s]+",
                test_id,
            )
            is None
        ):
            raise ValueError("synthetic coverage test_id must be a pytest node id")
        raw_dimensions = _object(
            value.get("dimensions"), "synthetic coverage dimensions"
        )
        if not raw_dimensions:
            raise ValueError("synthetic coverage dimensions must not be empty")
        dimensions: dict[str, tuple[str, ...]] = {}
        for dimension, raw_values in raw_dimensions.items():
            if dimension not in REQUIRED_REPLAY_COVERAGE:
                raise ValueError(f"unknown coverage dimension: {dimension}")
            values = _string_list(raw_values, f"synthetic coverage {dimension} values")
            if not values or len(values) != len(set(values)):
                raise ValueError(
                    f"synthetic coverage {dimension} values must be non-empty and unique"
                )
            unknown = set(values) - set(REQUIRED_REPLAY_COVERAGE[dimension])
            if unknown:
                raise ValueError(
                    "unknown coverage cell: "
                    + ", ".join(f"{dimension}:{item}" for item in sorted(unknown))
                )
            dimensions[dimension] = values
        return cls(test_id=test_id, dimensions=dimensions)

    def as_dict(self) -> dict[str, object]:
        return {
            "test_id": self.test_id,
            "dimensions": {
                dimension: list(values)
                for dimension, values in sorted(self.dimensions.items())
            },
        }


@dataclass(frozen=True)
class SyntheticCoverageSuite:
    """Reviewed mapping from required coverage cells to concrete pytest nodes."""

    schema_version: int
    evidence: tuple[SyntheticCoverageEvidence, ...]

    @classmethod
    def from_dict(cls, raw: object) -> "SyntheticCoverageSuite":
        value = _object(raw, "synthetic coverage suite")
        if set(value) != {"schema_version", "evidence"}:
            raise ValueError("synthetic coverage suite fields are invalid")
        if value.get("schema_version") != 1:
            raise ValueError("synthetic coverage schema_version must be 1")
        raw_evidence = value.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ValueError("synthetic coverage evidence must be a non-empty list")
        evidence = tuple(
            SyntheticCoverageEvidence.from_dict(item) for item in raw_evidence
        )
        test_ids = tuple(item.test_id for item in evidence)
        if len(test_ids) != len(set(test_ids)):
            raise ValueError("synthetic coverage contains a duplicate test_id")
        return cls(schema_version=1, evidence=evidence)

    @property
    def test_ids(self) -> tuple[str, ...]:
        return tuple(item.test_id for item in self.evidence)

    def as_mapping(self) -> dict[str, dict[str, tuple[str, ...]]]:
        mapping: dict[str, dict[str, list[str]]] = {}
        for item in self.evidence:
            for dimension, values in item.dimensions.items():
                for value in values:
                    mapping.setdefault(dimension, {}).setdefault(value, []).append(
                        item.test_id
                    )
        return {
            dimension: {
                value: tuple(test_ids) for value, test_ids in sorted(values.items())
            }
            for dimension, values in sorted(mapping.items())
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "evidence": [item.as_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class ReplayExpectation:
    """Human-reviewed expected behavior for one immutable inventory case."""

    execution_phase: ReplayExecutionPhase
    expected_status: str | None
    expected_reason: str
    expected_planned_tree: str | None
    expected_conflict_paths: tuple[str, ...]
    expected_after_status: str | None
    expected_after_reason: str | None
    expected_tip_status: str | None
    expected_tip_reason: str | None
    tier: ReplayTier

    @classmethod
    def from_dict(cls, raw: object) -> "ReplayExpectation":
        value = _object(raw, "replay expectation")
        expected_keys = {
            "execution_phase",
            "expected_status",
            "expected_reason",
            "expected_planned_tree",
            "expected_conflict_paths",
            "expected_after_status",
            "expected_after_reason",
            "expected_tip_status",
            "expected_tip_reason",
            "tier",
        }
        unsupported = set(value) - expected_keys
        if unsupported:
            raise ValueError(
                "replay expectation contains unsupported fields: "
                + ", ".join(sorted(unsupported))
            )
        try:
            phase = ReplayExecutionPhase(value.get("execution_phase"))
            tier = ReplayTier(value.get("tier"))
        except (TypeError, ValueError) as exc:
            raise ValueError("replay expectation phase or tier is unknown") from exc
        status = _optional_string(value.get("expected_status"), "expected status")
        reason = _string(value.get("expected_reason"), "expected reason")
        planned_tree = _sha(
            value.get("expected_planned_tree"),
            "expected planned tree",
            optional=True,
        )
        conflict_paths = _string_list(
            value.get("expected_conflict_paths"), "expected conflict paths"
        )
        after_status = _optional_string(
            value.get("expected_after_status"), "expected after status"
        )
        after_reason = _optional_string(
            value.get("expected_after_reason"), "expected after reason"
        )
        tip_status = _optional_string(
            value.get("expected_tip_status"), "expected tip status"
        )
        tip_reason = _optional_string(
            value.get("expected_tip_reason"), "expected tip reason"
        )
        if phase is ReplayExecutionPhase.INVENTORY and status is not None:
            raise ValueError("inventory expectation status must be null")
        if phase is not ReplayExecutionPhase.INVENTORY and status is None:
            raise ValueError("executed expectation status must not be null")
        if (after_status is None) != (after_reason is None):
            raise ValueError("after status and reason must both be set or null")
        if (tip_status is None) != (tip_reason is None):
            raise ValueError("tip status and reason must both be set or null")
        return cls(
            execution_phase=phase,
            expected_status=status,
            expected_reason=reason,
            expected_planned_tree=(
                str(planned_tree) if planned_tree is not None else None
            ),
            expected_conflict_paths=conflict_paths,
            expected_after_status=after_status,
            expected_after_reason=after_reason,
            expected_tip_status=tip_status,
            expected_tip_reason=tip_reason,
            tier=tier,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "execution_phase": self.execution_phase.value,
            "expected_status": self.expected_status,
            "expected_reason": self.expected_reason,
            "expected_planned_tree": self.expected_planned_tree,
            "expected_conflict_paths": list(self.expected_conflict_paths),
            "expected_after_status": self.expected_after_status,
            "expected_after_reason": self.expected_after_reason,
            "expected_tip_status": self.expected_tip_status,
            "expected_tip_reason": self.expected_tip_reason,
            "tier": self.tier.value,
        }

    def included_in(self, tier: ReplayTier) -> bool:
        return tier is ReplayTier.DEEP or self.tier is ReplayTier.FAST


@dataclass(frozen=True)
class ReviewedCorpus:
    """Schema-v2 inventory plus case-complete immutable expectations."""

    schema_version: int
    inventory: CorpusManifest
    expectations: dict[str, ReplayExpectation]

    @classmethod
    def from_dict(cls, raw: object) -> "ReviewedCorpus":
        value = _object(raw, "reviewed corpus")
        if value.get("schema_version") != 2:
            raise ValueError("reviewed corpus schema_version must be 2")
        inventory = CorpusManifest.from_dict(value.get("inventory"))
        expectation_value = _object(
            value.get("expectations"), "reviewed corpus expectations"
        )
        expectations = {
            _string(case_id, "expectation case id"): ReplayExpectation.from_dict(
                expectation
            )
            for case_id, expectation in expectation_value.items()
        }
        case_ids = {case.id for case in inventory.cases}
        expectation_ids = set(expectations)
        if case_ids != expectation_ids:
            missing = sorted(case_ids - expectation_ids)
            extra = sorted(expectation_ids - case_ids)
            detail = []
            if missing:
                detail.append("missing " + ", ".join(missing))
            if extra:
                detail.append("unexpected " + ", ".join(extra))
            raise ValueError(
                "reviewed corpus expectation mismatch: " + "; ".join(detail)
            )
        return cls(schema_version=2, inventory=inventory, expectations=expectations)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "inventory": self.inventory.as_dict(),
            "expectations": {
                case_id: expectation.as_dict()
                for case_id, expectation in self.expectations.items()
            },
        }


@dataclass(frozen=True)
class ManifestComparison:
    added_case_ids: tuple[str, ...]
    removed_case_ids: tuple[str, ...]
    changed_case_ids: tuple[str, ...]
    snapshot_changed: bool
    exit_code: int

    def as_dict(self) -> dict[str, object]:
        return {
            "added_case_ids": list(self.added_case_ids),
            "removed_case_ids": list(self.removed_case_ids),
            "changed_case_ids": list(self.changed_case_ids),
            "snapshot_changed": self.snapshot_changed,
            "exit_code": self.exit_code,
        }


def compare_candidate_to_golden(
    candidate: CorpusManifest,
    golden: ReviewedCorpus,
) -> ManifestComparison:
    """Detect every inventory drift without consulting current engine outcomes."""

    candidate_cases = {case.id: case.as_dict() for case in candidate.cases}
    golden_cases = {case.id: case.as_dict() for case in golden.inventory.cases}
    candidate_ids = set(candidate_cases)
    golden_ids = set(golden_cases)
    added = tuple(sorted(candidate_ids - golden_ids))
    removed = tuple(sorted(golden_ids - candidate_ids))
    changed = tuple(
        sorted(
            case_id
            for case_id in candidate_ids & golden_ids
            if candidate_cases[case_id] != golden_cases[case_id]
        )
    )
    snapshot_changed = candidate.snapshots != golden.inventory.snapshots
    return ManifestComparison(
        added_case_ids=added,
        removed_case_ids=removed,
        changed_case_ids=changed,
        snapshot_changed=snapshot_changed,
        exit_code=2 if added or removed or changed or snapshot_changed else 0,
    )


@dataclass(frozen=True)
class ManifestAudit:
    total_count: int
    strict_count: int
    diagnostic_count: int
    evidence_gap_count: int
    exit_code: int


def audit_manifest(manifest: CorpusManifest) -> ManifestAudit:
    """Require a reviewed classification for every inventoried transition."""

    unresolved = sum(
        case.classification is ReplayClassification.UNRESOLVED
        for case in manifest.cases
    )
    strict = sum(
        case.classification is ReplayClassification.STRICT_EXACT
        for case in manifest.cases
    )
    return ManifestAudit(
        total_count=len(manifest.cases),
        strict_count=strict,
        diagnostic_count=len(manifest.cases) - strict - unresolved,
        evidence_gap_count=unresolved,
        exit_code=2 if unresolved else 0,
    )


@dataclass(frozen=True)
class Provenance:
    source_prs: tuple[int, ...]
    source_commits: tuple[str, ...]
    method: str


def _ordered_unique(values: Sequence[int | str]) -> tuple[int | str, ...]:
    return tuple(dict.fromkeys(values))


def is_revert_subject(subject: str) -> bool:
    """Recognize Git and conventional-commit revert titles."""

    return re.match(r"(?i)^revert(?:\s|[(:])", subject.lstrip()) is not None


def mentions_cherry_pick(subject: str, body: str) -> bool:
    """Detect an unproven historical cherry-pick claim conservatively."""

    dependency_bump = re.search(
        r"(?i)\b(?:version\s+)?bump\b.{0,80}\bfor\b.{0,40}" r"\bcherry[ -]?pick",
        subject,
    )
    if (
        dependency_bump is None
        and re.search(r"(?i)\bcherry[ -]?pick", subject) is not None
    ):
        return True
    body = DEPENDENCY_PR_URL_PATTERN.sub("", body)
    without_context = re.sub(
        r"(?i)\bcherry[ -]?pick\s+PRs?\s+to\s+(?:this|the)\s+branch\b",
        "",
        body,
    )
    return re.search(r"(?i)\bcherry[ -]?pick", without_context) is not None


def is_multi_source_claim(subject: str) -> bool:
    """Recognize titles that explicitly claim a plural cherry-pick bundle."""

    return (
        re.search(r"(?i)\bcherry[ -]?picks\b", subject) is not None
        or re.search(r"(?i)\bcherry[ -]?pick(?:ed)?\s+commits\b", subject) is not None
    )


def _explicit_commits(body: str) -> tuple[str, ...]:
    commits = list(EXPLICIT_COMMIT_PATTERN.findall(body))
    commits.extend(QUALIFIED_COMMIT_PATTERN.findall(body))
    in_commit_list = False
    for line in body.splitlines():
        stripped = line.strip()
        if re.match(
            r"(?i)^(?:#{1,6}\s*)?original commits?\s*:?.*$",
            stripped,
        ):
            in_commit_list = True
            continue
        if in_commit_list:
            match = re.match(r"^[*+-]?\s*([0-9a-f]{40})\b", stripped, re.I)
            if match:
                commits.append(match.group(1))
                continue
            if stripped and not stripped.startswith(("*", "+", "-")):
                in_commit_list = False
    return tuple(str(value).lower() for value in _ordered_unique(commits))


def _qualified_source_prs(body: str) -> tuple[int, ...]:
    dependency_numbers = {
        int(value) for value in DEPENDENCY_PR_URL_PATTERN.findall(body)
    }
    return tuple(
        int(value)
        for value in QUALIFIED_PR_URL_PATTERN.findall(body)
        if int(value) not in dependency_numbers
    )


def extract_provenance(subject: str, body: str, *, repository: str) -> Provenance:
    """Extract only explicit source PR or full-SHA evidence from a target commit."""

    if repository not in SUPPORTED_REPOSITORIES:
        raise ValueError(f"repository {repository!r} is not supported")
    if is_revert_subject(subject):
        return Provenance((), (), "none")

    commits = _explicit_commits(body)
    body_prs: list[int] = [int(value) for value in EXPLICIT_PR_PATTERN.findall(body)]
    body_prs.extend(int(value) for value in BACKPORT_PR_PATTERN.findall(body))
    body_prs.extend(_qualified_source_prs(body))

    in_pr_list = False
    pending_pr_heading = False
    for line in body.splitlines():
        stripped = line.strip()
        heading = re.match(
            r"(?i)^(?:#{1,6}\s*)?cherry[ -]?pick(?:ed|s)?\b.*:\s*$",
            stripped,
        )
        if heading:
            in_pr_list = True
            pending_pr_heading = False
            continue
        if re.match(
            r"(?i)^(?:#{1,6}\s*)?cherry[ -]?pick(?:ed|s)?\b",
            stripped,
        ):
            pending_pr_heading = True
            continue
        if pending_pr_heading:
            if not stripped:
                continue
            pending_pr_heading = False
            if stripped.endswith(":"):
                in_pr_list = True
            continue
        if in_pr_list:
            if re.match(r"^#{1,6}\s*[^\d#]", stripped):
                in_pr_list = False
                continue
            match = re.match(r"^[*+-]?\s*#(\d+)\b", stripped)
            if match is None:
                match = PR_URL_PATTERN.search(stripped)
            if match:
                body_prs.append(int(match.group(1)))
            continue

    prs = tuple(int(value) for value in _ordered_unique(body_prs))
    if not prs:
        prs = tuple(
            int(value)
            for value in _ordered_unique(EXPLICIT_PR_PATTERN.findall(subject))
        )
    if not prs:
        title_prs = tuple(int(value) for value in TITLE_PR_PATTERN.findall(subject))
        if len(title_prs) > 1:
            prs = title_prs[:-1]
    if commits:
        method = "explicit_commit"
    elif prs:
        method = "explicit_source_pr"
    else:
        method = "none"
    return Provenance(prs, commits, method)


def offline_git_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a Git environment that cannot prompt or lazy-fetch objects."""

    result = dict(os.environ if environ is None else environ)
    result.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_HTTP_LOW_SPEED_LIMIT": "1024",
            "GIT_HTTP_LOW_SPEED_TIME": "120",
        }
    )
    return result


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        env=offline_git_environment(),
    )


def classify_replay_file_operations(
    repo: str | Path,
    before: str,
    after: str,
) -> tuple[tuple[str, ...], int]:
    """Derive material Git operations and textual change size from two trees."""

    repo_path = Path(repo)
    raw = _git(
        repo_path,
        "diff-tree",
        "--no-commit-id",
        "--raw",
        "-r",
        "-M",
        before,
        after,
    )
    operations: set[str] = set()
    for line in raw.stdout.splitlines():
        header, separator, _paths = line.partition("\t")
        fields = header.split()
        if not separator or len(fields) != 5:
            continue
        old_mode = fields[0].removeprefix(":")
        new_mode = fields[1]
        status = fields[4][:1]
        operation = {"A": "add", "D": "delete", "R": "rename"}.get(status)
        if operation is not None:
            operations.add(operation)
        elif status in {"M", "T"}:
            operations.add("modify")
        if old_mode != new_mode and "000000" not in {old_mode, new_mode}:
            operations.add("mode")
        if "120000" in {old_mode, new_mode}:
            operations.add("symlink")
        if "160000" in {old_mode, new_mode}:
            operations.add("gitlink")

    numstat = _git(repo_path, "diff", "--numstat", "--no-renames", before, after)
    changed_lines = 0
    for line in numstat.stdout.splitlines():
        additions, separator, remainder = line.partition("\t")
        deletions, second_separator, _path = remainder.partition("\t")
        if not separator or not second_separator:
            continue
        if additions == "-" or deletions == "-":
            operations.add("binary")
            continue
        try:
            changed_lines += int(additions) + int(deletions)
        except ValueError:
            continue
    ordered = tuple(
        operation
        for operation in REQUIRED_REPLAY_COVERAGE["file_operation"]
        if operation in operations
    )
    return ordered, changed_lines


@dataclass(frozen=True)
class InventoryCommit:
    before: str
    after: str
    after_tree: str
    subject: str
    body: str


def inventory_release_commits(
    repo: str | Path, source_revision: str, target_revision: str
) -> tuple[InventoryCommit, ...]:
    """Enumerate every first-parent target commit not reachable from source."""

    repo_path = Path(repo)
    listing = _git(
        repo_path,
        "rev-list",
        "--reverse",
        "--first-parent",
        target_revision,
        "--not",
        source_revision,
    )
    records: list[InventoryCommit] = []
    for after in listing.stdout.splitlines():
        parents = _git(repo_path, "show", "-s", "--format=%P", after).stdout.split()
        if not parents:
            raise ValueError(
                f"release-only root commit {after} has no historical parent"
            )
        subject = _git(repo_path, "show", "-s", "--format=%s", after).stdout.rstrip(
            "\n"
        )
        body = _git(repo_path, "show", "-s", "--format=%b", after).stdout.rstrip("\n")
        tree = _git(repo_path, "rev-parse", f"{after}^{{tree}}").stdout.strip()
        records.append(
            InventoryCommit(
                before=parents[0],
                after=after,
                after_tree=tree,
                subject=subject,
                body=body,
            )
        )
    return tuple(records)


def _has_commit(repo: Path, revision: str | None) -> bool:
    if revision is None:
        return False
    result = _git(repo, "cat-file", "-e", f"{revision}^{{commit}}", check=False)
    return result.returncode == 0


@dataclass(frozen=True)
class ReplayOutcome:
    case_id: str
    repository: str
    target_branch: str
    classification: ReplayClassification
    disposition: ReplayDisposition
    engine_status: str | None
    engine_reason: str | None
    destination_tree: str | None
    planned_tree: str | None
    historical_tree: str
    strict_failure: bool
    root_cause: str
    execution_phase: str = "core"
    conflict_paths: tuple[str, ...] = ()
    postmerge_status: str | None = None
    postmerge_reason: str | None = None
    tip_status: str | None = None
    tip_reason: str | None = None
    expected_status: str | None = None
    expected_reason: str | None = None
    expectation_mismatches: tuple[str, ...] = ()
    coverage_dimensions: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "repository": self.repository,
            "target_branch": self.target_branch,
            "classification": self.classification.value,
            "disposition": self.disposition.value,
            "engine_status": self.engine_status,
            "engine_reason": self.engine_reason,
            "destination_tree": self.destination_tree,
            "planned_tree": self.planned_tree,
            "historical_tree": self.historical_tree,
            "strict_failure": self.strict_failure,
            "root_cause": self.root_cause,
            "execution_phase": self.execution_phase,
            "conflict_paths": list(self.conflict_paths),
            "postmerge_status": self.postmerge_status,
            "postmerge_reason": self.postmerge_reason,
            "tip_status": self.tip_status,
            "tip_reason": self.tip_reason,
            "expected_status": self.expected_status,
            "expected_reason": self.expected_reason,
            "expectation_mismatches": list(self.expectation_mismatches),
            "coverage_dimensions": {
                dimension: list(values)
                for dimension, values in sorted(self.coverage_dimensions.items())
            },
        }


def compare_outcome_to_expectation(
    outcome: ReplayOutcome,
    expectation: ReplayExpectation,
) -> tuple[str, ...]:
    """Return every safety-relevant field that differs from reviewed evidence."""

    comparisons = (
        ("engine_status", outcome.engine_status, expectation.expected_status),
        ("engine_reason", outcome.engine_reason, expectation.expected_reason),
        ("planned_tree", outcome.planned_tree, expectation.expected_planned_tree),
        (
            "conflict_paths",
            tuple(outcome.conflict_paths),
            expectation.expected_conflict_paths,
        ),
        (
            "postmerge_status",
            outcome.postmerge_status,
            expectation.expected_after_status,
        ),
        (
            "postmerge_reason",
            outcome.postmerge_reason,
            expectation.expected_after_reason,
        ),
        ("tip_status", outcome.tip_status, expectation.expected_tip_status),
        ("tip_reason", outcome.tip_reason, expectation.expected_tip_reason),
    )
    return tuple(name for name, actual, expected in comparisons if actual != expected)


@dataclass(frozen=True)
class ReplayCoverageAudit:
    """Independent historical and synthetic evidence for required replay cells."""

    historical: dict[str, dict[str, int]]
    synthetic: dict[str, dict[str, tuple[str, ...]]]
    historical_gaps: tuple[str, ...]
    gaps: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "historical": {
                dimension: dict(sorted(values.items()))
                for dimension, values in sorted(self.historical.items())
            },
            "synthetic": {
                dimension: {
                    value: list(test_ids) for value, test_ids in sorted(values.items())
                }
                for dimension, values in sorted(self.synthetic.items())
            },
            "historical_gaps": list(self.historical_gaps),
            "gaps": list(self.gaps),
        }


def _coverage_values(outcome: ReplayOutcome, dimension: str) -> tuple[str, ...]:
    if dimension in ENGINE_COVERAGE_DIMENSIONS and (
        outcome.execution_phase == ReplayExecutionPhase.INVENTORY.value
    ):
        return ()
    dimensions = outcome.coverage_dimensions
    if dimension in dimensions:
        return dimensions[dimension]
    value = getattr(outcome, dimension, None)
    if value is None:
        return ()
    if isinstance(value, StrEnum):
        return (value.value,)
    return (str(value),)


def audit_replay_coverage(
    outcomes: Sequence[ReplayOutcome],
    *,
    synthetic: Mapping[str, Mapping[str, Sequence[str]]],
    required: Mapping[str, Sequence[str]],
) -> ReplayCoverageAudit:
    """Audit required cells without presenting synthetic tests as history."""

    historical: dict[str, dict[str, int]] = {}
    normalized_synthetic: dict[str, dict[str, tuple[str, ...]]] = {}
    historical_gaps: list[str] = []
    gaps: list[str] = []
    for dimension, required_values in required.items():
        counts = Counter(
            value
            for outcome in outcomes
            for value in _coverage_values(outcome, dimension)
        )
        historical[dimension] = dict(sorted(counts.items()))
        supplied = synthetic.get(dimension, {})
        normalized_synthetic[dimension] = {
            value: tuple(test_ids) for value, test_ids in sorted(supplied.items())
        }
        for value in required_values:
            cell = f"{dimension}:{value}"
            if counts[value] == 0:
                historical_gaps.append(cell)
                if not normalized_synthetic[dimension].get(value):
                    gaps.append(cell)
    return ReplayCoverageAudit(
        historical=historical,
        synthetic=normalized_synthetic,
        historical_gaps=tuple(historical_gaps),
        gaps=tuple(gaps),
    )


def _outcome(
    case: HistoricalReplayCase,
    disposition: ReplayDisposition,
    root_cause: str,
    *,
    engine_status: str | None = None,
    engine_reason: str | None = None,
    destination_tree: str | None = None,
    planned_tree: str | None = None,
    execution_phase: str = "core",
    conflict_paths: tuple[str, ...] = (),
    postmerge_status: str | None = None,
    postmerge_reason: str | None = None,
    tip_status: str | None = None,
    tip_reason: str | None = None,
    expected_status: str | None = None,
    expected_reason: str | None = None,
    expectation_mismatches: tuple[str, ...] = (),
    coverage_dimensions: Mapping[str, Sequence[str]] | None = None,
) -> ReplayOutcome:
    dimensions: dict[str, tuple[str, ...]] = {
        "repository": (case.repository,),
        "destination_family": (destination_family(case.target_branch),),
        "classification": (case.classification.value,),
        "execution_phase": (execution_phase,),
    }
    if engine_status is not None:
        dimensions["outcome"] = (engine_status,)
    for dimension, values in (coverage_dimensions or {}).items():
        dimensions[dimension] = tuple(dict.fromkeys(values))
    return ReplayOutcome(
        case_id=case.id,
        repository=case.repository,
        target_branch=case.target_branch,
        classification=case.classification,
        disposition=disposition,
        engine_status=engine_status,
        engine_reason=engine_reason,
        destination_tree=destination_tree,
        planned_tree=planned_tree,
        historical_tree=case.target_after_tree,
        strict_failure=disposition is ReplayDisposition.STRICT_FAILURE,
        root_cause=root_cause,
        execution_phase=execution_phase,
        conflict_paths=conflict_paths,
        postmerge_status=postmerge_status,
        postmerge_reason=postmerge_reason,
        tip_status=tip_status,
        tip_reason=tip_reason,
        expected_status=expected_status,
        expected_reason=expected_reason,
        expectation_mismatches=expectation_mismatches,
        coverage_dimensions=dimensions,
    )


def run_replay_case(
    repo: str | Path,
    case: HistoricalReplayCase,
    *,
    worktree_path: str | Path | None = None,
) -> ReplayOutcome:
    """Replay one case without permitting Git to hydrate missing objects."""

    repo_path = Path(repo)
    strict = case.classification is ReplayClassification.STRICT_EXACT
    if case.classification is ReplayClassification.UNRESOLVED:
        return _outcome(case, ReplayDisposition.EVIDENCE_GAP, "unresolved_provenance")
    if not _has_commit(repo_path, case.target_before) or not _has_commit(
        repo_path, case.target_after
    ):
        return _outcome(case, ReplayDisposition.EVIDENCE_GAP, "missing_target_evidence")
    source_objects = (
        case.source_merge_commit,
        case.source_head,
        *case.source_commits,
    )
    if any(not _has_commit(repo_path, item) for item in source_objects):
        if strict:
            return _outcome(
                case, ReplayDisposition.EVIDENCE_GAP, "missing_source_evidence"
            )
        return _outcome(
            case, ReplayDisposition.DIAGNOSTIC, "diagnostic_source_unavailable"
        )
    if case.source_merge_commit is None or case.source_head is None:
        return _outcome(
            case,
            ReplayDisposition.EVIDENCE_GAP if strict else ReplayDisposition.DIAGNOSTIC,
            "incomplete_source_provenance",
        )
    try:
        changeset = prove_changeset(
            repo_path,
            case.source_merge_commit,
            case.source_head,
            case.source_commits,
        )
    except ChangesetError:
        return _outcome(
            case,
            (
                ReplayDisposition.STRICT_FAILURE
                if strict
                else ReplayDisposition.DIAGNOSTIC
            ),
            "changeset_proof_failed",
        )
    result = evaluate_changeset(
        repo_path,
        changeset,
        case.target_before,
        worktree_path=worktree_path,
    )
    destination_tree = result.evidence.get("destination_tree")
    planned_tree = result.evidence.get("planned_tree")
    destination_value = destination_tree if isinstance(destination_tree, str) else None
    planned_value = planned_tree if isinstance(planned_tree, str) else None
    conflict_value = result.evidence.get("conflict_paths")
    conflict_paths = (
        tuple(item for item in conflict_value if isinstance(item, str))
        if isinstance(conflict_value, list)
        else ()
    )
    operations, changed_lines = classify_replay_file_operations(
        repo_path,
        case.target_before,
        case.target_after,
    )
    coverage_dimensions = {
        "changeset_kind": (changeset.kind.value,),
        "file_operation": operations,
        "change_size": (classify_change_size(changed_lines),),
        "recovery_mode": ("warm" if worktree_path is not None else "fresh",),
    }
    if not strict:
        return _outcome(
            case,
            ReplayDisposition.DIAGNOSTIC,
            f"diagnostic_{result.reason_code}",
            engine_status=result.status.value,
            engine_reason=result.reason_code,
            destination_tree=destination_value,
            planned_tree=planned_value,
            conflict_paths=conflict_paths,
            coverage_dimensions=coverage_dimensions,
        )
    if result.status is Status.BLOCKED_EVIDENCE:
        return _outcome(
            case,
            ReplayDisposition.EVIDENCE_GAP,
            result.reason_code,
            engine_status=result.status.value,
            engine_reason=result.reason_code,
            destination_tree=destination_value,
            planned_tree=planned_value,
            conflict_paths=conflict_paths,
            coverage_dimensions=coverage_dimensions,
        )
    if result.status is not Status.DRAFT_PLANNED:
        return _outcome(
            case,
            ReplayDisposition.STRICT_FAILURE,
            f"unexpected_{result.reason_code}",
            engine_status=result.status.value,
            engine_reason=result.reason_code,
            destination_tree=destination_value,
            planned_tree=planned_value,
            conflict_paths=conflict_paths,
            coverage_dimensions=coverage_dimensions,
        )
    if planned_value != case.target_after_tree:
        return _outcome(
            case,
            ReplayDisposition.STRICT_FAILURE,
            "planned_tree_mismatch",
            engine_status=result.status.value,
            engine_reason=result.reason_code,
            destination_tree=destination_value,
            planned_tree=planned_value,
            conflict_paths=conflict_paths,
            coverage_dimensions=coverage_dimensions,
        )
    return _outcome(
        case,
        ReplayDisposition.PASSED,
        "exact_historical_tree",
        engine_status=result.status.value,
        engine_reason=result.reason_code,
        destination_tree=destination_value,
        planned_tree=planned_value,
        conflict_paths=conflict_paths,
        coverage_dimensions=coverage_dimensions,
    )


def _source_identity(case: HistoricalReplayCase) -> SourceIdentity | None:
    if len(case.source_prs) != 1 or case.source_merge_commit is None:
        return None
    return SourceIdentity(
        repository=case.repository,
        pull_number=case.source_prs[0],
        merge_commit=case.source_merge_commit,
    )


def run_reviewed_case(
    repo: str | Path,
    case: HistoricalReplayCase,
    expectation: ReplayExpectation,
    *,
    target_tip: str,
    worktree_path: str | Path | None = None,
) -> ReplayOutcome:
    """Run one case against immutable forward and containment expectations."""

    if expectation.execution_phase is ReplayExecutionPhase.INVENTORY:
        return _outcome(
            case,
            ReplayDisposition.DIAGNOSTIC,
            expectation.expected_reason,
            execution_phase=expectation.execution_phase.value,
            expected_status=expectation.expected_status,
            expected_reason=expectation.expected_reason,
        )

    base = run_replay_case(repo, case, worktree_path=worktree_path)
    postmerge_status = None
    postmerge_reason = None
    tip_status = None
    tip_reason = None
    if expectation.expected_after_status is not None:
        try:
            if (
                case.source_merge_commit is None
                or case.source_head is None
                or not case.source_commits
            ):
                raise ChangesetError("reviewed containment source evidence missing")
            changeset = prove_changeset(
                repo,
                case.source_merge_commit,
                case.source_head,
                case.source_commits,
            )
            identity = _source_identity(case)
            after = evaluate_changeset(
                repo,
                changeset,
                case.target_after,
                worktree_path=worktree_path,
                source_identity=identity,
            )
            postmerge_status = after.status.value
            postmerge_reason = after.reason_code
            tip = evaluate_changeset(
                repo,
                changeset,
                target_tip,
                worktree_path=worktree_path,
                source_identity=identity,
            )
            tip_status = tip.status.value
            tip_reason = tip.reason_code
        except (ChangesetError, OSError, WorktreeStateError):
            postmerge_status = "blocked_evidence"
            postmerge_reason = "reviewed_containment_evidence_unavailable"
            tip_status = postmerge_status
            tip_reason = postmerge_reason

    coverage_dimensions = dict(base.coverage_dimensions)
    phases = list(coverage_dimensions.get("execution_phase", ()))
    outcomes = list(coverage_dimensions.get("outcome", ()))
    if expectation.expected_after_status is not None:
        phases.append("postmerge")
    outcomes.extend(
        status for status in (postmerge_status, tip_status) if status is not None
    )
    coverage_dimensions["execution_phase"] = tuple(dict.fromkeys(phases))
    coverage_dimensions["outcome"] = tuple(sorted(set(outcomes)))
    evaluated = _outcome(
        case,
        base.disposition,
        "reviewed_expectation_match",
        engine_status=base.engine_status,
        engine_reason=base.engine_reason,
        destination_tree=base.destination_tree,
        planned_tree=base.planned_tree,
        execution_phase=expectation.execution_phase.value,
        conflict_paths=base.conflict_paths,
        postmerge_status=postmerge_status,
        postmerge_reason=postmerge_reason,
        tip_status=tip_status,
        tip_reason=tip_reason,
        expected_status=expectation.expected_status,
        expected_reason=expectation.expected_reason,
        coverage_dimensions=coverage_dimensions,
    )
    mismatches = compare_outcome_to_expectation(evaluated, expectation)
    disposition = ReplayDisposition.STRICT_FAILURE if mismatches else base.disposition
    return _outcome(
        case,
        disposition,
        "reviewed_expectation_mismatch" if mismatches else "reviewed_expectation_match",
        engine_status=base.engine_status,
        engine_reason=base.engine_reason,
        destination_tree=base.destination_tree,
        planned_tree=base.planned_tree,
        execution_phase=expectation.execution_phase.value,
        conflict_paths=base.conflict_paths,
        postmerge_status=postmerge_status,
        postmerge_reason=postmerge_reason,
        tip_status=tip_status,
        tip_reason=tip_reason,
        expected_status=expectation.expected_status,
        expected_reason=expectation.expected_reason,
        expectation_mismatches=mismatches,
        coverage_dimensions=coverage_dimensions,
    )


def run_reviewed_cases(
    data_root: str | Path,
    corpus: ReviewedCorpus,
    *,
    tier: ReplayTier | str = ReplayTier.FAST,
    jobs: int = 4,
) -> tuple[ReplayOutcome, ...]:
    """Replay a reviewed tier concurrently with deterministic manifest order."""

    selected_tier = ReplayTier(tier)
    selected = tuple(
        (index, case, corpus.expectations[case.id])
        for index, case in enumerate(corpus.inventory.cases)
        if corpus.expectations[case.id].included_in(selected_tier)
    )
    if jobs < 1:
        raise ValueError("replay jobs must be at least 1")
    root = Path(data_root)
    grouped: dict[str, list[tuple[int, HistoricalReplayCase, ReplayExpectation]]] = {}
    for item in selected:
        grouped.setdefault(item[1].repository, []).append(item)

    def execute_group(
        item: tuple[str, list[tuple[int, HistoricalReplayCase, ReplayExpectation]]],
    ) -> tuple[tuple[int, ReplayOutcome], ...]:
        repository, cases = item
        slug = repository.split("/", 1)[1]
        repo = root / f"{slug}.git"
        worktree = root / ".cherry-pick-replay-worktrees" / slug
        snapshot = corpus.inventory.snapshots[repository]
        return tuple(
            (
                index,
                run_reviewed_case(
                    repo,
                    case,
                    expectation,
                    target_tip=snapshot.targets[case.target_branch],
                    worktree_path=worktree,
                ),
            )
            for index, case, expectation in cases
        )

    groups = tuple(grouped.items())
    if jobs == 1 or len(groups) <= 1:
        indexed = tuple(outcome for group in groups for outcome in execute_group(group))
    else:
        with ThreadPoolExecutor(
            max_workers=min(jobs, len(groups)),
            thread_name_prefix="reviewed-cherry-pick-replay",
        ) as executor:
            indexed = tuple(
                outcome
                for group_outcomes in executor.map(execute_group, groups)
                for outcome in group_outcomes
            )
    return tuple(outcome for _index, outcome in sorted(indexed))


def run_replay_cases(
    data_root: str | Path,
    cases: Sequence[HistoricalReplayCase],
    *,
    jobs: int = 4,
) -> tuple[ReplayOutcome, ...]:
    """Replay cases concurrently while retaining deterministic manifest order."""

    if jobs < 1:
        raise ValueError("replay jobs must be at least 1")
    root = Path(data_root)
    indexed_groups: dict[str, list[tuple[int, HistoricalReplayCase]]] = {}
    for index, case in enumerate(cases):
        indexed_groups.setdefault(case.repository, []).append((index, case))

    def execute_group(
        item: tuple[str, list[tuple[int, HistoricalReplayCase]]],
    ) -> tuple[tuple[int, ReplayOutcome], ...]:
        repository, indexed_cases = item
        slug = repository.split("/", 1)[1]
        repo = root / f"{slug}.git"
        worktree = root / ".cherry-pick-replay-worktrees" / slug
        return tuple(
            (
                index,
                run_replay_case(repo, case, worktree_path=worktree),
            )
            for index, case in indexed_cases
        )

    groups = tuple(indexed_groups.items())
    if jobs == 1 or len(groups) <= 1:
        indexed_outcomes = tuple(
            outcome for group in groups for outcome in execute_group(group)
        )
    else:
        with ThreadPoolExecutor(
            max_workers=min(jobs, len(groups)),
            thread_name_prefix="cherry-pick-replay",
        ) as executor:
            indexed_outcomes = tuple(
                outcome
                for group_outcomes in executor.map(execute_group, groups)
                for outcome in group_outcomes
            )
    return tuple(
        outcome
        for _index, outcome in sorted(indexed_outcomes, key=lambda item: item[0])
    )


def rollback_replay_worktrees(data_root: str | Path) -> dict[str, str]:
    """Clean every persistent replay worktree without recreating its index."""

    root = Path(data_root)
    results: dict[str, str] = {}
    for repository in SUPPORTED_REPOSITORIES:
        slug = repository.split("/", 1)[1]
        repo = root / f"{slug}.git"
        worktree = root / ".cherry-pick-replay-worktrees" / slug
        if not worktree.exists():
            results[repository] = "absent"
            continue
        target = rollback_replay_worktree(repo, worktree)
        results[repository] = f"rolled_back:{target}"
    return results


@dataclass(frozen=True)
class ReplayReport:
    outcomes: tuple[ReplayOutcome, ...]
    counts: dict[str, int]
    exit_code: int
    coverage: ReplayCoverageAudit | None = None

    @classmethod
    def from_outcomes(
        cls,
        outcomes: Sequence[ReplayOutcome],
        *,
        coverage: ReplayCoverageAudit | None = None,
    ) -> "ReplayReport":
        values = tuple(outcomes)
        counts = Counter(outcome.disposition.value for outcome in values)
        if counts[ReplayDisposition.EVIDENCE_GAP.value] or (
            coverage is not None and coverage.gaps
        ):
            exit_code = 2
        elif counts[ReplayDisposition.STRICT_FAILURE.value]:
            exit_code = 1
        else:
            exit_code = 0
        return cls(values, dict(sorted(counts.items())), exit_code, coverage)

    def as_dict(self) -> dict[str, object]:
        execution_counts = Counter(outcome.execution_phase for outcome in self.outcomes)
        return {
            "schema_version": 3,
            "exit_code": self.exit_code,
            "counts": dict(self.counts),
            "execution_counts": dict(sorted(execution_counts.items())),
            "coverage": self.coverage.as_dict() if self.coverage is not None else None,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }


def render_markdown_report(report: ReplayReport) -> str:
    """Render a review-oriented, deterministic replay report."""

    strict_total = sum(
        outcome.classification is ReplayClassification.STRICT_EXACT
        for outcome in report.outcomes
    )
    strict_passed = sum(
        outcome.disposition is ReplayDisposition.PASSED for outcome in report.outcomes
    )
    evidence_gaps = report.counts.get(ReplayDisposition.EVIDENCE_GAP.value, 0)
    coverage_gaps = len(report.coverage.gaps) if report.coverage is not None else 0
    historical_gaps = (
        len(report.coverage.historical_gaps) if report.coverage is not None else 0
    )
    lines = [
        "# Historical cherry-pick replay",
        "",
        f"- Strict eligible: {strict_passed}/{strict_total} passed",
        f"- Evidence gaps: {evidence_gaps}",
        f"- Historical coverage gaps: {historical_gaps}",
        f"- Uncovered required cells: {coverage_gaps}",
        f"- Exit code: {report.exit_code}",
        "- Execution depth: "
        + ", ".join(
            f"{phase}={count}"
            for phase, count in sorted(
                Counter(item.execution_phase for item in report.outcomes).items()
            )
        ),
    ]
    if report.coverage is not None:
        lines.extend(
            (
                "",
                "## Coverage gaps",
                "",
                "- Historical-only gaps: "
                + (", ".join(report.coverage.historical_gaps) or "none"),
                "- Uncovered required cells: "
                + (", ".join(report.coverage.gaps) or "none"),
            )
        )
    lines.extend(
        (
            "",
            "| Case | Repository | Branch | Classification | Phase | Result | Root cause |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        )
    )
    for outcome in report.outcomes:
        lines.append(
            "| "
            + " | ".join(
                (
                    outcome.case_id,
                    outcome.repository,
                    outcome.target_branch,
                    outcome.classification.value,
                    outcome.execution_phase,
                    outcome.disposition.value,
                    outcome.root_cause,
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class MirrorSpec:
    """Allowlisted official mirror input."""

    repository: str
    source_branch: str
    target_branches: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.repository not in SUPPORTED_REPOSITORIES:
            raise ValueError(f"repository {self.repository!r} is not supported")
        expected_source = SUPPORTED_REPOSITORIES[self.repository][0]
        if self.source_branch != expected_source:
            raise ValueError(
                f"source branch must be {expected_source!r} for {self.repository}"
            )
        if not self.target_branches or len(set(self.target_branches)) != len(
            self.target_branches
        ):
            raise ValueError("target branches must be a non-empty unique sequence")
        for branch in self.target_branches:
            if not valid_branch_name(branch):
                raise ValueError(f"target branch {branch!r} is not a safe Git branch")

    @property
    def url(self) -> str:
        return SUPPORTED_REPOSITORIES[self.repository][1]


DEFAULT_MIRROR_SPECS = (
    MirrorSpec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=(
            "release/therock-7.12",
            "release/therock-7.14",
            "release/therock-10.0",
        ),
    ),
    MirrorSpec(
        repository="ROCm/rocm-systems",
        source_branch="develop",
        target_branches=(
            "release/therock-7.12",
            "release/therock-7.14",
            "release/therock-10.0",
        ),
    ),
    MirrorSpec(
        repository="ROCm/rocm-libraries",
        source_branch="develop",
        target_branches=(
            "release/therock-7.12",
            "release/therock-7.14",
            "release/therock-10.0",
        ),
    ),
)


def _mirror_path(data_root: str | Path, repository: str) -> Path:
    return Path(data_root) / f"{repository.split('/', 1)[1]}.git"


def _remote_ref(branch: str) -> str:
    return f"refs/remotes/origin/{branch}"


def _resolve_required(repo: Path, revision: str) -> str:
    result = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"required replay revision is unavailable: {revision}")
    return result.stdout.strip()


def _resolve_optional(repo: Path, revision: str) -> str | None:
    result = _git(
        repo,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _source_merge_for_pr(repo: Path, source_tip: str, number: int) -> str | None:
    log = _git(
        repo,
        "log",
        "--format=%H%x09%s",
        f"--fixed-strings",
        f"--grep=(#{number})",
        source_tip,
        check=False,
    )
    if log.returncode != 0:
        return None
    marker = f"(#{number})"
    candidates = []
    for line in log.stdout.splitlines():
        commit, separator, subject = line.partition("\t")
        if separator and marker in subject and SHA_PATTERN.fullmatch(commit):
            candidates.append(commit)
    return candidates[0] if len(candidates) == 1 else None


def _source_inputs_for_pr(
    repo: Path, source_tip: str, number: int
) -> tuple[str, str, tuple[str, ...]] | None:
    source_merge = _source_merge_for_pr(repo, source_tip, number)
    source_head_result = _git(
        repo,
        "rev-parse",
        "--verify",
        f"refs/pull/{number}/head^{{commit}}",
        check=False,
    )
    if source_merge is None or source_head_result.returncode != 0:
        return None
    source_head = source_head_result.stdout.strip()
    parents = _git(repo, "show", "-s", "--format=%P", source_merge).stdout.split()
    if len(parents) == 2:
        base = parents[0]
    elif len(parents) == 1:
        merge_base = _git(repo, "merge-base", parents[0], source_head, check=False)
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            return None
        base = merge_base.stdout.strip()
    else:
        return None
    commits_result = _git(
        repo, "rev-list", "--reverse", f"{base}..{source_head}", check=False
    )
    commits = tuple(commits_result.stdout.splitlines())
    if commits_result.returncode != 0 or not commits or commits[-1] != source_head:
        return None
    return source_merge, source_head, commits


def _source_inputs_for_commit(
    repo: Path, commit: str
) -> tuple[str, str, tuple[str, ...]] | None:
    if not _has_commit(repo, commit):
        return None
    parents = _git(repo, "show", "-s", "--format=%P", commit).stdout.split()
    if len(parents) == 1:
        return commit, commit, (commit,)
    if len(parents) != 2:
        return None
    source_head = parents[1]
    commits_result = _git(
        repo, "rev-list", "--reverse", f"{parents[0]}..{source_head}", check=False
    )
    commits = tuple(commits_result.stdout.splitlines())
    if commits_result.returncode != 0 or not commits:
        return None
    return commit, source_head, commits


def _has_gitlink_delta(repo: Path, before: str, after: str) -> bool:
    result = _git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--raw",
        "-r",
        before,
        after,
        check=False,
    )
    return result.returncode == 0 and any(
        field == "160000" for field in result.stdout.split()
    )


def _case_id(repository: str, branch: str, after: str) -> str:
    identity = f"{repository}-{branch}-{after[:12]}"
    return re.sub(r"[^A-Za-z0-9._-]", "-", identity)


def _classify_inventory_record(
    repo: Path,
    spec: MirrorSpec,
    source_tip: str,
    branch: str,
    record: InventoryCommit,
    *,
    worktree_path: Path | None = None,
) -> HistoricalReplayCase:
    provenance = extract_provenance(
        record.subject, record.body, repository=spec.repository
    )
    source_merge: str | None = None
    source_head: str | None = None
    source_commits: tuple[str, ...] = ()
    classification = ReplayClassification.UNRESOLVED
    notes = "Source provenance requires review."

    if is_revert_subject(record.subject):
        classification = ReplayClassification.REVERT
        notes = "Release transition is an explicit revert, not a strict backport."
    elif _has_gitlink_delta(repo, record.before, record.after):
        classification = ReplayClassification.GITLINK_ROLLUP
        notes = "Transition changes a gitlink and is retained as a diagnostic rollup."
    elif (
        len(provenance.source_prs) > 1
        or len(provenance.source_commits) > 1
        or is_multi_source_claim(record.subject)
    ):
        classification = ReplayClassification.MULTI_SOURCE_BUNDLE
        notes = "Transition explicitly combines multiple source changes."
    elif len(provenance.source_prs) == 1:
        inputs = _source_inputs_for_pr(repo, source_tip, provenance.source_prs[0])
        if inputs is not None:
            source_merge, source_head, source_commits = inputs
        else:
            pr_head = _resolve_optional(
                repo,
                f"refs/pull/{provenance.source_prs[0]}/head",
            )
            direct_inputs = (
                _source_inputs_for_commit(repo, pr_head)
                if pr_head is not None
                else None
            )
            if direct_inputs is not None:
                source_merge, source_head, source_commits = direct_inputs
                classification = ReplayClassification.HISTORICAL_ADAPTATION
                notes = (
                    "Explicit PR head is not merged into the pinned source "
                    "snapshot; retained diagnostically."
                )
    elif len(provenance.source_commits) == 1:
        inputs = _source_inputs_for_commit(repo, provenance.source_commits[0])
        if inputs is not None:
            source_merge, source_head, source_commits = inputs
            classification = ReplayClassification.HISTORICAL_ADAPTATION
            notes = "Explicit commit lacks one canonical source PR; diagnostic only."
    else:
        if mentions_cherry_pick(record.subject, record.body):
            classification = ReplayClassification.UNRESOLVED
            notes = "Cherry-pick claim has no positive source provenance."
        else:
            classification = ReplayClassification.RELEASE_NATIVE
            notes = "No positive source provenance; treated as release-native, not a cherry-pick."

    provisional = HistoricalReplayCase(
        id=_case_id(spec.repository, branch, record.after),
        repository=spec.repository,
        source_branch=spec.source_branch,
        target_branch=branch,
        source_prs=provenance.source_prs,
        source_merge_commit=source_merge,
        source_head=source_head,
        source_commits=source_commits,
        target_before=record.before,
        target_after=record.after,
        target_after_tree=record.after_tree,
        provenance_method=provenance.method,
        classification=classification,
        analysis_notes=notes,
    )
    if (
        classification is not ReplayClassification.UNRESOLVED
        or len(provenance.source_prs) != 1
        or source_merge is None
        or source_head is None
        or not source_commits
    ):
        return provisional

    strict_candidate = HistoricalReplayCase(
        **{
            **provisional.__dict__,
            "classification": ReplayClassification.STRICT_EXACT,
            "analysis_notes": "One canonical source PR; pending exact replay.",
        }
    )
    outcome = run_replay_case(
        repo,
        strict_candidate,
        worktree_path=worktree_path,
    )
    if outcome.disposition is ReplayDisposition.PASSED:
        return HistoricalReplayCase(
            **{
                **strict_candidate.__dict__,
                "analysis_notes": "One-source replay exactly reproduces the historical tree.",
            }
        )
    if outcome.root_cause.startswith("unexpected_cherry_pick_conflict"):
        new_classification = ReplayClassification.MANUAL_RESOLUTION
        new_notes = "Canonical source conflicts with the historical parent; manual result retained diagnostically."
    elif outcome.root_cause == "planned_tree_mismatch":
        new_classification = ReplayClassification.HISTORICAL_ADAPTATION
        new_notes = (
            "Canonical source applies but does not reproduce the historical tree."
        )
    else:
        new_classification = ReplayClassification.UNRESOLVED
        new_notes = f"Strict qualification failed: {outcome.root_cause}."
    return HistoricalReplayCase(
        **{
            **provisional.__dict__,
            "classification": new_classification,
            "analysis_notes": new_notes,
        }
    )


def discover_corpus_pull_requests(
    specs: Sequence[MirrorSpec], data_root: str | Path
) -> dict[str, tuple[int, ...]]:
    """Discover explicit source PR refs needed by the second refresh pass."""

    discovered: dict[str, tuple[int, ...]] = {}
    for spec in specs:
        repo = _mirror_path(data_root, spec.repository)
        source = _resolve_required(repo, _remote_ref(spec.source_branch))
        numbers: list[int] = []
        for branch in spec.target_branches:
            target = _resolve_required(repo, _remote_ref(branch))
            for record in inventory_release_commits(repo, source, target):
                if is_revert_subject(record.subject) or _has_gitlink_delta(
                    repo, record.before, record.after
                ):
                    continue
                provenance = extract_provenance(
                    record.subject, record.body, repository=spec.repository
                )
                numbers.extend(provenance.source_prs)
        discovered[spec.repository] = tuple(sorted(set(numbers)))
    return discovered


def build_corpus_manifest(
    specs: Sequence[MirrorSpec], data_root: str | Path
) -> CorpusManifest:
    """Build and auto-classify an exhaustive manifest from pinned local refs."""

    snapshots: dict[str, Snapshot] = {}
    cases: list[HistoricalReplayCase] = []
    for spec in specs:
        repo = _mirror_path(data_root, spec.repository)
        worktree = (
            Path(data_root)
            / ".cherry-pick-replay-worktrees"
            / spec.repository.split("/", 1)[1]
        )
        source_tip = _resolve_required(repo, _remote_ref(spec.source_branch))
        targets: dict[str, str] = {}
        for branch in spec.target_branches:
            target_tip = _resolve_required(repo, _remote_ref(branch))
            targets[branch] = target_tip
            for record in inventory_release_commits(repo, source_tip, target_tip):
                cases.append(
                    _classify_inventory_record(
                        repo,
                        spec,
                        source_tip,
                        branch,
                        record,
                        worktree_path=worktree,
                    )
                )
        snapshots[spec.repository] = Snapshot(
            source_branch=spec.source_branch,
            source_tip=source_tip,
            targets=targets,
        )
    return CorpusManifest(
        schema_version=1,
        snapshots=snapshots,
        cases=tuple(cases),
    )


def audit_manifest_inventory(
    manifest: CorpusManifest, data_root: str | Path
) -> ManifestAudit:
    """Compare pinned Git inventory with the exact set of manifest transitions."""

    expected: dict[tuple[str, str, str], tuple[str, str]] = {}
    unreadable_snapshots = 0
    for repository, snapshot in manifest.snapshots.items():
        repo = _mirror_path(data_root, repository)
        for branch, target_tip in snapshot.targets.items():
            try:
                records = inventory_release_commits(
                    repo, snapshot.source_tip, target_tip
                )
            except (OSError, subprocess.CalledProcessError, ValueError):
                unreadable_snapshots += 1
                continue
            for record in records:
                expected[(repository, branch, record.after)] = (
                    record.before,
                    record.after_tree,
                )
    actual = {
        (case.repository, case.target_branch, case.target_after): (
            case.target_before,
            case.target_after_tree,
        )
        for case in manifest.cases
    }
    unresolved = sum(
        case.classification is ReplayClassification.UNRESOLVED
        for case in manifest.cases
    )
    expected_keys = set(expected)
    actual_keys = set(actual)
    endpoint_mismatches = sum(
        expected[key] != actual[key] for key in expected_keys & actual_keys
    )
    evidence_gaps = (
        len(expected_keys - actual_keys)
        + len(actual_keys - expected_keys)
        + endpoint_mismatches
        + unresolved
        + unreadable_snapshots
    )
    strict = sum(
        case.classification is ReplayClassification.STRICT_EXACT
        for case in manifest.cases
    )
    return ManifestAudit(
        total_count=len(manifest.cases),
        strict_count=strict,
        diagnostic_count=len(manifest.cases) - strict - unresolved,
        evidence_gap_count=evidence_gaps,
        exit_code=2 if evidence_gaps else 0,
    )


@dataclass(frozen=True)
class GitCommand:
    args: tuple[str, ...]


def _fetch_refspecs(spec: MirrorSpec, pull_requests: Sequence[int]) -> tuple[str, ...]:
    refs = [
        f"+refs/heads/{spec.source_branch}:refs/remotes/origin/{spec.source_branch}"
    ]
    refs.extend(
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
        for branch in spec.target_branches
    )
    refs.extend(
        f"+refs/pull/{number}/head:refs/pull/{number}/head"
        for number in sorted(set(pull_requests))
    )
    return tuple(refs)


def build_refresh_commands(
    spec: MirrorSpec,
    mirror: str | Path,
    pull_requests: Sequence[int] = (),
    *,
    existing: bool = False,
) -> tuple[GitCommand, ...]:
    """Build commands containing only local setup and explicit read-only fetches."""

    mirror_path = Path(mirror)
    commands: list[GitCommand] = []
    if not existing:
        commands.extend(
            (
                GitCommand(("git", "init", "--bare", str(mirror_path))),
                GitCommand(
                    (
                        "git",
                        "-C",
                        str(mirror_path),
                        "remote",
                        "add",
                        "origin",
                        spec.url,
                    )
                ),
            )
        )
    else:
        commands.append(
            GitCommand(
                (
                    "git",
                    "-C",
                    str(mirror_path),
                    "remote",
                    "set-url",
                    "origin",
                    spec.url,
                )
            )
        )
    commands.extend(
        (
            GitCommand(
                (
                    "git",
                    "-C",
                    str(mirror_path),
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    "disabled://read-only",
                )
            ),
            GitCommand(
                (
                    "git",
                    "-c",
                    "http.version=HTTP/1.1",
                    "-C",
                    str(mirror_path),
                    "fetch",
                    "--no-tags",
                    "origin",
                    *_fetch_refspecs(spec, pull_requests),
                )
            ),
        )
    )
    return tuple(commands)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def refresh_mirror(
    spec: MirrorSpec,
    mirror: str | Path,
    *,
    allow_read_only_network: bool,
    pull_requests: Sequence[int] = (),
    runner: CommandRunner = subprocess.run,
) -> tuple[GitCommand, ...]:
    """Refresh one dedicated mirror after an explicit read-only authority gate."""

    if not allow_read_only_network:
        raise PermissionError("explicit read-only network authority is required")
    mirror_path = Path(mirror)
    expected_name = f"{spec.repository.split('/', 1)[1]}.git"
    if mirror_path.name != expected_name:
        raise ValueError(f"dedicated mirror must be named {expected_name}")
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    existing = mirror_path.exists()
    if existing:
        probe = subprocess.run(
            ["git", "-C", str(mirror_path), "rev-parse", "--is-bare-repository"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=offline_git_environment(),
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            raise ValueError(
                f"existing mirror path is not a bare Git repository: {mirror_path}"
            )
    commands = build_refresh_commands(
        spec, mirror_path, pull_requests, existing=existing
    )
    for command in commands:
        runner(
            list(command.args),
            check=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=offline_git_environment(),
        )
    return commands


def load_manifest(path: str | Path) -> CorpusManifest:
    """Load a manifest while retaining strict schema validation."""

    return CorpusManifest.from_dict(json.loads(Path(path).read_text()))


def load_reviewed_corpus(path: str | Path) -> ReviewedCorpus:
    """Load immutable schema-v2 expectations for offline execution."""

    return ReviewedCorpus.from_dict(json.loads(Path(path).read_text()))


def load_synthetic_coverage(path: str | Path) -> SyntheticCoverageSuite:
    """Load reviewed deterministic test-to-coverage claims."""

    return SyntheticCoverageSuite.from_dict(json.loads(Path(path).read_text()))


def write_replay_reports(report: ReplayReport, report_dir: str | Path) -> None:
    """Write canonical local JSON and Markdown evidence."""

    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "historical-replay.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    (destination / "historical-replay.md").write_text(render_markdown_report(report))
