"""Load and validate version-controlled Express Train configuration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_REPOSITORIES = frozenset(
    {"ROCm/TheRock", "ROCm/rocm-systems", "ROCm/rocm-libraries"}
)
VALID_STATES = frozenset({"active", "inactive"})
VALID_MODES = frozenset({"disabled", "validate", "shadow", "create-draft"})
TRAIN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
LABEL_PREFIX = "express-train:"


class ConfigError(ValueError):
    """The train configuration is invalid and must not be used."""


@dataclass(frozen=True)
class RepositoryConfig:
    source_branch: str
    target_branch: str


@dataclass(frozen=True)
class TrainConfig:
    id: str
    jira_fix_version: str
    state: str
    mode: str
    repositories: dict[str, RepositoryConfig]

    @property
    def label(self) -> str:
        return f"{LABEL_PREFIX}{self.id}"


@dataclass(frozen=True)
class ExpressTrainConfig:
    trains: dict[str, TrainConfig]

    def train(self, train_id: str) -> TrainConfig:
        try:
            return self.trains[train_id]
        except KeyError as exc:
            raise ConfigError(f"unknown train id: {train_id}") from exc


def _required_string(value: dict[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return item


def _parse_repository(name: str, raw: Any, context: str) -> RepositoryConfig:
    if name not in SUPPORTED_REPOSITORIES:
        raise ConfigError(f"unsupported repository: {name}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    source = _required_string(raw, "source_branch", context)
    target = _required_string(raw, "target_branch", context)
    expected_source = "main" if name == "ROCm/TheRock" else "develop"
    if source != expected_source:
        raise ConfigError(
            f"{context}.source_branch must be {expected_source!r}, got {source!r}"
        )
    if not target.startswith("release/"):
        raise ConfigError(f"{context}.target_branch must start with release/")
    return RepositoryConfig(source_branch=source, target_branch=target)


def _parse_train(raw: Any, index: int) -> TrainConfig:
    context = f"trains[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{context} must be an object")
    train_id = _required_string(raw, "id", context)
    if TRAIN_ID_RE.fullmatch(train_id) is None:
        raise ConfigError(f"{context}.id is invalid: {train_id!r}")
    jira_fix_version = _required_string(raw, "jira_fix_version", context)
    state = _required_string(raw, "state", context)
    mode = _required_string(raw, "mode", context)
    if state not in VALID_STATES:
        raise ConfigError(f"{context}.state must be one of {sorted(VALID_STATES)}")
    if mode not in VALID_MODES:
        raise ConfigError(f"{context}.mode must be one of {sorted(VALID_MODES)}")
    raw_repositories = raw.get("repositories")
    if not isinstance(raw_repositories, dict) or not raw_repositories:
        raise ConfigError(f"{context}.repositories must be a non-empty object")
    repositories = {
        name: _parse_repository(name, value, f"{context}.repositories[{name!r}]")
        for name, value in raw_repositories.items()
    }
    return TrainConfig(
        id=train_id,
        jira_fix_version=jira_fix_version,
        state=state,
        mode=mode,
        repositories=repositories,
    )


def load_config(path: str | Path) -> ExpressTrainConfig:
    """Read *path*, validate all fields, and return immutable train objects."""

    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load train configuration: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    raw_trains = raw.get("trains")
    if not isinstance(raw_trains, list):
        raise ConfigError("trains must be an array")
    trains: dict[str, TrainConfig] = {}
    for index, raw_train in enumerate(raw_trains):
        train = _parse_train(raw_train, index)
        if train.id in trains:
            raise ConfigError(f"duplicate train id: {train.id}")
        trains[train.id] = train
    return ExpressTrainConfig(trains=trains)


def parse_train_label(label: str) -> str | None:
    """Return the train ID encoded by a valid Express Train label."""

    if not label.startswith(LABEL_PREFIX):
        return None
    train_id = label.removeprefix(LABEL_PREFIX)
    return train_id if TRAIN_ID_RE.fullmatch(train_id) else None
