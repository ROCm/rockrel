import json

import pytest

from scripts.express_train.config import ConfigError, load_config, parse_train_label


def write_config(tmp_path, trains):
    path = tmp_path / "trains.json"
    path.write_text(json.dumps({"schema_version": 1, "trains": trains}))
    return path


def valid_train(**overrides):
    train = {
        "id": "10.1-20260811",
        "jira_fix_version": "10.1.0a20260811",
        "state": "active",
        "mode": "validate",
        "repositories": {
            "ROCm/TheRock": {
                "source_branch": "main",
                "target_branch": "release/bkc/therock-10.1-20260811",
            },
            "ROCm/rocm-systems": {
                "source_branch": "develop",
                "target_branch": "release/bkc/therock-10.1-20260811",
            },
        },
    }
    train.update(overrides)
    return train


def test_loads_valid_configuration(tmp_path):
    config = load_config(write_config(tmp_path, [valid_train()]))

    train = config.train("10.1-20260811")
    assert train.label == "express-train:10.1-20260811"
    assert train.repositories["ROCm/TheRock"].source_branch == "main"
    assert train.repositories["ROCm/rocm-systems"].target_branch.startswith(
        "release/"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad train"),
        ("jira_fix_version", ""),
        ("state", "paused"),
        ("mode", "write-everything"),
    ],
)
def test_rejects_invalid_train_fields(tmp_path, field, value):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, [valid_train(**{field: value})]))


def test_rejects_duplicate_train_ids(tmp_path):
    with pytest.raises(ConfigError, match="duplicate train id"):
        load_config(write_config(tmp_path, [valid_train(), valid_train()]))


def test_rejects_unapproved_repository(tmp_path):
    train = valid_train(
        repositories={
            "someone/fork": {
                "source_branch": "main",
                "target_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="unsupported repository"):
        load_config(write_config(tmp_path, [train]))


def test_rejects_non_release_target(tmp_path):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branch": "main",
                "target_branch": "main",
            }
        }
    )
    with pytest.raises(ConfigError, match="must start with release/"):
        load_config(write_config(tmp_path, [train]))


@pytest.mark.parametrize(
    "label,expected",
    [
        ("express-train:10.1-20260811", "10.1-20260811"),
        ("bug", None),
        ("express-train:", None),
        ("express-train:bad train", None),
    ],
)
def test_parse_train_label(label, expected):
    assert parse_train_label(label) == expected
