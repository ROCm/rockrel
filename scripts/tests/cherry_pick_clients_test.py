import json

import pytest

from scripts.cherry_pick.clients import (
    ApiError,
    GitHubClient,
    JiraClient,
    extract_jira_keys,
    parse_pull_request_url,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers=None, body=None):
        self.requests.append((method, url, headers or {}, body))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_parse_pull_request_url_accepts_only_canonical_github_prs():
    assert parse_pull_request_url("https://github.com/ROCm/TheRock/pull/7282") == (
        "ROCm",
        "TheRock",
        7282,
    )
    with pytest.raises(ValueError):
        parse_pull_request_url("https://example.com/ROCm/TheRock/pull/7282")
    with pytest.raises(ValueError):
        parse_pull_request_url("https://github.com/ROCm/TheRock/issues/7282")


def test_extract_jira_keys_is_unique_and_case_normalized():
    assert extract_jira_keys("Fix ROCM-29371 and rocm-29371; relates ROCM-9") == [
        "ROCM-29371",
        "ROCM-9",
    ]


def test_github_client_finds_last_exact_label_actor():
    transport = FakeTransport(
        [
            [
                {
                    "event": "labeled",
                    "label": {"name": "cherry-pick:other"},
                    "actor": {"login": "wrong"},
                },
                {
                    "event": "labeled",
                    "label": {"name": "cherry-pick:10.1-20260811"},
                    "actor": {"login": "first"},
                },
                {
                    "event": "unlabeled",
                    "label": {"name": "cherry-pick:10.1-20260811"},
                    "actor": {"login": "remover"},
                },
                {
                    "event": "labeled",
                    "label": {"name": "cherry-pick:10.1-20260811"},
                    "actor": {"login": "operator"},
                },
            ]
        ]
    )
    github = GitHubClient("token", transport=transport)

    assert github.label_actor("ROCm", "TheRock", 7282, "cherry-pick:10.1-20260811") == "operator"
    assert transport.requests[0][2]["Accept"].endswith("+json")


def test_github_client_reads_current_permission_and_branch_protection():
    transport = FakeTransport(
        [
            {"permission": "maintain"},
            {"name": "release/bkc/test", "protected": True},
        ]
    )
    github = GitHubClient("token", transport=transport)

    assert github.permission("ROCm", "TheRock", "operator") == "maintain"
    assert github.branch("ROCm", "TheRock", "release/bkc/test") == {
        "exists": True,
        "protected": True,
        "sha": None,
    }


def test_missing_branch_is_a_fact_but_transport_errors_are_not():
    missing = GitHubClient(
        "token", transport=FakeTransport([ApiError(404, "not found")])
    )
    assert missing.branch("ROCm", "TheRock", "release/missing")["exists"] is False

    broken = GitHubClient(
        "token", transport=FakeTransport([ApiError(503, "unavailable")])
    )
    with pytest.raises(ApiError):
        broken.branch("ROCm", "TheRock", "release/test")


def test_jira_client_reads_fix_versions_with_bearer_auth():
    transport = FakeTransport(
        [{"fields": {"fixVersions": [{"name": "10.1.0a20260811"}]}}]
    )
    jira = JiraClient("https://jira.example", "secret", transport=transport)

    assert jira.fix_versions("ROCM-29371") == {"10.1.0a20260811"}
    method, url, headers, body = transport.requests[0]
    assert method == "GET"
    assert url.endswith("/rest/api/2/issue/ROCM-29371?fields=fixVersions")
    assert headers["Authorization"] == "Bearer secret"
    assert body is None


def test_github_create_pull_forces_draft_true():
    transport = FakeTransport([{"html_url": "https://github.com/ROCm/TheRock/pull/1"}])
    github = GitHubClient("token", transport=transport)

    url = github.create_pull(
        "ROCm",
        "TheRock",
        title="change",
        body="body",
        head="shared/cherry-pick/train/1",
        base="release/test",
    )

    assert url.endswith("/pull/1")
    payload = json.loads(transport.requests[0][3].decode())
    assert payload["draft"] is True


def test_github_search_returns_only_pull_request_urls():
    transport = FakeTransport(
        [
            {
                "items": [
                    {
                        "html_url": "https://github.com/ROCm/TheRock/pull/7282",
                        "pull_request": {"url": "api-url"},
                    },
                    {"html_url": "https://github.com/ROCm/TheRock/issues/1"},
                ]
            }
        ]
    )
    github = GitHubClient("token", transport=transport)

    urls = github.search_merged_labeled_pull_requests(
        "ROCm", "TheRock", "cherry-pick:10.1-20260811"
    )

    assert urls == ["https://github.com/ROCm/TheRock/pull/7282"]
    assert "is%3Amerged" in transport.requests[0][1]


def test_github_commit_and_compare_use_encoded_commit_paths():
    transport = FakeTransport(
        [
            {"sha": "a" * 40, "commit": {"message": "source"}},
            {"status": "ahead", "commits": []},
        ]
    )
    github = GitHubClient("token", transport=transport)
    assert github.commit("ROCm", "llvm-project", "a" * 40)["sha"] == "a" * 40
    assert github.compare("ROCm", "llvm-project", "a" * 40, "b" * 40)[
        "status"
    ] == "ahead"
    assert "/compare/" in transport.requests[1][1]
