# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Read-only orchestration for label-driven cherry-pick requests."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from .clients import (
    GitHubClient,
    JiraClient,
    extract_dependency_trailers,
    extract_jira_keys,
    parse_pull_request_url,
)
from .config import TrainCatalog
from .coverage import find_covering_pull
from .git import (
    Changeset,
    ChangesetError,
    evaluate_changeset,
    prove_changeset,
)
from .models import Result, Status
from .policy import QualificationFacts, qualify_request


def automation_branch(train_id: str, source_number: int) -> str:
    return f"shared/cherry-pick/{train_id}/{source_number}"


def identity_marker(repository: str, source_number: int, train_id: str) -> str:
    return f"<!-- cherry-pick:{repository}#{source_number}:{train_id} -->"


def status_marker(train_id: str) -> str:
    return f"<!-- cherry-pick-status:{train_id} -->"


def discover_train_ids(
    catalog: TrainCatalog,
    *,
    current_labels: tuple[str, ...],
    event_action: str,
    event_label: str,
) -> tuple[str, ...]:
    """Resolve event labels centrally without trusting them as configuration."""

    labels = set(current_labels)
    if event_action == "unlabeled" and event_label:
        labels.add(event_label)
    train_ids = {
        train.id
        for train in catalog.trains.values()
        if train.label in labels
        and train.state == "active"
        and train.mode in {"shadow", "create-draft"}
    }
    return tuple(sorted(train_ids))


def render_pull_body(
    *,
    marker: str,
    source_url: str,
    source_repository: str,
    source_sha: str,
    source_head: str,
    train_id: str,
    destination_branch: str,
    destination_head: str,
    changeset_kind: str,
    ordered_commits: tuple[str, ...],
    mainline: int | None,
    jira_keys: tuple[str, ...],
    jira_fix_versions: tuple[str, ...],
    unresolved_dependencies: tuple[str, ...],
    proof_method: str,
    source_body: str,
) -> str:
    """Render an operator-grade ROCm draft description."""

    commits = "\n".join(f"  - `{commit}`" for commit in ordered_commits)
    jira = ", ".join(f"`{key}`" for key in jira_keys) or "_None_"
    versions = ", ".join(f"`{item}`" for item in jira_fix_versions) or "_None_"
    dependencies = (
        "\n".join(f"  - {item}" for item in unresolved_dependencies)
        if unresolved_dependencies
        else "  - None declared"
    )
    command = "git cherry-pick -x"
    if mainline is not None:
        command += f" -m {mainline}"
    command += " " + " ".join(ordered_commits)
    original = source_body.strip() or "_No source description was provided._"
    return f"""{marker}
# Operator review required

This pull request was generated as a **draft**. The automation never marks this PR ready or merges it.

## Source and destination

- Source: {source_url}
- Source repository: `{source_repository}`
- Source head: `{source_head}`
- Merged commit/range head: [`{source_sha}`]({source_url}/commits/{source_sha})
- Train: `{train_id}`
- Destination: `{destination_branch}` at `{destination_head}`

## Application and provenance

- Representation: `{changeset_kind}`
- Proof: `{proof_method}`
- Ordered application commits:
{commits}
- Planned command: `{command}`
- Git provenance: every generated commit uses `-x`.

## Jira and dependencies

- Jira issues: {jira}
- Fix Versions: {versions}
- Dependencies/order:
{dependencies}

## Test plan and result

- Local preflight: the complete proven changeset applied cleanly and was non-empty.
- Review the complete destination diff.
- Run and evaluate this repository's native required checks; no CI success is claimed here.

## Submission checklist

- [ ] Source representation and provenance verified
- [ ] Jira and destination verified
- [ ] Dependencies/order reviewed
- [ ] Destination diff reviewed
- [ ] Native CI reviewed

## Original source pull request

{original}
"""


def render_status_comment(result: Result) -> str:
    lines = [
        "## Cherry-pick status",
        "",
        f"- Train: `{result.train_id}`",
        f"- Status: `{result.status.value}`",
        f"- Reason: `{result.reason_code}`",
        f"- Destination: `{result.destination_branch}`",
    ]
    if result.pull_request_url:
        lines.append(f"- Pull request: {result.pull_request_url}")
    lines.extend(["", result.message])
    return "\n".join(lines)


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _nested_string(value: object, key: str) -> str:
    return _string(value.get(key)) if isinstance(value, dict) else ""


class Planner:
    """Gather canonical external facts and produce one deterministic plan."""

    def __init__(
        self,
        config: TrainCatalog,
        github: GitHubClient,
        jira: JiraClient | None,
        *,
        changeset_builder: Callable[
            [Path, str, str, tuple[str, ...]], Changeset
        ] = prove_changeset,
        evaluator: Callable[[Path, Changeset, str], Result] = evaluate_changeset,
        coverage_evaluator: Callable[
            [Path, GitHubClient, str, dict[str, object]], dict[str, object] | None
        ] = find_covering_pull,
    ) -> None:
        self.config = config
        self.github = github
        self.jira = jira
        self.changeset_builder = changeset_builder
        self.evaluator = evaluator
        self.coverage_evaluator = coverage_evaluator

    @staticmethod
    def _result(
        *,
        status: Status,
        reason_code: str,
        message: str,
        source_url: str,
        repository: str,
        train_id: str,
        destination_branch: str | None,
        evidence: dict[str, object] | None = None,
        pull_request_url: str | None = None,
    ) -> Result:
        return Result(
            status=status,
            reason_code=reason_code,
            message=message,
            evidence=evidence or {},
            source_pr=source_url,
            source_repository=repository,
            train_id=train_id,
            destination_branch=destination_branch,
            pull_request_url=pull_request_url,
        )

    @staticmethod
    def _with_context(
        result: Result,
        *,
        source_url: str,
        repository: str,
        train_id: str,
        destination_branch: str | None,
        evidence: dict[str, object] | None = None,
        pull_request_url: str | None = None,
    ) -> Result:
        combined = dict(result.evidence)
        combined.update(evidence or {})
        return Result(
            status=result.status,
            reason_code=result.reason_code,
            message=result.message,
            evidence=combined,
            source_pr=source_url,
            source_repository=repository,
            train_id=train_id,
            destination_branch=destination_branch,
            pull_request_url=pull_request_url or result.pull_request_url,
        )

    def plan(
        self,
        source_url: str,
        train_id: str,
        repo_dir: str | Path,
        *,
        event_action: str | None = None,
    ) -> Result:
        owner, repo, number = parse_pull_request_url(source_url)
        repository = f"{owner}/{repo}"
        train = self.config.train(train_id)
        repository_config = train.repositories.get(repository)
        destination_branch = (
            repository_config.destination_branch if repository_config else None
        )
        early_evidence = {
            "source_number": number,
            "train_mode": train.mode,
            "event_action": event_action,
        }
        if train.mode == "disabled":
            return self._result(
                status=Status.CANCELLED,
                reason_code="train_disabled",
                message="The train is disabled and performs no work.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=early_evidence,
            )
        if train.mode == "validate" and event_action not in (None, "manual"):
            return self._result(
                status=Status.CANCELLED,
                reason_code="validate_mode_manual_only",
                message="Validate mode accepts manual read-only planning only.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=early_evidence,
            )
        if train.state != "active":
            return self._result(
                status=Status.INELIGIBLE_SOURCE,
                reason_code="inactive_train",
                message="The train is inactive.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=early_evidence,
            )

        pull = self.github.pull(owner, repo, number)
        labels = pull.get("labels")
        current_labels = (
            {item.get("name") for item in labels if isinstance(item, dict)}
            if isinstance(labels, list)
            else set()
        )
        if event_action == "unlabeled" and train.label not in current_labels:
            return self._result(
                status=Status.CANCELLED,
                reason_code="train_label_removed",
                message="The cherry-pick request label was removed.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=early_evidence,
            )
        if train.label not in current_labels:
            return self._result(
                status=Status.INELIGIBLE_SOURCE,
                reason_code="train_label_missing",
                message=f"The source PR does not currently have label {train.label}.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=early_evidence,
            )

        title = _string(pull.get("title"))
        body = _string(pull.get("body"))
        combined_text = f"{title}\n{body}"
        jira_keys = extract_jira_keys(combined_text)
        unresolved = list(extract_dependency_trailers(combined_text))
        errors: list[str] = []
        label_actor: str | None = None
        permission = "none"
        branch = None
        destination_policy = None
        fix_versions: set[str] = set()
        try:
            label_actor = self.github.label_actor(owner, repo, number, train.label)
            if label_actor is not None:
                permission = self.github.permission(owner, repo, label_actor)
        except Exception as exc:  # Canonical evidence is fail-closed.
            errors.append(f"github_label_evidence:{type(exc).__name__}")
        if repository_config is not None:
            try:
                branch = self.github.branch(
                    owner, repo, repository_config.destination_branch
                )
                if branch.exists:
                    destination_policy = self.github.destination_policy(
                        owner, repo, repository_config.destination_branch
                    )
            except Exception as exc:
                errors.append(f"github_destination_evidence:{type(exc).__name__}")

        jira_required = train.requirements.jira_fix_version is not None
        if jira_required and self.jira is None:
            errors.append("jira_evidence:client_unavailable")
        elif jira_required:
            for key in jira_keys:
                try:
                    assert self.jira is not None
                    issue = self.jira.issue_evidence(key)
                    fix_versions.update(issue.fix_versions)
                    unresolved.extend(issue.dependencies)
                    unresolved.extend(issue.ordering_notes)
                except Exception as exc:
                    errors.append(f"jira_evidence:{key}:{type(exc).__name__}")
        unresolved_dependencies = tuple(dict.fromkeys(unresolved))

        facts = QualificationFacts(
            source_pr=source_url,
            repository=repository,
            base_branch=_nested_string(pull.get("base"), "ref"),
            merged=pull.get("merged") is True,
            closed=pull.get("state") == "closed",
            label_actor_permission=permission,
            jira_fix_versions=frozenset(fix_versions),
            destination_exists=branch is not None and branch.exists,
            destination_pr_required=(
                destination_policy is not None
                and destination_policy.pull_request_required
            ),
            unresolved_dependencies=unresolved_dependencies,
            evidence_errors=tuple(errors),
        )
        qualified = qualify_request(train, facts)
        destination_head = branch.sha if branch is not None else None
        rule_ids = (
            list(destination_policy.rule_ids) if destination_policy is not None else []
        )
        base_evidence: dict[str, object] = {
            "source_title": title,
            "source_body": body,
            "source_number": number,
            "source_repository": repository,
            "source_head": _nested_string(pull.get("head"), "sha"),
            "source_merge_commit": pull.get("merge_commit_sha"),
            "label_actor": label_actor,
            "jira_keys": jira_keys,
            "jira_fix_versions": sorted(fix_versions),
            "unresolved_dependencies": list(unresolved_dependencies),
            "destination_head": destination_head,
            "destination_rule_ids": rule_ids,
            "train_mode": train.mode,
            "event_action": event_action,
        }
        qualified = self._with_context(
            qualified,
            source_url=source_url,
            repository=repository,
            train_id=train_id,
            destination_branch=destination_branch,
            evidence=base_evidence,
        )
        if qualified.status is not Status.DRAFT_PLANNED:
            return qualified

        merged_sha = pull.get("merge_commit_sha")
        source_head = _nested_string(pull.get("head"), "sha")
        if not isinstance(merged_sha, str) or not source_head:
            return self._result(
                status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
                reason_code="source_merge_evidence_missing",
                message="The merged source PR lacks canonical merge/head evidence.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=base_evidence,
            )

        marker = identity_marker(repository, number, train_id)
        branch_name = automation_branch(train_id, number)
        try:
            destination_pulls = self.github.pulls(
                owner, repo, base=repository_config.destination_branch, state="all"
            )
        except Exception as exc:
            return self._result(
                status=Status.BLOCKED_EVIDENCE,
                reason_code="existing_pr_evidence_unavailable",
                message="GitHub could not enumerate destination pull requests.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence={**base_evidence, "error": type(exc).__name__},
            )
        for candidate in destination_pulls:
            candidate_head = candidate.get("head")
            candidate_ref = (
                candidate_head.get("ref") if isinstance(candidate_head, dict) else None
            )
            owns_identity = candidate.get("state") == "open" or bool(
                candidate.get("merged_at")
            )
            if owns_identity and (
                marker in _string(candidate.get("body")) or candidate_ref == branch_name
            ):
                return self._result(
                    status=Status.COVERED_BY_EXISTING_PR,
                    reason_code="existing_identity_match",
                    message="An existing destination PR owns this request identity.",
                    source_url=source_url,
                    repository=repository,
                    train_id=train_id,
                    destination_branch=destination_branch,
                    evidence=base_evidence,
                    pull_request_url=_string(candidate.get("html_url")) or None,
                )

        try:
            source_commits = self.github.pull_commits(owner, repo, number)
            changeset = self.changeset_builder(
                Path(repo_dir), merged_sha, source_head, source_commits
            )
        except ChangesetError as exc:
            return self._result(
                status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
                reason_code="changeset_proof_failed",
                message=str(exc),
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=base_evidence,
            )
        except Exception as exc:
            return self._result(
                status=Status.BLOCKED_EVIDENCE,
                reason_code="changeset_evidence_unavailable",
                message="Required changeset evidence is unavailable.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence={**base_evidence, "error": type(exc).__name__},
            )

        if len(changeset.commits) == 1:
            for candidate in destination_pulls:
                try:
                    coverage = self.coverage_evaluator(
                        Path(repo_dir), self.github, changeset.commits[0], candidate
                    )
                except Exception as exc:
                    return self._result(
                        status=Status.BLOCKED_EVIDENCE,
                        reason_code="covering_pr_evidence_unavailable",
                        message="Coverage evaluation for a destination PR failed.",
                        source_url=source_url,
                        repository=repository,
                        train_id=train_id,
                        destination_branch=destination_branch,
                        evidence={**base_evidence, "error": type(exc).__name__},
                    )
                if coverage is not None:
                    return self._result(
                        status=Status.COVERED_BY_EXISTING_PR,
                        reason_code=str(coverage["reason"]),
                        message="An existing destination PR positively covers this change.",
                        source_url=source_url,
                        repository=repository,
                        train_id=train_id,
                        destination_branch=destination_branch,
                        evidence={**base_evidence, "coverage": coverage},
                        pull_request_url=str(coverage["pull_request_url"]),
                    )

        if not isinstance(destination_head, str):
            return self._result(
                status=Status.BLOCKED_EVIDENCE,
                reason_code="destination_head_missing",
                message="The destination branch did not provide a canonical SHA.",
                source_url=source_url,
                repository=repository,
                train_id=train_id,
                destination_branch=destination_branch,
                evidence=base_evidence,
            )
        git_result = self.evaluator(Path(repo_dir), changeset, destination_head)
        changeset_evidence = {
            "changeset_kind": changeset.kind.value,
            "ordered_commits": list(changeset.commits),
            "aggregate_base": changeset.aggregate_base,
            "aggregate_head": changeset.aggregate_head,
            "mainline": changeset.mainline,
            "proof_method": changeset.proof.method,
            "changeset_proof": changeset.proof.as_dict(),
        }
        return self._with_context(
            git_result,
            source_url=source_url,
            repository=repository,
            train_id=train_id,
            destination_branch=destination_branch,
            evidence={**base_evidence, **changeset_evidence},
        )
