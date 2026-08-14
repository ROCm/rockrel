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
    destination_protected: bool
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
        train_id=train.id,
        destination_branch=repository.destination_branch if repository else None,
        evidence={
            "repository": facts.repository,
            "base_branch": facts.base_branch,
            "label_actor_permission": facts.label_actor_permission,
            "jira_fix_versions": sorted(facts.jira_fix_versions),
            "evidence_errors": list(facts.evidence_errors),
        },
    )


def qualify_request(train: TrainConfig, facts: QualificationFacts) -> Result:
    """Apply deterministic request policy without performing any I/O."""

    if facts.evidence_errors:
        return _result(
            Status.BLOCKED,
            "evidence_unavailable",
            "Required evidence is temporarily unavailable; the label is retained.",
            train,
            facts,
        )
    if train.state != "active":
        return _result(
            Status.INVALID,
            "inactive_train",
            f"Train {train.id} is inactive.",
            train,
            facts,
        )
    repository = train.repositories.get(facts.repository)
    if repository is None:
        return _result(
            Status.INVALID,
            "repository_not_configured",
            f"{facts.repository} is not configured for train {train.id}.",
            train,
            facts,
        )
    if facts.base_branch != repository.source_branch:
        return _result(
            Status.INVALID,
            "source_branch_mismatch",
            f"Source base must be {repository.source_branch}.",
            train,
            facts,
        )
    if facts.label_actor_permission not in AUTHORIZED_PERMISSIONS:
        return _result(
            Status.INVALID,
            "label_actor_not_authorized",
            "The label actor must have write, maintain, or admin permission.",
            train,
            facts,
        )
    jira_fix_version = train.requirements.jira_fix_version
    if jira_fix_version is not None and jira_fix_version not in facts.jira_fix_versions:
        return _result(
            Status.INVALID,
            "jira_fix_version_mismatch",
            f"No referenced ROCm Jira issue has Fix Version {jira_fix_version}.",
            train,
            facts,
        )
    if not facts.destination_exists:
        return _result(
            Status.INVALID,
            "destination_branch_missing",
            f"Destination branch {repository.destination_branch} does not exist.",
            train,
            facts,
        )
    if not facts.destination_protected:
        return _result(
            Status.INVALID,
            "destination_branch_not_protected",
            f"Destination branch {repository.destination_branch} is not protected by PR rules.",
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
            Status.WAITING_FOR_MERGE,
            "source_not_merged",
            "The request is valid and will run after the source PR merges.",
            train,
            facts,
        )
    return _result(
        Status.CHERRY_PICK_REQUIRED,
        "qualified_for_planning",
        "The request qualifies for repository planning.",
        train,
        facts,
    )
