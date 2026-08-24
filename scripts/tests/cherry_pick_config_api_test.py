# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from scripts.cherry_pick import config as config_module
from scripts.cherry_pick.release_hub import ReleaseHubClient, ReleaseHubError


TOKEN = "rrh1.abcdefghijkl." + "A" * 43
SOURCE_SHA = "a" * 64
ROOT_PR = "https://github.com/ROCm/rocm-systems/pull/10153"
DEP_PR = "https://github.com/ROCm/rocm-systems/pull/9716"


def config_payload():
    return {
        "requestId": "request-config",
        "data": {
            "schemaVersion": "cherry-pick-config.v1",
            "generatedAt": "2026-08-21T12:00:00.000Z",
            "source": {
                "schemaVersion": "release-trains.v5",
                "sha256": SOURCE_SHA,
                "loadedAt": "2026-08-21T11:59:00.000Z",
            },
            "authorization": {
                "minimum_human_permission": "write",
                "trusted_app_ids": [1234],
                "executor_app_id": 5678,
            },
            "dependency_policy": {"max_nodes": 64, "max_depth": 16},
            "coverage_policy": {"max_open_pull_requests": 128},
            "trains": [
                {
                    "id": "10.1-20260811",
                    "label": "cherry-pick:10.1-20260811",
                    "state": "active",
                    "mode": "validate",
                    "dependency_mode": "managed_stack",
                    "prerequisite_overrides": [
                        {
                            "source_pr": ROOT_PR,
                            "rationale": "Reviewed ROCm ordering.",
                            "edges": [{"from": ROOT_PR, "to": DEP_PR}],
                        }
                    ],
                    "repositories": {
                        "ROCm/TheRock": {
                            "source_branches": ["main"],
                            "destination_branch": "release/reviewed/therock",
                        },
                        "ROCm/rocm-systems": {
                            "source_branches": ["develop"],
                            "destination_branch": "release/reviewed/systems",
                        },
                        "ROCm/rocm-libraries": {
                            "source_branches": ["develop"],
                            "destination_branch": "release/reviewed/libraries",
                        },
                    },
                }
            ],
        },
    }


def test_parse_config_accepts_an_in_memory_complete_catalog_without_a_file():
    parse_config = getattr(config_module, "parse_config", None)
    assert parse_config is not None, "the runtime needs an in-memory config parser"

    catalog = parse_config(
        {
            "schema_version": 5,
            **{
                key: value
                for key, value in config_payload()["data"].items()
                if key
                in {
                    "authorization",
                    "dependency_policy",
                    "coverage_policy",
                    "trains",
                }
            },
        }
    )

    train = catalog.train("10.1-20260811")
    assert train.dependency_mode == "managed_stack"
    assert train.repositories["ROCm/rocm-systems"].destination_branch == (
        "release/reviewed/systems"
    )


def test_release_hub_returns_the_complete_versioned_catalog_from_one_endpoint():
    seen = []

    def transport(url, headers, timeout):
        seen.append((url, headers, timeout))
        return config_payload()

    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=transport,
        now=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    snapshot = client.cherry_pick_config()

    assert [item[0] for item in seen] == [
        "https://developer-central.amd.com/api/v1/cherry-pick/config"
    ]
    assert seen[0][1]["Authorization"] == f"Bearer {TOKEN}"
    assert snapshot.request_id == "request-config"
    assert snapshot.configuration_schema == "release-trains.v5"
    assert snapshot.configuration_sha256 == SOURCE_SHA
    assert snapshot.catalog.train("10.1-20260811").dependency_mode == "managed_stack"
    assert (
        snapshot.catalog.train("10.1-20260811")
        .repositories["ROCm/rocm-systems"]
        .destination_branch
        == "release/reviewed/systems"
    )
    assert snapshot.as_dict()["schema_version"] == "release-hub-config-snapshot.v1"
    assert "jira" not in str(snapshot.as_dict()).lower()


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda value: value.update(requestId=None), "requestId"),
        (
            lambda value: value["data"].update(schemaVersion="cherry-pick-config.v2"),
            "schema",
        ),
        (
            lambda value: value["data"]["source"].update(
                schemaVersion="release-trains.v4"
            ),
            "source schema",
        ),
        (
            lambda value: value["data"]["source"].update(sha256="bad"),
            "hash",
        ),
        (
            lambda value: value["data"]["trains"].append(
                deepcopy(value["data"]["trains"][0])
            ),
            "duplicate train",
        ),
        (
            lambda value: value["data"]["trains"][0]["repositories"][
                "ROCm/rocm-systems"
            ].update(destination_branch="bad branch"),
            "branch",
        ),
    ],
)
def test_release_hub_config_contract_fails_closed(mutate, reason):
    payload = config_payload()
    mutate(payload)
    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: payload,
    )
    with pytest.raises(ReleaseHubError, match=reason):
        client.cherry_pick_config()


def test_release_hub_config_has_no_bundled_or_last_known_good_fallback():
    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(ReleaseHubError, match="failed"):
        client.cherry_pick_config()


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda value: value.update(schema_version="v2"), "snapshot schema"),
        (
            lambda value: value["configuration"].update(
                schema_version="release-trains.v4"
            ),
            "source schema",
        ),
        (lambda value: value["configuration"].update(sha256="bad"), "source hash"),
        (lambda value: value["catalog"].update(schema_version=4), "catalog"),
    ],
)
def test_config_snapshot_parser_fails_closed_on_version_or_digest_drift(mutate, reason):
    from scripts.cherry_pick.release_hub import ReleaseHubConfigSnapshot

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()
    value = snapshot.as_dict()
    mutate(value)

    with pytest.raises(ReleaseHubError, match=reason):
        ReleaseHubConfigSnapshot.from_dict(value)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda value: value["data"].pop("authorization"), "omitted authorization"),
        (lambda value: value["data"].update(trains={}), "trains are malformed"),
        (
            lambda value: value["data"]["trains"][0].update(state="unexpected"),
            "state is invalid",
        ),
    ],
)
def test_config_endpoint_rejects_missing_or_ambiguous_train_policy(mutate, reason):
    payload = config_payload()
    mutate(payload)

    with pytest.raises(ReleaseHubError, match=reason):
        ReleaseHubClient(
            "https://developer-central.amd.com",
            TOKEN,
            transport=lambda *_args: payload,
        ).cherry_pick_config()


def test_config_endpoint_normalizes_explicitly_disabled_train_to_inactive():
    payload = config_payload()
    payload["data"]["trains"][0]["state"] = "disabled"

    snapshot = ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: payload,
    ).cherry_pick_config()

    assert snapshot.catalog.train("10.1-20260811").state == "inactive"


def test_release_hub_oidc_credentials_are_strict_and_errors_are_preserved():
    oidc = "header.payload.signature"
    client = ReleaseHubClient(
        "https://developer-central.amd.com",
        oidc,
        token_kind="oidc",
        transport=lambda *_args: (_ for _ in ()).throw(ReleaseHubError("classified")),
    )
    with pytest.raises(ReleaseHubError, match="classified"):
        client.cherry_pick_config()

    for token, kind, reason in (
        ("not-a-jwt", "oidc", "compact JWT"),
        (oidc, "unknown", "unsupported"),
    ):
        with pytest.raises(ReleaseHubError, match=reason):
            ReleaseHubClient(
                "https://developer-central.amd.com",
                token,
                token_kind=kind,
            )
