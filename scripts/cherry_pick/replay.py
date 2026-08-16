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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .git import ChangesetError, evaluate_changeset, prove_changeset
from .models import Status

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA_IN_TEXT_PATTERN = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", re.I)
TITLE_PR_PATTERN = re.compile(r"\(#(\d+)\)")
PR_URL_PATTERN = re.compile(
    r"https://github\.com/ROCm/[A-Za-z0-9_.-]+/pull/(\d+)", re.I
)
EXPLICIT_PR_PATTERN = re.compile(
    r"(?i)\b(?:cherry[ -]?pick(?:ed)?|original|source)\s+(?:PR\s*)?#(\d+)"
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


def extract_provenance(subject: str, body: str, *, repository: str) -> Provenance:
    """Extract only explicit source PR or full-SHA evidence from a target commit."""

    if repository not in SUPPORTED_REPOSITORIES:
        raise ValueError(f"repository {repository!r} is not supported")
    if subject.lstrip().lower().startswith("revert "):
        return Provenance((), (), "none")

    commits = tuple(
        str(value).lower()
        for value in _ordered_unique(SHA_IN_TEXT_PATTERN.findall(body))
    )
    body_prs: list[int] = [int(value) for value in PR_URL_PATTERN.findall(body)]
    body_prs.extend(int(value) for value in EXPLICIT_PR_PATTERN.findall(body))

    in_pr_list = False
    for line in body.splitlines():
        stripped = line.strip()
        if re.match(r"(?i)^cherry[ -]?picked PRs\s*:", stripped):
            in_pr_list = True
            continue
        if in_pr_list:
            match = re.match(r"^[*+-]?\s*#(\d+)\b", stripped)
            if match:
                body_prs.append(int(match.group(1)))
                continue
            if stripped and not stripped.startswith(("*", "+", "-", "#")):
                in_pr_list = False

    prs = tuple(int(value) for value in _ordered_unique(body_prs))
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
        }


def _outcome(
    case: HistoricalReplayCase,
    disposition: ReplayDisposition,
    root_cause: str,
    *,
    engine_status: str | None = None,
    engine_reason: str | None = None,
    destination_tree: str | None = None,
    planned_tree: str | None = None,
) -> ReplayOutcome:
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
    )


def run_replay_case(repo: str | Path, case: HistoricalReplayCase) -> ReplayOutcome:
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
    result = evaluate_changeset(repo_path, changeset, case.target_before)
    destination_tree = result.evidence.get("destination_tree")
    planned_tree = result.evidence.get("planned_tree")
    destination_value = destination_tree if isinstance(destination_tree, str) else None
    planned_value = planned_tree if isinstance(planned_tree, str) else None
    if not strict:
        return _outcome(
            case,
            ReplayDisposition.DIAGNOSTIC,
            f"diagnostic_{result.reason_code}",
            engine_status=result.status.value,
            engine_reason=result.reason_code,
            destination_tree=destination_value,
            planned_tree=planned_value,
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
        )
    return _outcome(
        case,
        ReplayDisposition.PASSED,
        "exact_historical_tree",
        engine_status=result.status.value,
        engine_reason=result.reason_code,
        destination_tree=destination_value,
        planned_tree=planned_value,
    )


@dataclass(frozen=True)
class ReplayReport:
    outcomes: tuple[ReplayOutcome, ...]
    counts: dict[str, int]
    exit_code: int

    @classmethod
    def from_outcomes(cls, outcomes: Sequence[ReplayOutcome]) -> "ReplayReport":
        values = tuple(outcomes)
        counts = Counter(outcome.disposition.value for outcome in values)
        if counts[ReplayDisposition.EVIDENCE_GAP.value]:
            exit_code = 2
        elif counts[ReplayDisposition.STRICT_FAILURE.value]:
            exit_code = 1
        else:
            exit_code = 0
        return cls(values, dict(sorted(counts.items())), exit_code)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "exit_code": self.exit_code,
            "counts": dict(self.counts),
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
    lines = [
        "# Historical cherry-pick replay",
        "",
        f"- Strict eligible: {strict_passed}/{strict_total} passed",
        f"- Evidence gaps: {evidence_gaps}",
        f"- Exit code: {report.exit_code}",
        "",
        "| Case | Repository | Branch | Classification | Result | Root cause |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for outcome in report.outcomes:
        lines.append(
            "| "
            + " | ".join(
                (
                    outcome.case_id,
                    outcome.repository,
                    outcome.target_branch,
                    outcome.classification.value,
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
            if not re.fullmatch(r"release/therock-[A-Za-z0-9._-]+", branch):
                raise ValueError(f"target branch {branch!r} is not supported")

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
) -> HistoricalReplayCase:
    provenance = extract_provenance(
        record.subject, record.body, repository=spec.repository
    )
    source_merge: str | None = None
    source_head: str | None = None
    source_commits: tuple[str, ...] = ()
    classification = ReplayClassification.UNRESOLVED
    notes = "Source provenance requires review."

    if record.subject.lstrip().lower().startswith("revert "):
        classification = ReplayClassification.REVERT
        notes = "Release transition is an explicit revert, not a strict backport."
    elif _has_gitlink_delta(repo, record.before, record.after):
        classification = ReplayClassification.GITLINK_ROLLUP
        notes = "Transition changes a gitlink and is retained as a diagnostic rollup."
    elif len(provenance.source_prs) > 1 or len(provenance.source_commits) > 1:
        classification = ReplayClassification.MULTI_SOURCE_BUNDLE
        notes = "Transition explicitly combines multiple source changes."
    elif len(provenance.source_prs) == 1:
        inputs = _source_inputs_for_pr(repo, source_tip, provenance.source_prs[0])
        if inputs is not None:
            source_merge, source_head, source_commits = inputs
    elif len(provenance.source_commits) == 1:
        inputs = _source_inputs_for_commit(repo, provenance.source_commits[0])
        if inputs is not None:
            source_merge, source_head, source_commits = inputs
            classification = ReplayClassification.HISTORICAL_ADAPTATION
            notes = "Explicit commit lacks one canonical source PR; diagnostic only."
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
        len(provenance.source_prs) != 1
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
    outcome = run_replay_case(repo, strict_candidate)
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
        source_tip = _resolve_required(repo, _remote_ref(spec.source_branch))
        targets: dict[str, str] = {}
        for branch in spec.target_branches:
            target_tip = _resolve_required(repo, _remote_ref(branch))
            targets[branch] = target_tip
            for record in inventory_release_commits(repo, source_tip, target_tip):
                cases.append(
                    _classify_inventory_record(repo, spec, source_tip, branch, record)
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


def write_replay_reports(report: ReplayReport, report_dir: str | Path) -> None:
    """Write canonical local JSON and Markdown evidence."""

    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "historical-replay.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    (destination / "historical-replay.md").write_text(render_markdown_report(report))
