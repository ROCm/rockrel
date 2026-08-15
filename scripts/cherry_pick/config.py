# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Load and validate version-controlled cherry-pick train configuration."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_REPOSITORIES = frozenset(
    {"ROCm/TheRock", "ROCm/rocm-systems", "ROCm/rocm-libraries"}
)
VALID_STATES = frozenset({"active", "inactive"})
VALID_MODES = frozenset({"disabled", "validate", "shadow", "create-draft"})
TRAIN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
LABEL_PREFIX = "cherry-pick:"


class ConfigError(ValueError):
    """The train configuration is invalid and must not be used."""


@dataclass(frozen=True)
class RepositoryConfig:
    source_branches: tuple[str, ...]
    destination_branch: str


@dataclass(frozen=True)
class TrainRequirements:
    jira_fix_version: str | None = None
    block_on_dependencies: bool = True


@dataclass(frozen=True)
class TrainConfig:
    id: str
    label: str
    state: str
    mode: str
    requirements: TrainRequirements
    repositories: dict[str, RepositoryConfig]


@dataclass(frozen=True)
class TrainCatalog:
    trains: dict[str, TrainConfig]

    def train(self, train_id: str) -> TrainConfig:
        try:
            return self.trains[train_id]
        except KeyError as exc:
            raise ConfigError(f"unknown train id: {train_id}") from exc

    def train_for_label(self, label: str) -> TrainConfig:
        train_id = parse_train_label(label)
        if train_id is None:
            raise ConfigError(f"invalid train label: {label}")
        train = self.train(train_id)
        if train.label != label:
            raise ConfigError(f"unknown train label: {label}")
        return train


def _required_string(value: dict[str, object], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return item


def _valid_branch(branch: str) -> bool:
    """Delegate branch syntax to Git's canonical ref-format implementation."""

    # Git accepts ``@`` as a branch name, but Git also interprets it as HEAD in
    # revision arguments. Reject that ambiguous spelling at the trust boundary.
    if branch == "@":
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


def _parse_source_branches(raw: object, context: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{context}.source_branches must be a non-empty array")
    if any(not isinstance(item, str) or not item for item in raw):
        raise ConfigError(f"{context}.source_branches must contain strings")
    branches = tuple(raw)
    if len(set(branches)) != len(branches):
        raise ConfigError(f"{context}.source_branches must not contain duplicates")
    if any(not _valid_branch(branch) for branch in branches):
        raise ConfigError(f"{context}.source_branches contains an invalid Git branch")
    return branches


def _parse_repository(
    name: str, raw: object, context: str
) -> RepositoryConfig:
    if name not in SUPPORTED_REPOSITORIES:
        raise ConfigError(f"unsupported repository: {name}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    source_branches = _parse_source_branches(raw.get("source_branches"), context)
    destination = _required_string(raw, "destination_branch", context)
    if not _valid_branch(destination):
        raise ConfigError(f"{context}.destination_branch is not a valid Git branch")
    unsupported = set(raw) - {"source_branches", "destination_branch"}
    if unsupported:
        raise ConfigError(f"unsupported repository field: {sorted(unsupported)[0]}")
    return RepositoryConfig(
        source_branches=source_branches,
        destination_branch=destination,
    )


def _parse_requirements(raw: object, context: str) -> TrainRequirements:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    unsupported = set(raw) - {"jira_fix_version", "block_on_dependencies"}
    if unsupported:
        raise ConfigError(f"unsupported requirement: {sorted(unsupported)[0]}")
    jira_fix_version = raw.get("jira_fix_version")
    if jira_fix_version is not None and (
        not isinstance(jira_fix_version, str) or not jira_fix_version.strip()
    ):
        raise ConfigError(f"{context}.jira_fix_version must be a non-empty string")
    block_on_dependencies = raw.get("block_on_dependencies", True)
    if not isinstance(block_on_dependencies, bool):
        raise ConfigError(f"{context}.block_on_dependencies must be a boolean")
    return TrainRequirements(
        jira_fix_version=jira_fix_version,
        block_on_dependencies=block_on_dependencies,
    )


def _parse_train(raw: object, index: int) -> TrainConfig:
    context = f"trains[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    unsupported = set(raw) - {
        "id",
        "label",
        "state",
        "mode",
        "requirements",
        "repositories",
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
    requirements = _parse_requirements(raw.get("requirements"), f"{context}.requirements")
    raw_repositories = raw.get("repositories")
    if not isinstance(raw_repositories, dict) or not raw_repositories:
        raise ConfigError(f"{context}.repositories must be a non-empty object")
    repositories = {
        name: _parse_repository(name, value, f"{context}.repositories[{name!r}]")
        for name, value in raw_repositories.items()
    }
    return TrainConfig(
        id=train_id,
        label=label,
        state=state,
        mode=mode,
        requirements=requirements,
        repositories=repositories,
    )


def load_config(path: str | Path) -> TrainCatalog:
    """Read *path*, validate all fields, and return immutable train objects."""

    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load train configuration: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 3:
        raise ConfigError("schema_version must be 3")
    if set(raw) - {"schema_version", "trains"}:
        raise ConfigError("configuration contains unsupported top-level fields")
    raw_trains = raw.get("trains")
    if not isinstance(raw_trains, list):
        raise ConfigError("trains must be an array")
    trains: dict[str, TrainConfig] = {}
    labels: set[str] = set()
    for index, raw_train in enumerate(raw_trains):
        train = _parse_train(raw_train, index)
        if train.id in trains:
            raise ConfigError(f"duplicate train id: {train.id}")
        if train.label in labels:
            raise ConfigError(f"duplicate train label: {train.label}")
        trains[train.id] = train
        labels.add(train.label)
    return TrainCatalog(trains=trains)


def parse_train_label(label: str) -> str | None:
    """Return the train ID encoded by a valid cherry-pick label."""

    if not label.startswith(LABEL_PREFIX):
        return None
    train_id = label.removeprefix(LABEL_PREFIX)
    return train_id if TRAIN_ID_RE.fullmatch(train_id) else None
