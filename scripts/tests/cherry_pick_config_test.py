# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json

import pytest

from scripts.cherry_pick.config import ConfigError, load_config, parse_train_label


def write_config(tmp_path, trains, *, schema_version=3):
    path = tmp_path / "trains.json"
    path.write_text(json.dumps({"schema_version": schema_version, "trains": trains}))
    return path


def valid_train(**overrides):
    train = {
        "id": "10.1-20260811",
        "label": "cherry-pick:10.1-20260811",
        "state": "active",
        "mode": "validate",
        "requirements": {
            "jira_fix_version": "10.1.0a20260811",
            "block_on_dependencies": True,
        },
        "repositories": {
            "ROCm/TheRock": {
                "source_branches": ["main"],
                "destination_branch": "release/bkc/therock-10.1-20260811",
            },
            "ROCm/rocm-systems": {
                "source_branches": ["develop"],
                "destination_branch": "release-staging/rocm-rel-10.1",
            },
        },
    }
    train.update(overrides)
    return train


def test_loads_schema_three_train_and_resolves_exact_label(tmp_path):
    config = load_config(write_config(tmp_path, [valid_train()]))

    train = config.train("10.1-20260811")
    assert train.label == "cherry-pick:10.1-20260811"
    assert config.train_for_label(train.label) is train
    assert train.requirements.jira_fix_version == "10.1.0a20260811"
    assert train.requirements.block_on_dependencies is True
    assert train.repositories["ROCm/TheRock"].source_branches == ("main",)
    assert (
        train.repositories["ROCm/rocm-systems"].destination_branch
        == "release-staging/rocm-rel-10.1"
    )


def test_jira_and_dependency_requirements_have_safe_defaults(tmp_path):
    config = load_config(write_config(tmp_path, [valid_train(requirements={})]))
    requirements = config.train("10.1-20260811").requirements
    assert requirements.jira_fix_version is None
    assert requirements.block_on_dependencies is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad train"),
        ("label", "release:10.1-20260811"),
        ("state", "paused"),
        ("mode", "write-everything"),
    ],
)
def test_rejects_invalid_train_fields(tmp_path, field, value):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, [valid_train(**{field: value})]))


@pytest.mark.parametrize("schema_version", [1, 2, 4])
def test_rejects_non_current_schema(tmp_path, schema_version):
    with pytest.raises(ConfigError, match="schema_version must be 3"):
        load_config(
            write_config(
                tmp_path, [valid_train()], schema_version=schema_version
            )
        )


def test_rejects_duplicate_train_ids_and_labels(tmp_path):
    with pytest.raises(ConfigError, match="duplicate train id"):
        load_config(write_config(tmp_path, [valid_train(), valid_train()]))

    second = valid_train(id="other", label="cherry-pick:other")
    second["label"] = valid_train()["label"]
    with pytest.raises(ConfigError, match="label"):
        load_config(write_config(tmp_path, [valid_train(), second]))


def test_rejects_unknown_or_invalid_requirements(tmp_path):
    with pytest.raises(ConfigError, match="unsupported requirement"):
        load_config(write_config(tmp_path, [valid_train(requirements={"build": "ok"})]))
    with pytest.raises(ConfigError, match="jira_fix_version"):
        load_config(
            write_config(
                tmp_path,
                [valid_train(requirements={"jira_fix_version": ""})],
            )
        )
    with pytest.raises(ConfigError, match="block_on_dependencies"):
        load_config(
            write_config(
                tmp_path,
                [valid_train(requirements={"block_on_dependencies": "yes"})],
            )
        )


def test_rejects_unapproved_repository(tmp_path):
    train = valid_train(
        repositories={
            "someone/fork": {
                "source_branches": ["main"],
                "destination_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="unsupported repository"):
        load_config(write_config(tmp_path, [train]))


def test_accepts_multiple_safe_source_branches_and_any_safe_destination(tmp_path):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branches": ["main", "integration/next"],
                "destination_branch": "staging/candidate-1",
            }
        }
    )
    config = load_config(write_config(tmp_path, [train]))
    repository = config.train(train["id"]).repositories["ROCm/TheRock"]
    assert repository.source_branches == ("main", "integration/next")
    assert repository.destination_branch == "staging/candidate-1"


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "../main",
        "main..old",
        "bad branch",
        "-main",
        "@",
        ".hidden/main",
        "topic/.hidden",
        "topic/name.lock",
        "topic/.",
        "topic//name",
        "topic/@{name",
    ],
)
def test_rejects_every_invalid_source_ref(tmp_path, branch):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branches": [branch],
                "destination_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="source_branches"):
        load_config(write_config(tmp_path, [train]))


@pytest.mark.parametrize("source_branches", [[], ["main", "main"], "main"])
def test_rejects_empty_duplicate_or_non_list_source_branches(
    tmp_path, source_branches
):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branches": source_branches,
                "destination_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="source_branches"):
        load_config(write_config(tmp_path, [train]))


@pytest.mark.parametrize("branch", ["main..bad", "bad branch", "-release", "@"])
def test_rejects_invalid_destination_ref_without_prefix_policy(tmp_path, branch):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branches": ["main"],
                "destination_branch": branch,
            }
        }
    )
    with pytest.raises(ConfigError, match="destination_branch"):
        load_config(write_config(tmp_path, [train]))


@pytest.mark.parametrize(
    "label,expected",
    [
        ("cherry-pick:10.1-20260811", "10.1-20260811"),
        ("bug", None),
        ("cherry-pick:", None),
        ("cherry-pick:bad train", None),
    ],
)
def test_parse_train_label(label, expected):
    assert parse_train_label(label) == expected
