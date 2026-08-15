# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json

import pytest

from scripts.cherry_pick import clients


ApiError = clients.ApiError
GitHubClient = clients.GitHubClient
JiraClient = clients.JiraClient
extract_jira_keys = clients.extract_jira_keys
parse_pull_request_url = clients.parse_pull_request_url


def retry_policy(**kwargs):
    policy_type = getattr(clients, "RetryPolicy", None)
    assert policy_type is not None, "RetryPolicy must define bounded retries"
    return policy_type(**kwargs)


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


def test_extracts_jira_keys_and_structured_dependency_trailers():
    text = """Fix ROCM-29371 and rocm-29371; relates ROCM-9

Cherry-Pick-Depends-On: https://github.com/ROCm/TheRock/pull/7000, ROCM-4
Cherry-Pick-After: ROCM-3
"""
    assert extract_jira_keys(text) == ["ROCM-29371", "ROCM-9", "ROCM-4", "ROCM-3"]
    extractor = getattr(clients, "extract_dependency_trailers", None)
    assert extractor is not None, "structured dependency trailers are required"
    assert extractor(text) == (
        "https://github.com/ROCm/TheRock/pull/7000",
        "ROCM-4",
        "ROCM-3",
    )


def test_default_clients_have_isolated_network_denying_transports():
    first = GitHubClient("token")
    second = GitHubClient("token")
    assert type(first.transport).__name__ == "DenyNetworkTransport"
    assert first.transport is not second.transport


def test_github_client_retries_transient_errors_with_injected_sleep():
    transport = FakeTransport([ApiError(503, "busy"), {"permission": "maintain"}])
    delays = []
    github = GitHubClient(
        "token",
        transport=transport,
        retry_policy=retry_policy(max_attempts=2, base_delay_seconds=0.25),
        sleep=delays.append,
    )

    assert github.permission("ROCm", "TheRock", "operator") == "maintain"
    assert delays == [0.25]
    assert len(transport.requests) == 2


def test_github_client_does_not_retry_permanent_errors():
    transport = FakeTransport([ApiError(404, "missing")])
    github = GitHubClient(
        "token",
        transport=transport,
        retry_policy=retry_policy(max_attempts=3, base_delay_seconds=0),
        sleep=lambda _delay: None,
    )
    with pytest.raises(ApiError, match="404"):
        github.permission("ROCm", "TheRock", "operator")
    assert len(transport.requests) == 1


def test_github_client_retries_only_rate_limit_403_errors():
    rate_limited = FakeTransport(
        [ApiError(403, "API rate limit exceeded"), {"permission": "write"}]
    )
    github = GitHubClient(
        "token",
        transport=rate_limited,
        retry_policy=retry_policy(max_attempts=2, base_delay_seconds=0),
        sleep=lambda _delay: None,
    )
    assert github.permission("ROCm", "TheRock", "operator") == "write"

    forbidden = GitHubClient(
        "token",
        transport=FakeTransport([ApiError(403, "forbidden")]),
        retry_policy=retry_policy(max_attempts=2, base_delay_seconds=0),
        sleep=lambda _delay: None,
    )
    with pytest.raises(ApiError, match="forbidden"):
        forbidden.permission("ROCm", "TheRock", "operator")


def test_github_client_finds_last_exact_label_actor_across_pages():
    first_page = [
        {
            "event": "labeled",
            "label": {"name": "cherry-pick:other"},
            "actor": {"login": "wrong"},
        }
    ] * 100
    second_page = [
        {
            "event": "labeled",
            "label": {"name": "cherry-pick:10.1-20260811"},
            "actor": {"login": "operator"},
        }
    ]
    transport = FakeTransport([first_page, second_page])
    github = GitHubClient("token", transport=transport)

    assert (
        github.label_actor(
            "ROCm", "TheRock", 7282, "cherry-pick:10.1-20260811"
        )
        == "operator"
    )
    assert "page=2" in transport.requests[1][1]


def test_github_client_reads_branch_and_effective_pull_request_rule():
    transport = FakeTransport(
        [
            {"name": "release/bkc/test", "commit": {"sha": "a" * 40}},
            [
                {
                    "type": "deletion",
                    "ruleset_id": 10,
                    "source": "ROCm",
                    "source_type": "Organization",
                },
                {
                    "type": "pull_request",
                    "ruleset_id": 20,
                    "source": "ROCm",
                    "source_type": "Organization",
                    "parameters": {
                        "required_approving_review_count": 1,
                        "require_last_push_approval": True,
                        "allowed_merge_methods": ["squash"],
                    },
                },
            ],
        ]
    )
    github = GitHubClient("token", transport=transport)

    branch = github.branch("ROCm", "TheRock", "release/bkc/test")
    policy = github.destination_policy("ROCm", "TheRock", "release/bkc/test")
    assert branch.exists is True
    assert branch.sha == "a" * 40
    assert policy.pull_request_required is True
    assert policy.rule_ids == (20,)
    assert policy.required_approvals == 1
    assert policy.require_last_push_approval is True
    assert policy.allowed_merge_methods == ("squash",)


def test_missing_branch_is_a_fact_but_transport_errors_are_not():
    missing = GitHubClient(
        "token", transport=FakeTransport([ApiError(404, "not found")])
    )
    assert missing.branch("ROCm", "TheRock", "release/missing").exists is False

    broken = GitHubClient(
        "token",
        transport=FakeTransport([ApiError(500, "unavailable")]),
        retry_policy=retry_policy(max_attempts=1),
    )
    with pytest.raises(ApiError):
        broken.branch("ROCm", "TheRock", "release/test")


def test_pulls_and_comments_are_fully_paginated():
    first_pulls = [{"number": number} for number in range(1, 101)]
    second_pulls = [{"number": 101}]
    first_comments = [{"id": number, "body": ""} for number in range(1, 101)]
    second_comments = [{"id": 101, "body": "marker"}]
    transport = FakeTransport(
        [first_pulls, second_pulls, first_comments, second_comments]
    )
    github = GitHubClient("token", transport=transport)

    assert len(github.pulls("ROCm", "TheRock", base="release/test")) == 101
    assert len(github.issue_comments("ROCm", "TheRock", 1)) == 101
    assert "page=2" in transport.requests[1][1]
    assert "page=2" in transport.requests[3][1]


def test_sticky_comment_after_first_hundred_is_updated_not_duplicated():
    first_comments = [{"id": number, "body": ""} for number in range(1, 101)]
    second_comments = [{"id": 101, "body": "<!-- marker -->\nold"}]
    transport = FakeTransport([first_comments, second_comments, None])
    github = GitHubClient("token", transport=transport)

    github.upsert_comment(
        "ROCm", "TheRock", 1, marker="<!-- marker -->", body="new"
    )

    method, url, _headers, payload = transport.requests[-1]
    assert method == "PATCH"
    assert url.endswith("/issues/comments/101")
    assert json.loads(payload)["body"].endswith("new")


def test_search_is_paginated_and_returns_only_pull_requests():
    first = {
        "total_count": 101,
        "items": [
            {
                "html_url": f"https://github.com/ROCm/TheRock/pull/{number}",
                "pull_request": {"url": "api-url"},
            }
            for number in range(1, 101)
        ],
    }
    second = {
        "total_count": 101,
        "items": [
            {
                "html_url": "https://github.com/ROCm/TheRock/pull/101",
                "pull_request": {"url": "api-url"},
            },
            {"html_url": "https://github.com/ROCm/TheRock/issues/1"},
        ],
    }
    transport = FakeTransport([first, second])
    github = GitHubClient("token", transport=transport)

    urls = github.search_merged_labeled_pull_requests(
        "ROCm", "TheRock", "cherry-pick:10.1-20260811"
    )
    assert len(urls) == 101
    assert urls[-1].endswith("/pull/101")
    assert "page=2" in transport.requests[1][1]


def test_jira_client_returns_fix_versions_and_dependency_evidence():
    transport = FakeTransport(
        [
            {
                "fields": {
                    "fixVersions": [{"name": "10.1.0a20260811"}],
                    "issuelinks": [
                        {
                            "type": {"inward": "is blocked by"},
                            "inwardIssue": {"key": "ROCM-1"},
                        }
                    ],
                    "customfield_order": "Apply after compiler change",
                }
            }
        ]
    )
    jira = JiraClient("https://jira.example", "secret", transport=transport)

    evidence = jira.issue_evidence(
        "ROCM-29371", ordering_fields=("customfield_order",)
    )
    assert evidence.fix_versions == frozenset({"10.1.0a20260811"})
    assert evidence.dependencies == ("ROCM-1",)
    assert evidence.ordering_notes == ("Apply after compiler change",)
    assert "fields=fixVersions%2Cissuelinks%2Ccustomfield_order" in transport.requests[0][1]


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


def test_malformed_api_shapes_raise_typed_api_errors():
    github = GitHubClient("token", transport=FakeTransport([{"permission": 3}]))
    with pytest.raises(ApiError, match="permission"):
        github.permission("ROCm", "TheRock", "operator")
