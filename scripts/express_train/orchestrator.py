"""Orchestrate one source pull request and one Express Train."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .clients import GitHubClient, JiraClient, extract_jira_keys, parse_pull_request_url
from .config import ExpressTrainConfig
from .coverage import find_covering_pull
from .git import evaluate_cherry_pick
from .models import Result, Status
from .policy import QualificationFacts, qualify_request


def automation_branch(train_id: str, source_number: int) -> str:
    return f"shared/cherry-pick/{train_id}/{source_number}"


def identity_marker(repository: str, source_number: int, train_id: str) -> str:
    return f"<!-- express-train:{repository}#{source_number}:{train_id} -->"


def status_marker(train_id: str) -> str:
    return f"<!-- express-train-status:{train_id} -->"


def render_pull_body(
    *,
    marker: str,
    source_url: str,
    source_sha: str,
    train_id: str,
    source_body: str,
) -> str:
    return (
        f"{marker}\n"
        f"Cherry-picks [`{source_sha}`]({source_url}/commits/{source_sha}) from "
        f"{source_url} for Express Train `{train_id}`.\n\n"
        "This pull request was created as a draft and remains a draft until an "
        "operator completes target-branch review. The automation never marks it "
        "ready or merges it.\n\n"
        "## Source pull request\n\n"
        f"{source_body.strip() or '_No source description was provided._'}\n"
    )


def render_status_comment(result: Result) -> str:
    lines = [
        "## Express Train cherry-pick status",
        "",
        f"- Train: `{result.train_id}`",
        f"- Status: `{result.status.value}`",
        f"- Reason: `{result.reason_code}`",
        f"- Target: `{result.target_branch}`",
    ]
    if result.pull_request_url:
        lines.append(f"- Pull request: {result.pull_request_url}")
    lines.extend(["", result.message])
    return "\n".join(lines)


class Planner:
    """Gather external facts and produce one deterministic request plan."""

    def __init__(
        self,
        config: ExpressTrainConfig,
        github: GitHubClient,
        jira: JiraClient,
        *,
        evaluator: Callable[[Path, str, str], Result] = evaluate_cherry_pick,
        coverage_evaluator: Callable[
            [Path, GitHubClient, str, dict[str, Any]], dict[str, Any] | None
        ] = find_covering_pull,
    ) -> None:
        self.config = config
        self.github = github
        self.jira = jira
        self.evaluator = evaluator
        self.coverage_evaluator = coverage_evaluator

    @staticmethod
    def _with_context(
        result: Result,
        *,
        source_url: str,
        train_id: str,
        target_branch: str | None,
        evidence: dict[str, Any] | None = None,
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
            train_id=train_id,
            target_branch=target_branch,
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
        target_branch = (
            repository_config.target_branch if repository_config else None
        )
        pull = self.github.pull(owner, repo, number)
        current_labels = {
            item.get("name")
            for item in pull.get("labels", [])
            if isinstance(item, dict)
        }
        if event_action == "unlabeled" and train.label not in current_labels:
            return Result(
                status=Status.CANCELLED,
                reason_code="train_label_removed",
                message="The Express Train request label was removed.",
                source_pr=source_url,
                train_id=train_id,
                target_branch=target_branch,
                evidence={
                    "source_number": number,
                    "source_repository": repository,
                    "event_action": event_action,
                    "train_mode": train.mode,
                },
            )
        if train.label not in current_labels:
            return Result(
                status=Status.INVALID,
                reason_code="train_label_missing",
                message=f"The source PR does not currently have label {train.label}.",
                source_pr=source_url,
                train_id=train_id,
                target_branch=target_branch,
                evidence={
                    "source_number": number,
                    "source_repository": repository,
                    "event_action": event_action,
                    "train_mode": train.mode,
                },
            )

        errors: list[str] = []
        label_actor: str | None = None
        permission = "none"
        branch = {"exists": False, "protected": False, "sha": None}
        jira_keys = extract_jira_keys(
            f"{pull.get('title') or ''}\n{pull.get('body') or ''}"
        )
        fix_versions: set[str] = set()
        try:
            label_actor = self.github.label_actor(
                owner, repo, number, train.label
            )
            if label_actor is not None:
                permission = self.github.permission(owner, repo, label_actor)
        except Exception as exc:  # external evidence is fail-closed
            errors.append(f"github_label_evidence:{type(exc).__name__}")
        if label_actor is None and not errors:
            permission = "none"
        if repository_config is not None:
            try:
                branch = self.github.branch(
                    owner, repo, repository_config.target_branch
                )
            except Exception as exc:
                errors.append(f"github_target_evidence:{type(exc).__name__}")
        if not jira_keys:
            fix_versions = set()
        else:
            for key in jira_keys:
                try:
                    fix_versions.update(self.jira.fix_versions(key))
                except Exception as exc:
                    errors.append(f"jira_evidence:{key}:{type(exc).__name__}")

        facts = QualificationFacts(
            source_pr=source_url,
            repository=repository,
            base_branch=pull.get("base", {}).get("ref") or "",
            merged=pull.get("merged") is True,
            closed=pull.get("state") == "closed",
            label_actor_permission=permission,
            jira_fix_versions=frozenset(fix_versions),
            target_exists=branch["exists"] is True,
            target_protected=branch["protected"] is True,
            evidence_errors=tuple(errors),
        )
        qualified = qualify_request(train, facts)
        base_evidence = {
            "source_title": pull.get("title") or "",
            "source_body": pull.get("body") or "",
            "source_number": number,
            "source_repository": repository,
            "source_merge_commit": pull.get("merge_commit_sha"),
            "label_actor": label_actor,
            "jira_keys": jira_keys,
            "target_head": branch.get("sha"),
            "train_mode": train.mode,
            "event_action": event_action,
        }
        qualified = self._with_context(
            qualified,
            source_url=source_url,
            train_id=train_id,
            target_branch=target_branch,
            evidence=base_evidence,
        )
        if qualified.status is not Status.CHERRY_PICK_REQUIRED:
            return qualified
        if not isinstance(pull.get("merge_commit_sha"), str):
            return Result(
                status=Status.BLOCKED,
                reason_code="merge_commit_missing",
                message="The merged source PR has no aggregate merge commit SHA.",
                evidence=base_evidence,
                source_pr=source_url,
                train_id=train_id,
                target_branch=target_branch,
            )

        marker = identity_marker(repository, number, train_id)
        branch_name = automation_branch(train_id, number)
        try:
            target_pulls = self.github.pulls(
                owner, repo, base=repository_config.target_branch, state="all"
            )
        except Exception as exc:
            return Result(
                status=Status.BLOCKED,
                reason_code="existing_pr_evidence_unavailable",
                message="GitHub could not enumerate existing target pull requests.",
                evidence={**base_evidence, "error": type(exc).__name__},
                source_pr=source_url,
                train_id=train_id,
                target_branch=target_branch,
            )
        for candidate in target_pulls:
            owns_identity = candidate.get("state") == "open" or bool(
                candidate.get("merged_at")
            )
            if owns_identity and (
                marker in (candidate.get("body") or "")
                or candidate.get("head", {}).get("ref") == branch_name
            ):
                return Result(
                    status=Status.COVERED_BY_EXISTING_PR,
                    reason_code="existing_identity_match",
                    message="An existing target pull request owns this source/train identity.",
                    evidence=base_evidence,
                    source_pr=source_url,
                    train_id=train_id,
                    target_branch=target_branch,
                    pull_request_url=candidate.get("html_url"),
                )
            try:
                coverage = self.coverage_evaluator(
                    Path(repo_dir),
                    self.github,
                    pull["merge_commit_sha"],
                    candidate,
                )
            except Exception as exc:
                return Result(
                    status=Status.BLOCKED,
                    reason_code="covering_pr_evidence_unavailable",
                    message="Coverage evaluation for an existing target PR failed.",
                    evidence={
                        **base_evidence,
                        "candidate_pull_request": candidate.get("html_url"),
                        "error": type(exc).__name__,
                    },
                    source_pr=source_url,
                    train_id=train_id,
                    target_branch=target_branch,
                )
            if coverage is not None:
                return Result(
                    status=Status.COVERED_BY_EXISTING_PR,
                    reason_code=str(coverage["reason"]),
                    message="An existing target pull request positively covers this change.",
                    evidence={**base_evidence, "coverage": coverage},
                    source_pr=source_url,
                    train_id=train_id,
                    target_branch=target_branch,
                    pull_request_url=str(coverage["pull_request_url"]),
                )

        git_result = self.evaluator(
            Path(repo_dir), pull["merge_commit_sha"], branch["sha"]
        )
        return self._with_context(
            git_result,
            source_url=source_url,
            train_id=train_id,
            target_branch=target_branch,
            evidence=base_evidence,
        )
