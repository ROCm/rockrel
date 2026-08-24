# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json

import pytest

from scripts.cherry_pick.config import ConfigError, load_config, parse_train_label


ROOT_PR = "https://github.com/ROCm/rocm-systems/pull/9716"
MIDDLE_PR = "https://github.com/ROCm/rocm-systems/pull/9480"
FIRST_PR = "https://github.com/ROCm/rocm-systems/pull/8221"
COMMIT_URL = (
    "https://github.com/ROCm/rocm-systems/commit/"
    "3a3fb3206000a3b47e953fd6613571ae6ca0edb4"
)


def valid_document(*, trains=None, **overrides):
    document = {
        "schema_version": 5,
        "authorization": {
            "minimum_human_permission": "write",
            "trusted_app_ids": [123456],
            "executor_app_id": 654321,
        },
        "dependency_policy": {"max_nodes": 64, "max_depth": 16},
        "coverage_policy": {"max_open_pull_requests": 128},
        "trains": trains if trains is not None else [valid_train()],
    }
    document.update(overrides)
    return document


def write_config(tmp_path, document):
    path = tmp_path / "trains.json"
    path.write_text(json.dumps(document))
    return path


def valid_train(**overrides):
    train = {
        "id": "10.1-20260811",
        "label": "cherry-pick:10.1-20260811",
        "state": "active",
        "mode": "validate",
        "dependency_mode": "gate",
        "prerequisite_overrides": [],
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


def reviewed_override(*, source=ROOT_PR, edges=None, **overrides):
    value = {
        "source_pr": source,
        "rationale": "Maintainer-reviewed ordering for this train.",
        "edges": edges if edges is not None else [{"from": ROOT_PR, "to": MIDDLE_PR}],
    }
    value.update(overrides)
    return value


def test_loads_schema_five_authorization_prerequisite_coverage_and_train_contract(
    tmp_path,
):
    config = load_config(write_config(tmp_path, valid_document()))

    train = config.train("10.1-20260811")
    assert config.train_for_label(train.label) is train
    assert config.authorization.minimum_human_permission == "write"
    assert config.authorization.trusted_app_ids == (123456,)
    assert config.authorization.executor_app_id == 654321
    assert config.dependency_policy.max_nodes == 64
    assert config.dependency_policy.max_depth == 16
    assert config.coverage_policy.max_open_pull_requests == 128
    assert train.prerequisite_overrides == ()
    assert train.repositories["ROCm/TheRock"].source_branches == ("main",)
    assert (
        train.repositories["ROCm/rocm-systems"].destination_branch
        == "release-staging/rocm-rel-10.1"
    )
    assert not hasattr(train, "requirements")


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4, 6])
def test_rejects_non_current_schema(tmp_path, schema_version):
    document = valid_document(schema_version=schema_version)
    with pytest.raises(ConfigError, match="schema_version must be 5"):
        load_config(write_config(tmp_path, document))


@pytest.mark.parametrize(
    "legacy",
    [
        {"requirements": {}},
        {"requirements": {"jira_fix_version": "10.1"}},
        {"requirements": {"block_on_dependencies": True}},
    ],
)
def test_rejects_legacy_jira_and_requirements_fields(tmp_path, legacy):
    train = valid_train(**legacy)
    with pytest.raises(ConfigError, match="unsupported train field"):
        load_config(write_config(tmp_path, valid_document(trains=[train])))


@pytest.mark.parametrize(
    "authorization,message",
    [
        (None, "authorization must be an object"),
        ({}, "minimum_human_permission"),
        (
            {
                "minimum_human_permission": "triage",
                "trusted_app_ids": [],
                "executor_app_id": None,
            },
            "write",
        ),
        (
            {
                "minimum_human_permission": "write",
                "trusted_app_ids": "1",
                "executor_app_id": None,
            },
            "array",
        ),
        (
            {
                "minimum_human_permission": "write",
                "trusted_app_ids": [0],
                "executor_app_id": None,
            },
            "positive",
        ),
        (
            {
                "minimum_human_permission": "write",
                "trusted_app_ids": [1, 1],
                "executor_app_id": None,
            },
            "duplicates",
        ),
        (
            {
                "minimum_human_permission": "write",
                "trusted_app_ids": [],
            },
            "executor_app_id",
        ),
        (
            {
                "minimum_human_permission": "write",
                "trusted_app_ids": [],
                "executor_app_id": None,
                "unexpected": True,
            },
            "unsupported authorization field",
        ),
    ],
)
def test_rejects_invalid_authorization_policy(tmp_path, authorization, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, valid_document(authorization=authorization)))


@pytest.mark.parametrize("executor_app_id", [False, 0, -1, "654321"])
def test_rejects_invalid_executor_app_identity(tmp_path, executor_app_id):
    document = valid_document()
    document["authorization"]["executor_app_id"] = executor_app_id

    with pytest.raises(ConfigError, match="executor_app_id"):
        load_config(write_config(tmp_path, document))


def test_validate_configuration_allows_unprovisioned_executor_app(tmp_path):
    document = valid_document()
    document["authorization"]["executor_app_id"] = None

    config = load_config(write_config(tmp_path, document))

    assert config.authorization.executor_app_id is None


@pytest.mark.parametrize(
    "policy,message",
    [
        (None, "dependency_policy must be an object"),
        ({"max_nodes": 0, "max_depth": 1}, "max_nodes"),
        ({"max_nodes": 65, "max_depth": 1}, "max_nodes"),
        ({"max_nodes": 1, "max_depth": 0}, "max_depth"),
        ({"max_nodes": 1, "max_depth": 17}, "max_depth"),
        ({"max_nodes": True, "max_depth": 1}, "max_nodes"),
        ({"max_nodes": 1, "max_depth": 1, "jira": True}, "unsupported"),
    ],
)
def test_rejects_invalid_dependency_policy(tmp_path, policy, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, valid_document(dependency_policy=policy)))


@pytest.mark.parametrize(
    "policy,message",
    [
        (None, "coverage_policy must be an object"),
        ({"max_open_pull_requests": 0}, "max_open_pull_requests"),
        ({"max_open_pull_requests": 129}, "max_open_pull_requests"),
        ({"max_open_pull_requests": True}, "max_open_pull_requests"),
        (
            {"max_open_pull_requests": 128, "unknown": True},
            "unsupported coverage policy field",
        ),
    ],
)
def test_rejects_invalid_coverage_policy(tmp_path, policy, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, valid_document(coverage_policy=policy)))


def test_loads_reviewed_additive_pr_and_commit_prerequisite_override(tmp_path):
    train = valid_train(
        prerequisite_overrides=[
            {
                "source_pr": ROOT_PR,
                "rationale": "Reviewed historical ordering required by maintainers.",
                "edges": [
                    {"from": ROOT_PR, "to": MIDDLE_PR},
                    {"from": MIDDLE_PR, "to": FIRST_PR},
                    {"from": FIRST_PR, "to": COMMIT_URL},
                ],
            }
        ]
    )

    parsed = load_config(write_config(tmp_path, valid_document(trains=[train])))
    override = parsed.train(train["id"]).prerequisite_overrides[0]

    assert override.source_pr == ROOT_PR
    assert override.rationale.startswith("Reviewed")
    assert override.edges == (
        (ROOT_PR, MIDDLE_PR),
        (MIDDLE_PR, FIRST_PR),
        (FIRST_PR, COMMIT_URL),
    )
    assert parsed.train(train["id"]).prerequisite_edges_for(ROOT_PR) == override.edges
    assert parsed.train(train["id"]).prerequisite_edges_for("missing") == ()


@pytest.mark.parametrize(
    "overrides,message",
    [
        (["not-an-object"], "must be an object"),
        ([reviewed_override(unexpected=True)], "unsupported prerequisite override"),
        ([reviewed_override(source=COMMIT_URL)], "source_pr must be a canonical"),
        (
            [
                reviewed_override(
                    source="https://github.com/ROCm/rocm-libraries/pull/1",
                    edges=[
                        {
                            "from": "https://github.com/ROCm/rocm-libraries/pull/1",
                            "to": "https://github.com/ROCm/rocm-libraries/pull/2",
                        }
                    ],
                )
            ],
            "repository is not configured",
        ),
        (
            [reviewed_override(), reviewed_override()],
            "duplicate prerequisite override source_pr",
        ),
        ([reviewed_override(edges=[])], "edges must be a non-empty array"),
        ([reviewed_override(edges=["not-an-object"])], "must be an object"),
        ([reviewed_override(edges=[{"from": ROOT_PR}])], "exactly from and to"),
        (
            [
                reviewed_override(
                    edges=[
                        {
                            "from": ROOT_PR,
                            "to": "https://github.com/ROCm/rocm-libraries/pull/1",
                        }
                    ]
                )
            ],
            "repository not configured",
        ),
        (
            [
                reviewed_override(
                    edges=[
                        {"from": ROOT_PR, "to": MIDDLE_PR},
                        {"from": ROOT_PR, "to": MIDDLE_PR},
                    ]
                )
            ],
            "duplicate prerequisite override edge",
        ),
        (
            [
                reviewed_override(
                    edges=[
                        {"from": ROOT_PR, "to": MIDDLE_PR},
                        {"from": MIDDLE_PR, "to": ROOT_PR},
                    ]
                )
            ],
            "contains a cycle",
        ),
        (
            [
                reviewed_override(
                    edges=[
                        {"from": ROOT_PR, "to": MIDDLE_PR},
                        {"from": FIRST_PR, "to": COMMIT_URL},
                    ]
                )
            ],
            "unreachable prerequisite override edge",
        ),
    ],
)
def test_rejects_every_reviewed_override_failure_shape(tmp_path, overrides, message):
    train = valid_train(prerequisite_overrides=overrides)
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, valid_document(trains=[train])))


def test_accepts_shared_prerequisite_subgraph_without_false_cycle(tmp_path):
    other = "https://github.com/ROCm/rocm-systems/pull/9000"
    train = valid_train(
        prerequisite_overrides=[
            reviewed_override(
                edges=[
                    {"from": ROOT_PR, "to": MIDDLE_PR},
                    {"from": ROOT_PR, "to": FIRST_PR},
                    {"from": MIDDLE_PR, "to": other},
                    {"from": FIRST_PR, "to": other},
                ]
            )
        ]
    )

    parsed = load_config(write_config(tmp_path, valid_document(trains=[train])))

    assert len(parsed.train(train["id"]).prerequisite_overrides[0].edges) == 4


@pytest.mark.parametrize(
    "override,message",
    [
        (None, "prerequisite_overrides must be an array"),
        ({}, "prerequisite_overrides must be an array"),
        ([{}], "source_pr"),
        (
            [
                {
                    "source_pr": "ROCm/rocm-systems#9716",
                    "rationale": "reviewed",
                    "edges": [],
                }
            ],
            "canonical ROCm pull request URL",
        ),
        (
            [
                {
                    "source_pr": "https://github.com/ROCm/rocm-systems/pull/9716",
                    "rationale": "",
                    "edges": [],
                }
            ],
            "rationale",
        ),
        (
            [
                {
                    "source_pr": "https://github.com/ROCm/rocm-systems/pull/9716",
                    "rationale": "reviewed",
                    "edges": [
                        {
                            "from": "https://github.com/ROCm/rocm-systems/commit/"
                            "3a3fb3206000a3b47e953fd6613571ae6ca0edb4",
                            "to": "https://github.com/ROCm/rocm-systems/pull/8221",
                        }
                    ],
                }
            ],
            "commit prerequisite must be a leaf",
        ),
    ],
)
def test_rejects_malformed_prerequisite_overrides(tmp_path, override, message):
    train = valid_train(prerequisite_overrides=override)
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, valid_document(trains=[train])))


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
        load_config(
            write_config(
                tmp_path, valid_document(trains=[valid_train(**{field: value})])
            )
        )


def test_rejects_duplicate_train_ids_and_labels(tmp_path):
    with pytest.raises(ConfigError, match="duplicate train id"):
        load_config(
            write_config(
                tmp_path, valid_document(trains=[valid_train(), valid_train()])
            )
        )

    second = valid_train(id="other", label="cherry-pick:other")
    second["label"] = valid_train()["label"]
    with pytest.raises(ConfigError, match="label"):
        load_config(
            write_config(tmp_path, valid_document(trains=[valid_train(), second]))
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
        load_config(write_config(tmp_path, valid_document(trains=[train])))


def test_accepts_multiple_safe_source_branches_and_any_safe_destination(tmp_path):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branches": ["main", "integration/next"],
                "destination_branch": "staging/candidate-1",
            }
        }
    )
    config = load_config(write_config(tmp_path, valid_document(trains=[train])))
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
        load_config(write_config(tmp_path, valid_document(trains=[train])))


@pytest.mark.parametrize("source_branches", [[], ["main", "main"], "main"])
def test_rejects_empty_duplicate_or_non_list_source_branches(tmp_path, source_branches):
    train = valid_train(
        repositories={
            "ROCm/TheRock": {
                "source_branches": source_branches,
                "destination_branch": "release/example",
            }
        }
    )
    with pytest.raises(ConfigError, match="source_branches"):
        load_config(write_config(tmp_path, valid_document(trains=[train])))


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
        load_config(write_config(tmp_path, valid_document(trains=[train])))


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


def test_catalog_rejects_unknown_ids_and_non_train_labels(tmp_path):
    catalog = load_config(write_config(tmp_path, valid_document()))
    with pytest.raises(ConfigError, match="unknown train id"):
        catalog.train("missing")
    with pytest.raises(ConfigError, match="invalid train label"):
        catalog.train_for_label("bug")
    with pytest.raises(ConfigError, match="unknown train id"):
        catalog.train_for_label("cherry-pick:missing")


def test_rejects_non_object_train_unknown_fields_and_empty_repositories(tmp_path):
    with pytest.raises(ConfigError, match=r"trains\[0\] must be an object"):
        load_config(write_config(tmp_path, valid_document(trains=["train"])))

    unexpected = valid_train()
    unexpected["unexpected"] = True
    with pytest.raises(ConfigError, match="unsupported train field"):
        load_config(write_config(tmp_path, valid_document(trains=[unexpected])))

    for repositories in ({}, []):
        with pytest.raises(ConfigError, match="repositories must be a non-empty"):
            load_config(
                write_config(
                    tmp_path,
                    valid_document(trains=[valid_train(repositories=repositories)]),
                )
            )


def test_rejects_non_object_and_unknown_repository_configuration(tmp_path):
    for repositories, message in (
        ({"ROCm/TheRock": []}, "must be an object"),
        (
            {
                "ROCm/TheRock": {
                    "source_branches": ["main"],
                    "destination_branch": "release/example",
                    "unexpected": True,
                }
            },
            "unsupported repository field",
        ),
    ):
        with pytest.raises(ConfigError, match=message):
            load_config(
                write_config(
                    tmp_path,
                    valid_document(trains=[valid_train(repositories=repositories)]),
                )
            )


def test_load_errors_are_typed_for_missing_invalid_and_unsupported_documents(tmp_path):
    with pytest.raises(ConfigError, match="cannot load train configuration"):
        load_config(tmp_path / "missing.json")

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("not-json")
    with pytest.raises(ConfigError, match="cannot load train configuration"):
        load_config(invalid_json)

    for payload, message in (
        ({"schema_version": 5, "trains": []}, "authorization"),
        ({**valid_document(), "extra": True}, "unsupported top-level"),
        ({**valid_document(), "trains": {}}, "trains must be an array"),
    ):
        path = tmp_path / f"invalid-{len(list(tmp_path.iterdir()))}.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ConfigError, match=message):
            load_config(path)
