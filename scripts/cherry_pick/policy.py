# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Pure qualification policy for label-triggered cherry-pick requests."""

from __future__ import annotations

from dataclasses import dataclass

from .config import TrainConfig
from .models import Result, Status


AUTHORIZED_PERMISSIONS = frozenset({"write", "maintain", "admin"})


@dataclass(frozen=True)
class QualificationFacts:
    source_pr: str
    repository: str
    base_branch: str
    merged: bool
    closed: bool
    label_actor_permission: str
    jira_fix_versions: frozenset[str]
    destination_exists: bool
    destination_pr_required: bool
    unresolved_dependencies: tuple[str, ...] = ()
    evidence_errors: tuple[str, ...] = ()


def _result(
    status: Status,
    reason_code: str,
    message: str,
    train: TrainConfig,
    facts: QualificationFacts,
) -> Result:
    repository = train.repositories.get(facts.repository)
    return Result(
        status=status,
        reason_code=reason_code,
        message=message,
        source_pr=facts.source_pr,
        source_repository=facts.repository,
        train_id=train.id,
        destination_branch=repository.destination_branch if repository else None,
        evidence={
            "repository": facts.repository,
            "base_branch": facts.base_branch,
            "label_actor_permission": facts.label_actor_permission,
            "jira_fix_versions": sorted(facts.jira_fix_versions),
            "unresolved_dependencies": list(facts.unresolved_dependencies),
            "evidence_errors": list(facts.evidence_errors),
        },
    )


def qualify_request(train: TrainConfig, facts: QualificationFacts) -> Result:
    """Apply deterministic request policy without performing I/O."""

    if train.mode == "disabled":
        return _result(
            Status.CANCELLED,
            "train_disabled",
            f"Train {train.id} is disabled and performs no work.",
            train,
            facts,
        )
    if facts.evidence_errors:
        return _result(
            Status.BLOCKED_EVIDENCE,
            "evidence_unavailable",
            "Required evidence is unavailable; no destination write is allowed.",
            train,
            facts,
        )
    if train.state != "active":
        return _result(
            Status.INELIGIBLE_SOURCE,
            "inactive_train",
            f"Train {train.id} is inactive.",
            train,
            facts,
        )
    repository = train.repositories.get(facts.repository)
    if repository is None:
        return _result(
            Status.INELIGIBLE_SOURCE,
            "repository_not_configured",
            f"{facts.repository} is not configured for train {train.id}.",
            train,
            facts,
        )
    if facts.base_branch not in repository.source_branches:
        return _result(
            Status.INELIGIBLE_SOURCE,
            "source_branch_mismatch",
            f"Source base must be one of {', '.join(repository.source_branches)}.",
            train,
            facts,
        )
    if facts.label_actor_permission not in AUTHORIZED_PERMISSIONS:
        return _result(
            Status.BLOCKED_POLICY,
            "label_actor_not_authorized",
            "The label actor must have write, maintain, or admin permission.",
            train,
            facts,
        )
    jira_fix_version = train.requirements.jira_fix_version
    if jira_fix_version is not None and jira_fix_version not in facts.jira_fix_versions:
        return _result(
            Status.INELIGIBLE_SOURCE,
            "jira_fix_version_mismatch",
            f"No referenced ROCm Jira issue has Fix Version {jira_fix_version}.",
            train,
            facts,
        )
    if not facts.destination_exists:
        return _result(
            Status.BLOCKED_POLICY,
            "destination_branch_missing",
            f"Destination branch {repository.destination_branch} does not exist.",
            train,
            facts,
        )
    if not facts.destination_pr_required:
        return _result(
            Status.BLOCKED_POLICY,
            "destination_pull_request_rule_missing",
            f"Destination branch {repository.destination_branch} lacks an effective PR rule.",
            train,
            facts,
        )
    if train.requirements.block_on_dependencies and facts.unresolved_dependencies:
        return _result(
            Status.BLOCKED_DEPENDENCY,
            "unresolved_dependencies",
            "Declared dependencies or ordering requirements need operator review.",
            train,
            facts,
        )
    if not facts.merged:
        if facts.closed:
            return _result(
                Status.CANCELLED,
                "source_closed_without_merge",
                "The source pull request closed without merging.",
                train,
                facts,
            )
        return _result(
            Status.AWAITING_MERGE,
            "source_not_merged",
            "The request is valid and will be reevaluated after merge.",
            train,
            facts,
        )
    return _result(
        Status.DRAFT_PLANNED,
        "qualified_for_planning",
        "The request qualifies for repository planning.",
        train,
        facts,
    )
