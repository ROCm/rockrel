import json

import pytest

from scripts.cherry_pick.config import ConfigError, load_config, parse_train_label


def write_config(tmp_path, trains, *, schema_version=2):
    path = tmp_path / "trains.json"
    path.write_text(json.dumps({"schema_version": schema_version, "trains": trains}))
    return path


def valid_train(**overrides):
    train = {
        "id": "10.1-20260811",
        "label": "cherry-pick:10.1-20260811",
        "state": "active",
        "mode": "validate",
        "requirements": {"jira_fix_version": "10.1.0a20260811"},
        "repositories": {
            "ROCm/TheRock": {
                "source_branch": "main",
                "destination_branch": "release/bkc/therock-10.1-20260811",
            },
            "ROCm/rocm-systems": {
                "source_branch": "develop",
                "destination_branch": "release/bkc/therock-10.1-20260811",
            },
        },
    }
    train.update(overrides)
    return train


def test_loads_destination_branch_train_and_resolves_exact_label(tmp_path):
    config = load_config(write_config(tmp_path, [valid_train()]))

    train = config.train("10.1-20260811")
    assert train.label == "cherry-pick:10.1-20260811"
    assert config.train_for_label(train.label) is train
    assert train.requirements.jira_fix_version == "10.1.0a20260811"
    assert train.repositories["ROCm/TheRock"].source_branch == "main"
    assert train.repositories["ROCm/rocm-systems"].destination_branch.startswith(
        "release/"
    )


def test_jira_requirement_is_optional_per_train(tmp_path):
    config = load_config(write_config(tmp_path, [valid_train(requirements={})]))
    assert config.train("10.1-20260811").requirements.jira_fix_version is None


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


def test_rejects_schema_version_one(tmp_path):
    with pytest.raises(ConfigError, match="schema_version must be 2"):
        load_config(write_config(tmp_path, [valid_train()], schema_version=1))


def test_rejects_label_that_does_not_match_train_id(tmp_path):
    with pytest.raises(ConfigError, match="label must be 'cherry-pick:10.1-20260811'"):
        load_config(
            write_config(tmp_path, [valid_train(label="cherry-pick:other-train")])
        )


def test_rejects_duplicate_train_ids(tmp_path):
    with pytest.raises(ConfigError, match="duplicate train id"):
        load_config(write_config(tmp_path, [valid_train(), valid_train()]))


def test_rejects_unknown_or_empty_requirements(tmp_path):
    with pytest.raises(ConfigError, match="unsupported requirement"):
        load_config(write_config(tmp_path, [valid_train(requirements={"build": "ok"})]))
    with pytest.raises(ConfigError, match="jira_fix_version"):
        load_config(
            write_config(tmp_path, [valid_train(requirements={"jira_fix_version": ""})])
        )


def test_rejects_unapproved_repository(tmp_path):
    train = valid_train(
        repositories={
            "someone/fork": {
                "source_branch": "main",
                "destination_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="unsupported repository"):
        load_config(write_config(tmp_path, [train]))


def test_accepts_configurable_safe_source_branch(tmp_path):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branch": "integration/next",
                "destination_branch": "release/example",
            }
        }
    )
    config = load_config(write_config(tmp_path, [train]))
    assert config.train(train["id"]).repositories["ROCm/TheRock"].source_branch == (
        "integration/next"
    )


@pytest.mark.parametrize("branch", ["", "../main", "main..old", "bad branch", "-main"])
def test_rejects_unsafe_source_branch(tmp_path, branch):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branch": branch,
                "destination_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="source_branch"):
        load_config(write_config(tmp_path, [train]))


def test_rejects_non_release_destination(tmp_path):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branch": "main",
                "destination_branch": "main",
            }
        }
    )
    with pytest.raises(ConfigError, match="must start with release/"):
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
