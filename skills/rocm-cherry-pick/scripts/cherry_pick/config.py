# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Strict version-controlled configuration for cherry-pick trains."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_REPOSITORIES = frozenset(
    {"ROCm/TheRock", "ROCm/rocm-systems", "ROCm/rocm-libraries"}
)
VALID_STATES = frozenset({"active", "inactive"})
VALID_MODES = frozenset({"disabled", "validate", "shadow", "create-draft"})
VALID_DEPENDENCY_MODES = frozenset({"gate", "managed_stack"})
TRAIN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
LABEL_PREFIX = "cherry-pick:"
PR_URL_RE = re.compile(
    r"https://github\.com/(ROCm)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)\Z"
)
COMMIT_URL_RE = re.compile(
    r"https://github\.com/(ROCm)/([A-Za-z0-9_.-]+)/commit/([0-9a-f]{40})\Z"
)


class ConfigError(ValueError):
    """Configuration is unsafe or malformed."""


@dataclass(frozen=True)
class RepositoryConfig:
    """Represent repository config in the config contract."""

    source_branches: tuple[str, ...]
    destination_branch: str


@dataclass(frozen=True)
class AuthorizationPolicy:
    """Represent authorization policy in the config contract."""

    minimum_human_permission: str = "write"
    trusted_app_ids: tuple[int, ...] = ()
    executor_app_id: int | None = None


@dataclass(frozen=True)
class DependencyPolicy:
    """Represent dependency policy in the config contract."""

    max_nodes: int = 64
    max_depth: int = 16


@dataclass(frozen=True)
class CoveragePolicy:
    """Represent coverage policy in the config contract."""

    max_open_pull_requests: int = 128


@dataclass(frozen=True)
class PrerequisiteOverride:
    """Represent prerequisite override in the config contract."""

    source_pr: str
    rationale: str
    edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TrainConfig:
    """Represent train config in the config contract."""

    id: str
    label: str
    state: str
    mode: str
    repositories: dict[str, RepositoryConfig]
    dependency_mode: str = "gate"
    prerequisite_overrides: tuple[PrerequisiteOverride, ...] = ()

    def prerequisite_edges_for(self, source_pr: str) -> tuple[tuple[str, str], ...]:
        """Return configured prerequisite edges for one source pull request."""

        return tuple(
            edge
            for override in self.prerequisite_overrides
            if override.source_pr == source_pr
            for edge in override.edges
        )


@dataclass(frozen=True)
class TrainCatalog:
    """Represent train catalog in the config contract."""

    trains: dict[str, TrainConfig]
    authorization: AuthorizationPolicy = field(default_factory=AuthorizationPolicy)
    dependency_policy: DependencyPolicy = field(default_factory=DependencyPolicy)
    coverage_policy: CoveragePolicy = field(default_factory=CoveragePolicy)

    def train(self, train_id: str) -> TrainConfig:
        """Return one configured train by its stable identifier."""

        try:
            return self.trains[train_id]
        except KeyError as exc:
            raise ConfigError(f"unknown train id: {train_id}") from exc

    def train_for_label(self, label: str) -> TrainConfig:
        """Resolve one configured train from its exact label."""

        train_id = parse_train_label(label)
        if train_id is None:
            raise ConfigError(f"invalid train label: {label}")
        train = self.train(train_id)
        if train.label != label:
            raise ConfigError(f"unknown train label: {label}")
        return train


def _required_string(value: dict[str, object], key: str, context: str) -> str:
    """Require a nonempty string field from configuration."""

    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return item


def valid_branch_name(branch: str) -> bool:
    """Return whether a candidate is a safe Git branch name."""

    if branch == "@" or branch.startswith("refs/"):
        return False
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        check=False,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _parse_authorization(raw: object) -> AuthorizationPolicy:
    """Validate write-level human and trusted-App authorization settings."""

    context = "authorization"
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    unsupported = set(raw) - {
        "minimum_human_permission",
        "trusted_app_ids",
        "executor_app_id",
    }
    if unsupported:
        raise ConfigError(f"unsupported authorization field: {sorted(unsupported)[0]}")
    minimum = _required_string(raw, "minimum_human_permission", context)
    if minimum != "write":
        raise ConfigError("authorization.minimum_human_permission must be write")
    app_ids = raw.get("trusted_app_ids")
    if not isinstance(app_ids, list):
        raise ConfigError("authorization.trusted_app_ids must be an array")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1
        for item in app_ids
    ):
        raise ConfigError(
            "authorization.trusted_app_ids must contain positive integers"
        )
    if len(set(app_ids)) != len(app_ids):
        raise ConfigError("authorization.trusted_app_ids must not contain duplicates")
    if "executor_app_id" not in raw:
        raise ConfigError("authorization.executor_app_id is required")
    executor_app_id = raw.get("executor_app_id")
    if executor_app_id is not None and (
        isinstance(executor_app_id, bool)
        or not isinstance(executor_app_id, int)
        or executor_app_id < 1
    ):
        raise ConfigError(
            "authorization.executor_app_id must be null or a positive integer"
        )
    return AuthorizationPolicy(minimum, tuple(app_ids), executor_app_id)


def _bounded_int(
    raw: dict[str, object],
    key: str,
    minimum: int,
    maximum: int,
    *,
    context: str = "dependency_policy",
) -> int:
    """Validate and return an integer within reviewed bounds."""

    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{context}.{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{context}.{key} must be between {minimum} and {maximum}")
    return value


def _parse_dependency_policy(raw: object) -> DependencyPolicy:
    """Validate reviewed node and depth limits for dependency traversal."""

    if not isinstance(raw, dict):
        raise ConfigError("dependency_policy must be an object")
    unsupported = set(raw) - {"max_nodes", "max_depth"}
    if unsupported:
        raise ConfigError(
            f"unsupported dependency policy field: {sorted(unsupported)[0]}"
        )
    return DependencyPolicy(
        max_nodes=_bounded_int(raw, "max_nodes", 1, 64),
        max_depth=_bounded_int(raw, "max_depth", 1, 16),
    )


def _parse_coverage_policy(raw: object) -> CoveragePolicy:
    """Validate the bounded open-pull snapshot limit used by coverage checks."""

    if not isinstance(raw, dict):
        raise ConfigError("coverage_policy must be an object")
    unsupported = set(raw) - {"max_open_pull_requests"}
    if unsupported:
        raise ConfigError(
            f"unsupported coverage policy field: {sorted(unsupported)[0]}"
        )
    return CoveragePolicy(
        max_open_pull_requests=_bounded_int(
            raw,
            "max_open_pull_requests",
            1,
            128,
            context="coverage_policy",
        )
    )


def _canonical_prerequisite_url(value: object, context: str) -> tuple[str, str]:
    """Normalize one prerequisite pull request URL into canonical form."""

    if not isinstance(value, str):
        raise ConfigError(
            f"{context} must be a canonical ROCm pull request or full commit URL"
        )
    match = PR_URL_RE.fullmatch(value)
    kind = "pull_request"
    if match is None:
        match = COMMIT_URL_RE.fullmatch(value)
        kind = "commit"
    if match is None:
        raise ConfigError(
            f"{context} must be a canonical ROCm pull request or full commit URL"
        )
    repository = f"{match.group(1)}/{match.group(2)}"
    if repository not in SUPPORTED_REPOSITORIES:
        raise ConfigError(
            f"{context} must be a canonical ROCm pull request or full commit URL"
        )
    return kind, repository


def _parse_prerequisite_overrides(
    raw: object,
    context: str,
    repositories: dict[str, RepositoryConfig],
) -> tuple[PrerequisiteOverride, ...]:
    """Validate reviewed prerequisite overrides as unique, reachable acyclic graphs."""

    if not isinstance(raw, list):
        raise ConfigError(f"{context}.prerequisite_overrides must be an array")
    parsed: list[PrerequisiteOverride] = []
    sources: set[str] = set()
    for index, value in enumerate(raw):
        item_context = f"{context}.prerequisite_overrides[{index}]"
        if not isinstance(value, dict):
            raise ConfigError(f"{item_context} must be an object")
        unsupported = set(value) - {"source_pr", "rationale", "edges"}
        if unsupported:
            raise ConfigError(
                f"unsupported prerequisite override field: {sorted(unsupported)[0]}"
            )
        source = value.get("source_pr")
        try:
            source_kind, source_repository = _canonical_prerequisite_url(
                source, f"{item_context}.source_pr"
            )
        except ConfigError as exc:
            raise ConfigError(
                f"{item_context}.source_pr must be a canonical ROCm pull request URL"
            ) from exc
        if source_kind != "pull_request":
            raise ConfigError(
                f"{item_context}.source_pr must be a canonical ROCm pull request URL"
            )
        assert isinstance(source, str)
        if source_repository not in repositories:
            raise ConfigError(
                f"{item_context}.source_pr repository is not configured for the train"
            )
        if source in sources:
            raise ConfigError(f"duplicate prerequisite override source_pr: {source}")
        sources.add(source)
        rationale = _required_string(value, "rationale", item_context)
        edges_raw = value.get("edges")
        if not isinstance(edges_raw, list) or not edges_raw:
            raise ConfigError(f"{item_context}.edges must be a non-empty array")
        edges: list[tuple[str, str]] = []
        seen_edges: set[tuple[str, str]] = set()
        adjacency: dict[str, set[str]] = {}
        for edge_index, edge_raw in enumerate(edges_raw):
            edge_context = f"{item_context}.edges[{edge_index}]"
            if not isinstance(edge_raw, dict):
                raise ConfigError(f"{edge_context} must be an object")
            if set(edge_raw) != {"from", "to"}:
                raise ConfigError(f"{edge_context} must contain exactly from and to")
            edge_source = edge_raw.get("from")
            edge_target = edge_raw.get("to")
            source_ref_kind, source_ref_repository = _canonical_prerequisite_url(
                edge_source, f"{edge_context}.from"
            )
            target_ref_kind, target_ref_repository = _canonical_prerequisite_url(
                edge_target, f"{edge_context}.to"
            )
            assert isinstance(edge_source, str) and isinstance(edge_target, str)
            if source_ref_kind == "commit":
                raise ConfigError("commit prerequisite must be a leaf")
            if (
                source_ref_repository not in repositories
                or target_ref_repository not in repositories
            ):
                raise ConfigError(
                    f"{edge_context} references a repository not configured for the train"
                )
            edge = (edge_source, edge_target)
            if edge in seen_edges:
                raise ConfigError(f"duplicate prerequisite override edge: {edge}")
            seen_edges.add(edge)
            edges.append(edge)
            adjacency.setdefault(edge_source, set()).add(edge_target)
            adjacency.setdefault(edge_target, set())

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(url: str) -> None:
            """Visit one dependency node while enforcing limits and cycle checks."""

            if url in visiting:
                raise ConfigError(
                    f"{item_context} prerequisite override contains a cycle"
                )
            if url in visited:
                return
            visiting.add(url)
            for target in sorted(adjacency.get(url, ())):
                visit(target)
            visiting.remove(url)
            visited.add(url)

        visit(source)
        if visited != set(adjacency):
            raise ConfigError(
                f"{item_context} contains an unreachable prerequisite override edge"
            )
        parsed.append(PrerequisiteOverride(source, rationale, tuple(edges)))
    return tuple(parsed)


def _parse_source_branches(raw: object, context: str) -> tuple[str, ...]:
    """Return unique source branches after strict Git ref-format validation."""

    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{context}.source_branches must be a non-empty array")
    if any(not isinstance(item, str) or not item for item in raw):
        raise ConfigError(f"{context}.source_branches must contain strings")
    branches = tuple(raw)
    if len(set(branches)) != len(branches):
        raise ConfigError(f"{context}.source_branches must not contain duplicates")
    if any(not valid_branch_name(branch) for branch in branches):
        raise ConfigError(f"{context}.source_branches contains an invalid Git branch")
    return branches


def _parse_repository(name: str, raw: object, context: str) -> RepositoryConfig:
    """Validate one supported repository's source and destination branch policy."""

    if name not in SUPPORTED_REPOSITORIES:
        raise ConfigError(f"unsupported repository: {name}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    unsupported = set(raw) - {"source_branches", "destination_branch"}
    if unsupported:
        raise ConfigError(f"unsupported repository field: {sorted(unsupported)[0]}")
    source_branches = _parse_source_branches(raw.get("source_branches"), context)
    destination = _required_string(raw, "destination_branch", context)
    if not valid_branch_name(destination):
        raise ConfigError(f"{context}.destination_branch is not a valid Git branch")
    return RepositoryConfig(source_branches, destination)


def _parse_train(raw: object, index: int) -> TrainConfig:
    """Validate one train's identity, modes, repositories, and reviewed overrides."""

    context = f"trains[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    unsupported = set(raw) - {
        "id",
        "label",
        "state",
        "mode",
        "dependency_mode",
        "repositories",
        "prerequisite_overrides",
    }
    if unsupported:
        raise ConfigError(f"unsupported train field: {sorted(unsupported)[0]}")
    train_id = _required_string(raw, "id", context)
    if TRAIN_ID_RE.fullmatch(train_id) is None:
        raise ConfigError(f"{context}.id is invalid: {train_id!r}")
    label = _required_string(raw, "label", context)
    expected_label = f"{LABEL_PREFIX}{train_id}"
    if label != expected_label:
        raise ConfigError(f"{context}.label must be {expected_label!r}")
    state = _required_string(raw, "state", context)
    mode = _required_string(raw, "mode", context)
    if state not in VALID_STATES:
        raise ConfigError(f"{context}.state must be one of {sorted(VALID_STATES)}")
    if mode not in VALID_MODES:
        raise ConfigError(f"{context}.mode must be one of {sorted(VALID_MODES)}")
    dependency_mode = _required_string(raw, "dependency_mode", context)
    if dependency_mode not in VALID_DEPENDENCY_MODES:
        raise ConfigError(
            f"{context}.dependency_mode must be one of {sorted(VALID_DEPENDENCY_MODES)}"
        )
    repositories_raw = raw.get("repositories")
    if not isinstance(repositories_raw, dict) or not repositories_raw:
        raise ConfigError(f"{context}.repositories must be a non-empty object")
    repositories = {
        name: _parse_repository(name, value, f"{context}.repositories[{name!r}]")
        for name, value in repositories_raw.items()
    }
    overrides = _parse_prerequisite_overrides(
        raw.get("prerequisite_overrides"), context, repositories
    )
    return TrainConfig(
        id=train_id,
        label=label,
        state=state,
        mode=mode,
        repositories=repositories,
        dependency_mode=dependency_mode,
        prerequisite_overrides=overrides,
    )


def parse_config(raw: object) -> TrainCatalog:
    """Validate a complete in-memory v5 train catalog."""

    if not isinstance(raw, dict):
        raise ConfigError("configuration must be an object")
    if raw.get("schema_version") != 5:
        raise ConfigError("schema_version must be 5")
    unsupported = set(raw) - {
        "schema_version",
        "authorization",
        "dependency_policy",
        "coverage_policy",
        "trains",
    }
    if unsupported:
        raise ConfigError(f"unsupported top-level field: {sorted(unsupported)[0]}")
    authorization = _parse_authorization(raw.get("authorization"))
    dependency_policy = _parse_dependency_policy(raw.get("dependency_policy"))
    coverage_policy = _parse_coverage_policy(raw.get("coverage_policy"))
    trains_raw = raw.get("trains")
    if not isinstance(trains_raw, list):
        raise ConfigError("trains must be an array")
    trains: dict[str, TrainConfig] = {}
    labels: set[str] = set()
    for index, value in enumerate(trains_raw):
        train = _parse_train(value, index)
        if train.id in trains:
            raise ConfigError(f"duplicate train id: {train.id}")
        if train.label in labels:
            raise ConfigError(f"duplicate train label: {train.label}")
        trains[train.id] = train
        labels.add(train.label)
    return TrainCatalog(trains, authorization, dependency_policy, coverage_policy)


def load_config(path: str | Path) -> TrainCatalog:
    """Read a JSON train catalog, raising ConfigError for I/O or schema failures."""

    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load train configuration: {exc}") from exc
    return parse_config(raw)


def parse_train_label(label: str) -> str | None:
    """Return the train identifier from an exact valid label, otherwise None."""

    if not label.startswith(LABEL_PREFIX):
        return None
    train_id = label.removeprefix(LABEL_PREFIX)
    return train_id if TRAIN_ID_RE.fullmatch(train_id) else None
