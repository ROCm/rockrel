# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from scripts.cherry_pick.authorization import LabelTransition
import pytest

from scripts.cherry_pick.clients import ApiError, GitHubClient


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def test_label_transitions_preserve_durable_actor_and_app_identity_across_pages():
    first_page = [
        {
            "id": index,
            "node_id": f"LE_{index}",
            "event": "commented",
            "created_at": "2026-08-16T09:00:00Z",
        }
        for index in range(100)
    ]
    first_page[99] = {
        "id": 100,
        "node_id": "LE_100",
        "event": "labeled",
        "created_at": "2026-08-16T10:00:00Z",
        "label": {"name": "cherry-pick:train"},
        "actor": {"id": 7, "login": "human"},
    }
    second_page = [
        {
            "id": 101,
            "node_id": "LE_101",
            "event": "labeled",
            "created_at": "2026-08-16T11:00:00Z",
            "label": {"name": "cherry-pick:train"},
            "actor": {"id": 8, "login": "approved-labeler[bot]"},
            "performed_via_github_app": {
                "id": 123456,
                "slug": "approved-labeler",
            },
        }
    ]
    transport = FakeTransport([first_page, second_page])
    client = GitHubClient("token", transport=transport)

    transitions = client.label_transitions("ROCm", "TheRock", 10, "cherry-pick:train")

    assert transitions == (
        LabelTransition(
            event_id=100,
            node_id="LE_100",
            label="cherry-pick:train",
            action="labeled",
            created_at="2026-08-16T10:00:00Z",
            actor_id=7,
            actor_login="human",
            performed_via_app_id=None,
        ),
        LabelTransition(
            event_id=101,
            node_id="LE_101",
            label="cherry-pick:train",
            action="labeled",
            created_at="2026-08-16T11:00:00Z",
            actor_id=8,
            actor_login="approved-labeler[bot]",
            performed_via_app_id=123456,
        ),
    )
    assert "page=2" in transport.calls[-1][1]


def test_label_transitions_include_unlabeled_and_ignore_other_labels():
    transport = FakeTransport(
        [
            [
                {
                    "id": 1,
                    "node_id": "LE_1",
                    "event": "labeled",
                    "created_at": "2026-08-16T10:00:00Z",
                    "label": {"name": "other"},
                    "actor": {"id": 7, "login": "human"},
                },
                {
                    "id": 2,
                    "node_id": "LE_2",
                    "event": "unlabeled",
                    "created_at": "2026-08-16T11:00:00Z",
                    "label": {"name": "cherry-pick:train"},
                    "actor": {"id": 7, "login": "human"},
                },
            ]
        ]
    )
    transitions = GitHubClient("token", transport=transport).label_transitions(
        "ROCm", "TheRock", 10, "cherry-pick:train"
    )
    assert len(transitions) == 1
    assert transitions[0].action == "unlabeled"


def test_upsert_check_run_updates_exact_external_identity_without_duplication():
    transport = FakeTransport(
        [
            {
                "total_count": 1,
                "check_runs": [
                    {
                        "id": 55,
                        "name": "ROCm Cherry-Pick / train",
                        "external_id": "cherrypick:v1:train:1:abc",
                    }
                ],
            },
            {"id": 55, "html_url": "https://github.com/checks/55"},
        ]
    )
    client = GitHubClient("token", transport=transport)
    url = client.upsert_check_run(
        "ROCm",
        "TheRock",
        head_sha="a" * 40,
        name="ROCm Cherry-Pick / train",
        external_id="cherrypick:v1:train:1:abc",
        conclusion="neutral",
        title="Waiting",
        summary="A dependency is not contained.",
    )
    assert url == "https://github.com/checks/55"
    assert transport.calls[1][0] == "PATCH"
    assert transport.calls[1][1].endswith("/repos/ROCm/TheRock/check-runs/55")


def test_upsert_check_run_creates_when_exact_identity_is_absent():
    transport = FakeTransport(
        [
            {"total_count": 0, "check_runs": []},
            {"id": 56, "html_url": "https://github.com/checks/56"},
        ]
    )
    client = GitHubClient("token", transport=transport)
    url = client.upsert_check_run(
        "ROCm",
        "TheRock",
        head_sha="a" * 40,
        name="ROCm Cherry-Pick / train",
        external_id="cherrypick:v1:train:1:def",
        conclusion="success",
        title="Contained",
        summary="No draft is needed.",
    )
    assert url == "https://github.com/checks/56"
    assert transport.calls[1][0] == "POST"
    assert transport.calls[1][1].endswith("/repos/ROCm/TheRock/check-runs")


def test_trusted_check_external_ids_require_exact_executor_app_and_paginate():
    first_page = [
        {
            "id": index + 1,
            "name": "unrelated",
            "external_id": f"noise-{index}",
            "app": {"id": 654321},
        }
        for index in range(100)
    ]
    first_page[0] = {
        "id": 1,
        "name": "ROCm Cherry-Pick / train",
        "external_id": "cherrypick:v1:train:7:trusted-one",
        "app": {"id": 654321},
    }
    first_page[1] = {
        "id": 2,
        "name": "ROCm Cherry-Pick / train",
        "external_id": "cherrypick:v1:train:7:foreign",
        "app": {"id": 999999},
    }
    first_page[2] = {
        "id": 3,
        "name": "ROCm Cherry-Pick / train",
        "external_id": None,
        "app": {"id": 654321},
    }
    second_page = [
        {
            "id": 101,
            "name": "ROCm Cherry-Pick / train",
            "external_id": "cherrypick:v1:train:8:trusted-two",
            "app": {"id": 654321},
        }
    ]
    transport = FakeTransport(
        [
            {"total_count": 101, "check_runs": first_page},
            {"total_count": 101, "check_runs": second_page},
        ]
    )
    client = GitHubClient("token", transport=transport)

    external_ids = client.trusted_check_external_ids(
        "ROCm",
        "TheRock",
        head_sha="a" * 40,
        name="ROCm Cherry-Pick / train",
        executor_app_id=654321,
    )

    assert external_ids == (
        "cherrypick:v1:train:7:trusted-one",
        "cherrypick:v1:train:8:trusted-two",
    )
    assert "page=2" in transport.calls[-1][1]


@pytest.mark.parametrize("executor_app_id", [False, 0])
def test_trusted_check_reader_rejects_invalid_executor_identity(executor_app_id):
    client = GitHubClient("token", transport=FakeTransport([]))

    with pytest.raises(ValueError, match="executor_app_id"):
        client.trusted_check_external_ids(
            "ROCm",
            "TheRock",
            head_sha="a" * 40,
            name="ROCm Cherry-Pick / train",
            executor_app_id=executor_app_id,
        )


@pytest.mark.parametrize(
    "check_run,message",
    [
        (
            {
                "name": "ROCm Cherry-Pick / train",
                "external_id": "snapshot",
                "app": {"id": True},
            },
            "App omitted id",
        ),
        (
            {
                "name": "ROCm Cherry-Pick / train",
                "external_id": "snapshot",
                "app": {"id": "654321"},
            },
            "App omitted id",
        ),
        (
            {
                "name": "ROCm Cherry-Pick / train",
                "external_id": 7,
                "app": {"id": 654321},
            },
            "external_id",
        ),
    ],
)
def test_trusted_check_reader_rejects_malformed_identity_evidence(check_run, message):
    client = GitHubClient(
        "token",
        transport=FakeTransport([{"total_count": 1, "check_runs": [check_run]}]),
    )

    with pytest.raises(ApiError, match=message):
        client.trusted_check_external_ids(
            "ROCm",
            "TheRock",
            head_sha="a" * 40,
            name="ROCm Cherry-Pick / train",
            executor_app_id=654321,
        )


def test_clients_module_exposes_no_jira_client_or_jira_extractors():
    import scripts.cherry_pick.clients as clients

    assert not hasattr(clients, "JiraClient")
    assert not hasattr(clients, "JiraIssueEvidence")
    assert not hasattr(clients, "extract_jira_keys")


def test_client_exposes_only_canonical_label_transitions_and_no_label_removal():
    assert not hasattr(GitHubClient, "label_actor")
    assert not hasattr(GitHubClient, "remove_label")
