# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""GitHub control-plane planning around the network-free Git core."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .authorization import (
    AuthorizationEnvelope,
    AuthorizationError,
    authorized_plan_fingerprint,
    authorize_label,
    latest_label_transition,
)
from .github_read import GitHubReadClient, parse_pull_request_url
from .config import PrerequisiteOverride, TrainCatalog, TrainConfig
from .core import CommitNode, CorePlanner, CoreRequest, ManifestError
from .dependencies import (
    DependencyGraph,
    DependencyError,
    build_dependency_graph,
    parse_dependency_trailers,
    parse_dependency_url,
)
from .git import cherry_pick_command
from .managed_stack import ManagedFrontierError, build_frontier_results
from .models import Result, Status
from .refs import (
    RefHydrationError,
    hydrate_commit_ref,
    hydrate_pull_head_ref,
    hydrate_pull_refs,
)


def automation_branch(train_id: str, source_number: int) -> str:
    """Return the deterministic automation branch for one train request."""
    return f"shared/cherry-pick/{train_id}/{source_number}"


def identity_marker(
    repository: str,
    source_number: int,
    train_id: str,
    plan_fingerprint: str,
) -> str:
    """Render an immutable draft identity marker bound to a reviewed plan."""
    if len(plan_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in plan_fingerprint
    ):
        raise ValueError("plan fingerprint must be a lowercase SHA-256 digest")
    return (
        f"<!-- cherry-pick:v2:{repository}#{source_number}:{train_id}:"
        f"{plan_fingerprint} -->"
    )


def status_marker(train_id: str) -> str:
    """Render the stable status-comment marker for one configured train."""
    return f"<!-- cherry-pick-status:{train_id} -->"


def discover_train_ids(
    catalog: TrainCatalog,
    *,
    current_labels: tuple[str, ...],
    event_action: str,
    event_label: str,
) -> tuple[str, ...]:
    """Discover active train requests while retaining an unlabeled event trigger."""
    labels = set(current_labels)
    if event_action == "unlabeled" and event_label:
        labels.add(event_label)
    return tuple(
        sorted(
            train.id
            for train in catalog.trains.values()
            if train.label in labels
            and train.state == "active"
            and train.mode in {"shadow", "create-draft"}
        )
    )


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
    dependencies: tuple[str, ...],
    dependency_status: str,
    proof_method: str,
    expected_tree: str,
    source_body: str,
) -> str:
    """Render the complete operator-review contract for a generated draft.

    The body records immutable source, destination, proof, and command evidence;
    it deliberately makes no semantic-readiness or CI-success claim.
    """
    commits = "\n".join(f"  - `{commit}`" for commit in ordered_commits)
    dependency_lines = (
        "\n".join(f"  - {item}" for item in dependencies)
        if dependencies
        else "  - None declared"
    )
    commands = tuple(
        shlex.join(
            (
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                *cherry_pick_command(commit, mainline, commit_result=True),
            )
        )
        for commit in ordered_commits
    )
    command_block = "\n".join(commands)
    original = source_body.strip() or "_No source description was provided._"
    return f"""{marker}
# Operator review required

This pull request was generated as a **draft**. The automation never marks this PR ready or merges it.

## Source and destination

- Source: {source_url}
- Source identity: `{source_repository}@{source_sha}`
- Source repository: `{source_repository}`
- Source head: `{source_head}`
- Merged commit/range head: `{source_sha}`
- Train: `{train_id}`
- Destination: `{destination_branch}` at `{destination_head}`
- Expected tree: `{expected_tree}`

## Application and provenance

- Representation: `{changeset_kind}`
- Proof: `{proof_method}`
- Ordered application commits:
{commits}
- Git provenance: every generated commit uses `-x`.

### Commands executed to create the cherry-pick

```console
{command_block}
```

## Dependencies

- Status: `{dependency_status}`
{dependency_lines}

## Test plan and result

- Local preflight: the complete proven changeset applied cleanly and was non-empty.
- Review the complete destination diff and expected tree.
- Run this repository's native required checks; no CI success is claimed here.

## Submission checklist

- [ ] Source representation and provenance verified
- [ ] Label authorization and destination verified
- [ ] Dependency graph reviewed
- [ ] Destination diff reviewed
- [ ] Native CI reviewed

## Original source pull request

{original}
"""


def render_status_comment(result: Result) -> str:
    """Render a stable human-readable summary of one planning result."""
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


def _value(value: object, key: str) -> object:
    """Read one key from an object-like GitHub response without coercion."""
    return value.get(key) if isinstance(value, dict) else None


def _text(value: object, key: str) -> str:
    """Read one string field from an object-like GitHub response."""
    item = _value(value, key)
    return item if isinstance(item, str) else ""


def _body_digest(body: str) -> str:
    """Hash exact source-body text for immutable authorization binding."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _graph_digest(
    records: dict[str, dict[str, object]],
    adjacency: dict[str, tuple[str, ...]],
) -> str:
    """Hash canonical pull identities and dependency edges for authorization."""
    values = [
        {
            "url": url,
            "head_sha": _text(record.get("head"), "sha"),
            "body_sha256": _body_digest(_text(record, "body")),
        }
        for url, record in sorted(records.items())
    ]
    payload = json.dumps(
        {
            "pull_requests": values,
            "edges": [
                {"from": source, "to": target}
                for source, targets in sorted(adjacency.items())
                for target in sorted(targets)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def coverage_snapshot_sha256(candidates: list[dict[str, object]]) -> str:
    """Hash a normalized, complete open-PR coverage snapshot."""

    payload = json.dumps(
        sorted(
            candidates, key=lambda item: (item.get("number", 0), item.get("url", ""))
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_coverage_pulls(
    pulls: list[dict[str, object]],
    *,
    repository: str,
    destination_branch: str,
) -> list[dict[str, object]]:
    """Normalize same-repository open PRs for the offline core and writer guard."""

    candidates: list[dict[str, object]] = []
    for pull in pulls:
        head = pull.get("head")
        base = pull.get("base")
        head_repo = _value(_value(head, "repo"), "full_name")
        if head_repo != repository:
            continue
        number = pull.get("number")
        url = pull.get("html_url")
        state = pull.get("state")
        draft = pull.get("draft")
        base_branch = _text(base, "ref")
        base_sha = _text(base, "sha")
        head_sha = _text(head, "sha")
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or number < 1
            or not isinstance(url, str)
            or state != "open"
            or not isinstance(draft, bool)
            or base_branch != destination_branch
            or re.fullmatch(r"[0-9a-f]{40}", base_sha) is None
            or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None
        ):
            raise DependencyError(
                "coverage_candidate_malformed",
                "An open destination pull request has malformed identity evidence.",
            )
        try:
            owner, name, parsed_number = parse_pull_request_url(url)
        except ValueError as exc:
            raise DependencyError(
                "coverage_candidate_malformed",
                "An open destination pull request has a noncanonical URL.",
            ) from exc
        if f"{owner}/{name}" != repository or parsed_number != number:
            raise DependencyError(
                "coverage_candidate_malformed",
                "An open destination pull request URL does not match its identity.",
            )
        candidates.append(
            {
                "url": url,
                "repository": repository,
                "number": number,
                "state": "open",
                "draft": draft,
                "base_branch": base_branch,
                "base_sha": base_sha,
                "head_repository": repository,
                "head_sha": head_sha,
            }
        )
    candidates.sort(key=lambda item: (int(item["number"]), str(item["url"])))
    return candidates


@dataclass(frozen=True)
class _PlanContext:
    """Carry stable request identity and pre-I/O result evidence between phases."""

    source_url: str
    owner: str
    repository_name: str
    source_number: int
    repository: str
    train_id: str
    train: TrainConfig
    repository_is_configured: bool
    destination_branch: str | None
    event_action: str | None
    early_evidence: dict[str, object]


@dataclass(frozen=True)
class _ControlPlaneFacts:
    """Carry one authorized and dependency-complete GitHub evidence snapshot."""

    source_pull: dict[str, object]
    records: dict[str, dict[str, object]]
    graph: DependencyGraph
    authorization: dict[str, object]
    envelope: AuthorizationEnvelope | None
    matching_overrides: tuple[PrerequisiteOverride, ...]
    trailer_edges: tuple[tuple[str, str], ...]


class Planner:
    """Acquire canonical GitHub facts, authorize them, and invoke the core."""

    def __init__(
        self,
        config: TrainCatalog,
        github: GitHubReadClient,
        *,
        core_planner: CorePlanner | None = None,
        ref_hydrator=hydrate_pull_refs,
        commit_hydrator=hydrate_commit_ref,
        coverage_hydrator=hydrate_pull_head_ref,
        config_revision: str = "0" * 40,
        execution_context: str = "github-app",
        control_plane_snapshot: Mapping[str, object] | None = None,
    ) -> None:
        """Configure canonical evidence clients and network-free planning adapters.

        A control-plane snapshot is accepted only for the explicitly read-only
        local-materialization context and is defensively JSON-copied.
        """
        if execution_context not in {
            "github-app",
            "local-gh",
            "local-materialize",
        }:
            raise ValueError(
                "execution_context must be github-app, local-gh, or local-materialize"
            )
        self.config = config
        self.github = github
        self.core_planner = core_planner or CorePlanner()
        self.ref_hydrator = ref_hydrator
        self.commit_hydrator = commit_hydrator
        self.coverage_hydrator = coverage_hydrator
        self.config_revision = config_revision
        self.execution_context = execution_context
        if (
            control_plane_snapshot is not None
            and execution_context != "local-materialize"
        ):
            raise ValueError(
                "control_plane_snapshot is supported only for local-materialize"
            )
        try:
            normalized_snapshot = (
                json.loads(
                    json.dumps(
                        control_plane_snapshot,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                )
                if control_plane_snapshot is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "control_plane_snapshot must be JSON serializable"
            ) from exc
        if normalized_snapshot is not None and (
            not isinstance(normalized_snapshot, dict) or not normalized_snapshot
        ):
            raise ValueError("control_plane_snapshot must be a non-empty object")
        self.control_plane_snapshot = normalized_snapshot

    def _hydrate_request(
        self,
        request: CoreRequest,
        repository_paths: dict[str, Path],
    ) -> None:
        """Hydrate every immutable source, destination, and coverage Git ref.

        Nodes are hydrated in core request order, followed by coverage candidates,
        so dependency-injected hydrators observe a stable call sequence.
        """
        for node in (*request.prerequisites, request.source):
            repository_path = repository_paths.get(node.repository)
            if repository_path is None:
                raise RefHydrationError(
                    "local_repository_missing",
                    f"No local repository was supplied for {node.repository}.",
                )
            if isinstance(node, CommitNode):
                self.commit_hydrator(
                    Path(repository_path),
                    remote="origin",
                    commit_sha=node.commit_sha,
                    destination_branch=node.destination.branch,
                    destination_sha=node.destination.head_sha,
                )
            else:
                self.ref_hydrator(
                    Path(repository_path),
                    remote="origin",
                    pull_number=node.number,
                    source_branch=node.base_branch,
                    merge_sha=node.merge_sha,
                    head_sha=node.head_sha,
                    ordered_commits=node.ordered_commits,
                    destination_branch=node.destination.branch,
                    destination_sha=node.destination.head_sha,
                )
        for candidate in request.coverage_candidates:
            repository_path = repository_paths.get(candidate.repository)
            if repository_path is None:
                raise RefHydrationError(
                    "local_repository_missing",
                    f"No local repository was supplied for {candidate.repository}.",
                )
            self.coverage_hydrator(
                Path(repository_path),
                remote="origin",
                pull_number=candidate.number,
                head_sha=candidate.head_sha,
            )

    @staticmethod
    def _result(
        status: Status,
        reason_code: str,
        message: str,
        *,
        source_url: str,
        repository: str,
        train_id: str,
        destination_branch: str | None,
        evidence: dict[str, object] | None = None,
    ) -> Result:
        """Build a result carrying the stable source and train identity."""
        return Result(
            status=status,
            reason_code=reason_code,
            message=message,
            evidence=evidence or {},
            source_pr=source_url,
            source_repository=repository,
            train_id=train_id,
            destination_branch=destination_branch,
        )

    def _pull_graph(
        self,
        source_url: str,
        source_pull: dict[str, object],
        *,
        override_edges: tuple[tuple[str, str], ...],
        max_nodes: int,
        max_depth: int,
    ) -> tuple[
        dict[str, dict[str, object]],
        dict[str, tuple[str, ...]],
        tuple[tuple[str, str], ...],
    ]:
        """Resolve canonical trailer and reviewed-override dependency evidence.

        The traversal fetches each pull at most once while enforcing depth and
        node limits before fetching an excess dependency.
        """
        records = {source_url: source_pull}
        adjacency: dict[str, tuple[str, ...]] = {}
        override_adjacency: dict[str, set[str]] = {}
        for edge_source, edge_target in override_edges:
            override_adjacency.setdefault(edge_source, set()).add(edge_target)
        visiting: set[str] = set()
        discovered: set[str] = set()
        trailer_edges: set[tuple[str, str]] = set()

        def visit(url: str, depth: int) -> None:
            """Visit one dependency URL while preserving bounded fetch order."""
            if url in adjacency:
                return
            visiting.add(url)
            current_ref = parse_dependency_url(url)
            trailer_dependencies = ()
            if current_ref.kind == "pull_request":
                record = records[url]
                trailer_dependencies = parse_dependency_trailers(_text(record, "body"))
                trailer_edges.update((url, item.url) for item in trailer_dependencies)
            override_targets = override_adjacency.get(url, set())
            if current_ref.kind == "commit" and override_targets:
                raise DependencyError(
                    "commit_prerequisite_not_leaf",
                    f"commit prerequisite must be a leaf: {url}",
                )
            target_urls = {
                *(item.url for item in trailer_dependencies),
                *override_targets,
            }
            adjacency[url] = tuple(sorted(target_urls))
            for target_url in sorted(target_urls):
                ref = parse_dependency_url(target_url)
                next_depth = depth + 1
                if next_depth > max_depth:
                    raise DependencyError(
                        "dependency_depth_limit",
                        f"dependency graph exceeds maximum depth {max_depth}",
                    )
                if ref.url != source_url and ref.url not in discovered:
                    if len(discovered) >= max_nodes:
                        raise DependencyError(
                            "dependency_node_limit",
                            "dependency graph exceeds maximum node count "
                            f"{max_nodes}",
                        )
                    discovered.add(ref.url)
                if ref.kind == "pull_request" and ref.url not in records:
                    assert ref.number is not None
                    owner, repo = ref.repository.split("/", 1)
                    pulled = self.github.pull(owner, repo, ref.number)
                    canonical = _text(pulled, "html_url")
                    if canonical != ref.url:
                        raise DependencyError(
                            "dependency_identity_mismatch",
                            "GitHub dependency identity does not match its URL",
                        )
                    records[ref.url] = pulled
                if ref.url not in visiting:
                    visit(ref.url, next_depth)
            visiting.remove(url)

        visit(source_url, 0)
        return records, adjacency, tuple(sorted(trailer_edges))

    def _change_payload(
        self,
        url: str,
        pull: dict[str, object],
        train_id: str,
    ) -> dict[str, object]:
        """Build one immutable pull-request node from canonical GitHub evidence."""
        ref = parse_dependency_url(url)
        if ref.kind != "pull_request" or ref.number is None:
            raise DependencyError(
                "change_identity_invalid", f"{url} is not a pull request"
            )
        train = self.config.train(train_id)
        repository_config = train.repositories.get(ref.repository)
        if repository_config is None:
            raise DependencyError(
                "dependency_repository_not_in_train",
                f"{ref.repository} is not configured for train {train_id}",
            )
        base = _text(pull.get("base"), "ref")
        if base not in repository_config.source_branches:
            raise DependencyError(
                "dependency_source_branch_ineligible",
                f"{url} is not based on an eligible source branch",
            )
        head = _text(pull.get("head"), "sha")
        merge = _text(pull, "merge_commit_sha")
        if not head or not merge:
            raise DependencyError(
                "dependency_merge_identity_missing",
                f"{url} is missing immutable merge evidence",
            )
        declared_commit_count = pull.get("commits")
        if (
            isinstance(declared_commit_count, bool)
            or not isinstance(declared_commit_count, int)
            or declared_commit_count < 1
        ):
            raise DependencyError(
                "change_commit_count_invalid",
                f"{url} is missing a valid source commit count",
            )
        owner, repo = ref.repository.split("/", 1)
        branch = self.github.branch(owner, repo, repository_config.destination_branch)
        if not branch.exists or not branch.sha:
            raise DependencyError(
                "destination_branch_missing",
                f"destination branch is unavailable for {ref.repository}",
            )
        policy = self.github.destination_policy(
            owner, repo, repository_config.destination_branch
        )
        if not policy.pull_request_required:
            raise DependencyError(
                "destination_pull_request_rule_missing",
                "The destination does not require pull requests.",
            )
        commits = self.github.pull_commits(owner, repo, ref.number)
        if len(commits) != declared_commit_count:
            raise DependencyError(
                "change_commit_list_incomplete",
                f"{url} source commit listing is incomplete",
            )
        return {
            "kind": "pull_request",
            "url": url,
            "repository": ref.repository,
            "number": ref.number,
            "base_branch": base,
            "head_sha": head,
            "merge_sha": merge,
            "ordered_commits": list(commits),
            "body_sha256": _body_digest(_text(pull, "body")),
            "destination": {
                "repository": ref.repository,
                "branch": repository_config.destination_branch,
                "head_sha": branch.sha,
            },
        }

    def _commit_payload(self, url: str, train_id: str) -> dict[str, object]:
        """Build one immutable commit node from reviewed destination evidence."""
        ref = parse_dependency_url(url)
        if ref.kind != "commit" or ref.commit_sha is None:
            raise DependencyError(
                "change_identity_invalid", f"{url} is not a standalone commit"
            )
        train = self.config.train(train_id)
        repository_config = train.repositories.get(ref.repository)
        if repository_config is None:
            raise DependencyError(
                "dependency_repository_not_in_train",
                f"{ref.repository} is not configured for train {train_id}",
            )
        owner, repo = ref.repository.split("/", 1)
        branch = self.github.branch(owner, repo, repository_config.destination_branch)
        if not branch.exists or not branch.sha:
            raise DependencyError(
                "destination_branch_missing",
                f"destination branch is unavailable for {ref.repository}",
            )
        policy = self.github.destination_policy(
            owner, repo, repository_config.destination_branch
        )
        if not policy.pull_request_required:
            raise DependencyError(
                "destination_pull_request_rule_missing",
                "The destination does not require pull requests.",
            )
        return {
            "kind": "commit",
            "url": url,
            "repository": ref.repository,
            "commit_sha": ref.commit_sha,
            "destination": {
                "repository": ref.repository,
                "branch": repository_config.destination_branch,
                "head_sha": branch.sha,
            },
        }

    def _plan_context(
        self,
        source_url: str,
        train_id: str,
        event_action: str | None,
    ) -> _PlanContext:
        """Resolve stable source and train identity before any external I/O."""

        owner, repository_name, source_number = parse_pull_request_url(source_url)
        repository = f"{owner}/{repository_name}"
        train = self.config.train(train_id)
        repository_config = train.repositories.get(repository)
        destination_branch = (
            repository_config.destination_branch if repository_config else None
        )
        return _PlanContext(
            source_url=source_url,
            owner=owner,
            repository_name=repository_name,
            source_number=source_number,
            repository=repository,
            train_id=train_id,
            train=train,
            repository_is_configured=repository_config is not None,
            destination_branch=destination_branch,
            event_action=event_action,
            early_evidence={
                "source_number": source_number,
                "train_mode": train.mode,
                "event_action": event_action,
                "execution_context": self.execution_context,
            },
        )

    def _early_result(self, context: _PlanContext) -> Result | None:
        """Apply configuration-only gates before canonical GitHub evidence is read."""

        if context.train.mode == "disabled":
            return self._result(
                Status.CANCELLED,
                "train_disabled",
                "The train is disabled.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        if context.train.mode == "validate" and context.event_action not in {
            None,
            "manual",
        }:
            return self._result(
                Status.CANCELLED,
                "validate_mode_manual_only",
                "Validate mode accepts manual planning only.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        if context.train.state != "active" or not context.repository_is_configured:
            return self._result(
                Status.INELIGIBLE_SOURCE,
                "inactive_or_unconfigured_train",
                "The train or repository is not active and configured.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        if (
            self.execution_context == "github-app"
            and context.train.mode in {"shadow", "create-draft"}
            and self.config.authorization.executor_app_id is None
        ):
            return self._result(
                Status.BLOCKED_AUTHORIZATION,
                "executor_app_identity_unconfigured",
                "The trusted executor GitHub App identity is not configured.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        return None

    def _authorization_snapshot(
        self,
        context: _PlanContext,
        source_pull: dict[str, object],
        labels: tuple[str, ...],
        dependency_snapshot_sha256: str,
    ) -> tuple[dict[str, object], AuthorizationEnvelope | None]:
        """Authorize the exact source and graph snapshot for this execution context."""

        if self.execution_context == "local-materialize":
            evidence: dict[str, object] = {
                "kind": "local_only_operator_request",
                "source_head_sha": _text(source_pull.get("head"), "sha"),
                "source_body_sha256": _body_digest(_text(source_pull, "body")),
                "dependency_snapshot_sha256": dependency_snapshot_sha256,
                "config_revision": self.config_revision,
            }
            if self.control_plane_snapshot is not None:
                evidence["control_plane_snapshot"] = json.loads(
                    json.dumps(self.control_plane_snapshot)
                )
            return evidence, None

        transitions = self.github.label_transitions(
            context.owner,
            context.repository_name,
            context.source_number,
            context.train.label,
        )
        permissions: dict[int, str] = {}
        latest_transition = latest_label_transition(context.train.label, transitions)
        if (
            context.train.label in labels
            and latest_transition is not None
            and latest_transition.action == "labeled"
            and latest_transition.performed_via_app_id is None
            and not latest_transition.actor_login.casefold().endswith("[bot]")
        ):
            permissions[latest_transition.actor_id] = self.github.permission(
                context.owner,
                context.repository_name,
                latest_transition.actor_login,
            )
        envelope = authorize_label(
            train_id=context.train.id,
            label=context.train.label,
            current_labels=labels,
            transitions=transitions,
            actor_permissions=permissions,
            minimum_human_permission=self.config.authorization.minimum_human_permission,
            trusted_app_ids=self.config.authorization.trusted_app_ids,
            source_head_sha=_text(source_pull.get("head"), "sha"),
            source_body=_text(source_pull, "body"),
            dependency_snapshot_sha256=dependency_snapshot_sha256,
            config_revision=self.config_revision,
        )
        if (
            self.execution_context == "github-app"
            and context.event_action not in {None, "labeled"}
            and context.train.mode in {"shadow", "create-draft"}
        ):
            executor_app_id = self.config.authorization.executor_app_id
            assert executor_app_id is not None and envelope is not None
            trusted_snapshots = self.github.trusted_check_external_ids(
                context.owner,
                context.repository_name,
                head_sha=envelope.source_head_sha,
                name=f"ROCm Cherry-Pick / {context.train.id}",
                executor_app_id=executor_app_id,
            )
            if envelope.check_external_id() not in trusted_snapshots:
                raise AuthorizationError(
                    "authorization_snapshot_missing_or_stale",
                    "The label-time authorization snapshot is missing or stale; relabel the pull request.",
                )
        return envelope.as_dict(), envelope

    def _acquire_control_plane(
        self, context: _PlanContext
    ) -> _ControlPlaneFacts | Result:
        """Acquire the complete dependency graph before performing authorization."""

        source_pull = self.github.pull(
            context.owner,
            context.repository_name,
            context.source_number,
        )
        labels_raw = source_pull.get("labels")
        labels = (
            tuple(
                str(item["name"])
                for item in labels_raw
                if isinstance(labels_raw, list)
                and isinstance(item, dict)
                and isinstance(item.get("name"), str)
            )
            if isinstance(labels_raw, list)
            else ()
        )
        if context.train.label not in labels and context.event_action == "unlabeled":
            return self._result(
                Status.CANCELLED,
                "train_label_removed",
                "The train label was removed.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        matching_overrides = tuple(
            item
            for item in context.train.prerequisite_overrides
            if item.source_pr == context.source_url
        )
        override_edges = tuple(
            edge for item in matching_overrides for edge in item.edges
        )
        records, adjacency, trailer_edges = self._pull_graph(
            context.source_url,
            source_pull,
            override_edges=override_edges,
            max_nodes=self.config.dependency_policy.max_nodes,
            max_depth=self.config.dependency_policy.max_depth,
        )
        graph = build_dependency_graph(
            context.source_url,
            adjacency,
            max_nodes=self.config.dependency_policy.max_nodes,
            max_depth=self.config.dependency_policy.max_depth,
        )
        authorization, envelope = self._authorization_snapshot(
            context,
            source_pull,
            labels,
            _graph_digest(records, adjacency),
        )
        return _ControlPlaneFacts(
            source_pull=source_pull,
            records=records,
            graph=graph,
            authorization=authorization,
            envelope=envelope,
            matching_overrides=matching_overrides,
            trailer_edges=trailer_edges,
        )

    def _control_plane_failure(
        self,
        context: _PlanContext,
        error: (
            AuthorizationError
            | DependencyError
            | RuntimeError
            | ValueError
            | KeyError
            | TypeError
        ),
    ) -> Result:
        """Convert only expected control-plane failures into stable result contracts."""

        if isinstance(error, AuthorizationError):
            return self._result(
                Status.BLOCKED_AUTHORIZATION,
                error.reason_code,
                str(error),
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        if isinstance(error, DependencyError):
            status = (
                Status.BLOCKED_EVIDENCE
                if error.reason_code
                in {
                    "destination_pull_request_rule_missing",
                    "change_commit_count_invalid",
                    "change_commit_list_incomplete",
                    "coverage_candidate_limit",
                    "coverage_candidate_malformed",
                }
                else Status.BLOCKED_DEPENDENCY
            )
            return self._result(
                status,
                error.reason_code,
                str(error),
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence=context.early_evidence,
            )
        return self._result(
            Status.BLOCKED_EVIDENCE,
            "github_evidence_unavailable",
            "Canonical GitHub evidence is unavailable or malformed.",
            source_url=context.source_url,
            repository=context.repository,
            train_id=context.train_id,
            destination_branch=context.destination_branch,
            evidence=context.early_evidence,
        )

    def _merge_gate(
        self,
        context: _PlanContext,
        facts: _ControlPlaneFacts,
    ) -> Result | None:
        """Require the source and every pull prerequisite to be merged."""

        if facts.source_pull.get("merged") is not True:
            return self._result(
                Status.AWAITING_MERGE,
                "source_not_merged",
                "The authorized source pull request is not merged.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence={
                    **context.early_evidence,
                    "authorization": facts.authorization,
                },
            )
        for dependency_url in facts.graph.topological_order:
            dependency_ref = parse_dependency_url(dependency_url)
            if (
                dependency_ref.kind == "pull_request"
                and facts.records[dependency_url].get("merged") is not True
            ):
                return self._result(
                    Status.AWAITING_DEPENDENCIES,
                    "dependency_not_merged",
                    "A declared dependency is not merged.",
                    source_url=context.source_url,
                    repository=context.repository,
                    train_id=context.train_id,
                    destination_branch=context.destination_branch,
                    evidence={
                        **context.early_evidence,
                        "authorization": facts.authorization,
                        "dependency_url": dependency_url,
                    },
                )
        return None

    def _coverage_candidates(
        self, payloads: tuple[dict[str, object], ...]
    ) -> list[dict[str, object]]:
        """Snapshot open pulls once for every distinct exact destination."""

        destinations: set[tuple[str, str]] = set()
        for payload in payloads:
            if payload.get("kind") != "pull_request":
                continue
            repository = payload.get("repository")
            destination = payload.get("destination")
            destination_branch = (
                destination.get("branch") if isinstance(destination, dict) else None
            )
            if not isinstance(repository, str) or not isinstance(
                destination_branch, str
            ):
                raise DependencyError(
                    "coverage_candidate_malformed",
                    "A pull source omitted its exact coverage destination.",
                )
            destinations.add((repository, destination_branch))

        candidates: list[dict[str, object]] = []
        for repository, destination_branch in sorted(destinations):
            owner, repository_name = repository.split("/", 1)
            normalized = normalize_coverage_pulls(
                self.github.pulls(
                    owner,
                    repository_name,
                    base=destination_branch,
                    state="open",
                ),
                repository=repository,
                destination_branch=destination_branch,
            )
            if len(normalized) > self.config.coverage_policy.max_open_pull_requests:
                raise DependencyError(
                    "coverage_candidate_limit",
                    "The open destination pull request snapshot exceeds the configured limit.",
                )
            candidates.extend(normalized)
        return candidates

    def _core_request(
        self,
        context: _PlanContext,
        facts: _ControlPlaneFacts,
    ) -> CoreRequest:
        """Build the exact offline-core manifest from authorized GitHub evidence."""

        source_payload = self._change_payload(
            context.source_url,
            facts.source_pull,
            context.train_id,
        )
        prerequisite_payloads: list[dict[str, object]] = []
        for url in facts.graph.topological_order:
            ref = parse_dependency_url(url)
            prerequisite_payloads.append(
                self._change_payload(url, facts.records[url], context.train_id)
                if ref.kind == "pull_request"
                else self._commit_payload(url, context.train_id)
            )
        return CoreRequest.from_dict(
            {
                "schema_version": 3,
                "train_id": context.train_id,
                "dependency_mode": context.train.dependency_mode,
                "source": source_payload,
                "prerequisites": prerequisite_payloads,
                "prerequisite_edges": [
                    {"from": source, "to": target}
                    for source, target in facts.graph.edges
                ],
                "coverage_candidates": self._coverage_candidates(
                    (source_payload, *prerequisite_payloads)
                ),
            }
        )

    def _manifest_failure(
        self,
        context: _PlanContext,
        authorization: dict[str, object],
        error: DependencyError | ManifestError | RuntimeError | ValueError | TypeError,
    ) -> Result:
        """Convert expected manifest failures with authorization evidence retained."""

        if isinstance(error, DependencyError):
            status = (
                Status.BLOCKED_EVIDENCE
                if error.reason_code.startswith("destination_")
                or error.reason_code
                in {
                    "change_commit_count_invalid",
                    "change_commit_list_incomplete",
                    "coverage_candidate_limit",
                    "coverage_candidate_malformed",
                }
                else Status.BLOCKED_DEPENDENCY
            )
            return self._result(
                status,
                error.reason_code,
                str(error),
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=context.destination_branch,
                evidence={
                    **context.early_evidence,
                    "authorization": authorization,
                },
            )
        return self._result(
            Status.BLOCKED_EVIDENCE,
            "request_manifest_unavailable",
            "The immutable Git request could not be established.",
            source_url=context.source_url,
            repository=context.repository,
            train_id=context.train_id,
            destination_branch=context.destination_branch,
            evidence={
                **context.early_evidence,
                "authorization": authorization,
            },
        )

    def _hydrate_or_result(
        self,
        context: _PlanContext,
        request: CoreRequest,
        repository_paths: dict[str, Path],
        authorization: dict[str, object],
    ) -> Result | None:
        """Hydrate the immutable manifest or return a sanitized evidence failure."""

        try:
            self._hydrate_request(request, repository_paths)
        except RefHydrationError as error:
            return self._result(
                Status.BLOCKED_EVIDENCE,
                error.reason_code,
                str(error),
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=request.source.destination.branch,
                evidence={
                    **context.early_evidence,
                    "authorization": authorization,
                    "plan_fingerprint": request.fingerprint(),
                },
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            return self._result(
                Status.BLOCKED_EVIDENCE,
                "ref_hydration_unavailable",
                "Exact Git refs could not be hydrated.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=request.source.destination.branch,
                evidence={
                    **context.early_evidence,
                    "authorization": authorization,
                    "plan_fingerprint": request.fingerprint(),
                },
            )
        return None

    def _invoke_core(
        self,
        request: CoreRequest,
        repository_paths: dict[str, Path],
        scratch_root: str | Path | None,
    ) -> Result:
        """Invoke the core with its legacy two-argument call when no scratch exists."""

        if scratch_root is None:
            return self.core_planner.plan(request, repository_paths)
        return self.core_planner.plan(
            request,
            repository_paths,
            scratch_root=Path(scratch_root),
        )

    @staticmethod
    def _bound_plan_fingerprint(
        core_request_fingerprint: str,
        authorization: dict[str, object],
        envelope: AuthorizationEnvelope | None,
    ) -> str:
        """Bind the core request to local evidence or the label-time envelope."""

        if envelope is not None:
            return authorized_plan_fingerprint(core_request_fingerprint, envelope)
        return hashlib.sha256(
            json.dumps(
                {
                    "core_request_sha256": core_request_fingerprint,
                    "local_authorization": authorization,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def _result_evidence(
        self,
        context: _PlanContext,
        facts: _ControlPlaneFacts,
        request: CoreRequest,
        core_result: Result,
    ) -> tuple[dict[str, object], str]:
        """Bind core evidence to authorization, source metadata, and the manifest."""

        core_request_fingerprint = request.fingerprint()
        evidence = dict(core_result.evidence)
        # Control-plane identities are authoritative; overwrite any same-named
        # values returned across the dependency-injected core boundary.
        evidence.update(
            {
                "authorization": facts.authorization,
                "execution_context": self.execution_context,
                "train_mode": context.train.mode,
                "source_kind": "pull_request",
                "source_number": context.source_number,
                "source_title": _text(facts.source_pull, "title"),
                "source_body": _text(facts.source_pull, "body"),
                "source_head": request.source.head_sha,
                "source_merge_commit": request.source.merge_sha,
                "prerequisites": [item.url for item in request.prerequisites],
                # Retained in the draft renderer contract until its wording is
                # migrated; values are the same typed prerequisite URLs.
                "dependencies": [item.url for item in request.prerequisites],
                "dependency_status": "contained",
                "prerequisite_sources": {
                    "trailers": [
                        {"from": source, "to": target}
                        for source, target in facts.trailer_edges
                    ],
                    "reviewed_overrides": [
                        {
                            "rationale": item.rationale,
                            "edges": [
                                {"from": source, "to": target}
                                for source, target in item.edges
                            ],
                        }
                        for item in facts.matching_overrides
                    ],
                },
                "coverage_snapshot_sha256": request.coverage_snapshot_sha256_for(
                    request.source
                ),
                "coverage_manifest_sha256": request.coverage_snapshot_sha256(),
                "core_request_fingerprint": core_request_fingerprint,
                "plan_fingerprint": self._bound_plan_fingerprint(
                    core_request_fingerprint,
                    facts.authorization,
                    facts.envelope,
                ),
                "request_manifest": request.as_dict(),
            }
        )
        return evidence, core_request_fingerprint

    def _bind_managed_frontier(
        self,
        context: _PlanContext,
        facts: _ControlPlaneFacts,
        request: CoreRequest,
        core_result: Result,
        evidence: dict[str, object],
        core_request_fingerprint: str,
    ) -> Result | None:
        """Bind a managed frontier to the root plan or fail closed."""

        if not (
            core_result.status is Status.AWAITING_DEPENDENCIES
            and core_result.reason_code == "managed_dependency_frontier"
        ):
            return None
        try:
            managed_results = build_frontier_results(
                request=request,
                core_result=core_result,
                records=facts.records,
                authorization=facts.authorization,
                execution_context=self.execution_context,
                train_mode=context.train.mode,
                root_plan_fingerprint=str(evidence["plan_fingerprint"]),
                core_request_fingerprint=core_request_fingerprint,
            )
        except (ManagedFrontierError, KeyError, TypeError, ValueError):
            return self._result(
                Status.BLOCKED_EVIDENCE,
                "managed_frontier_evidence_invalid",
                "The managed dependency frontier could not be bound safely.",
                source_url=context.source_url,
                repository=context.repository,
                train_id=context.train_id,
                destination_branch=request.source.destination.branch,
                evidence={
                    key: value
                    for key, value in evidence.items()
                    if key != "managed_frontier_results"
                },
            )
        evidence["managed_frontier_results"] = [
            item.as_dict() for item in managed_results
        ]
        return None

    @staticmethod
    def _final_result(
        context: _PlanContext,
        request: CoreRequest,
        core_result: Result,
        evidence: dict[str, object],
    ) -> Result:
        """Restore control-plane identity around the network-free core result."""

        return Result(
            status=core_result.status,
            reason_code=core_result.reason_code,
            message=core_result.message,
            evidence=evidence,
            source_pr=context.source_url,
            source_repository=context.repository,
            train_id=context.train_id,
            destination_branch=request.source.destination.branch,
            pull_request_url=core_result.pull_request_url,
        )

    def plan(
        self,
        source_url: str,
        train_id: str,
        repo_dirs: dict[str, Path] | str | Path,
        *,
        event_action: str | None = None,
        scratch_root: str | Path | None = None,
    ) -> Result:
        """Plan one authorized request through immutable Git evidence.

        Configuration-only gates run before GitHub I/O. The authorized control-plane
        snapshot is then converted to an offline manifest, hydrated in stable order,
        and bound to the core result before any caller may request a write.
        """

        context = self._plan_context(source_url, train_id, event_action)
        early_result = self._early_result(context)
        if early_result is not None:
            return early_result

        try:
            acquired = self._acquire_control_plane(context)
        except (
            AuthorizationError,
            DependencyError,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            return self._control_plane_failure(context, error)
        if isinstance(acquired, Result):
            return acquired

        merge_result = self._merge_gate(context, acquired)
        if merge_result is not None:
            return merge_result
        try:
            request = self._core_request(context, acquired)
        except (
            DependencyError,
            ManifestError,
            RuntimeError,
            ValueError,
            TypeError,
        ) as error:
            return self._manifest_failure(context, acquired.authorization, error)

        repository_paths = (
            repo_dirs
            if isinstance(repo_dirs, dict)
            else {context.repository: Path(repo_dirs)}
        )
        hydration_result = self._hydrate_or_result(
            context,
            request,
            repository_paths,
            acquired.authorization,
        )
        if hydration_result is not None:
            return hydration_result

        core_result = self._invoke_core(request, repository_paths, scratch_root)
        evidence, core_request_fingerprint = self._result_evidence(
            context,
            acquired,
            request,
            core_result,
        )
        frontier_result = self._bind_managed_frontier(
            context,
            acquired,
            request,
            core_result,
            evidence,
            core_request_fingerprint,
        )
        if frontier_result is not None:
            return frontier_result
        return self._final_result(context, request, core_result, evidence)
