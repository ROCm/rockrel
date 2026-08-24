# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json

import pytest

from scripts.cherry_pick import clients

ApiError = clients.ApiError
GitHubClient = clients.GitHubClient
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

    github.upsert_comment("ROCm", "TheRock", 1, marker="<!-- marker -->", body="new")

    method, url, _headers, payload = transport.requests[-1]
    assert method == "PATCH"
    assert url.endswith("/issues/comments/101")
    assert json.loads(payload)["body"].endswith("new")


def test_search_is_paginated_and_returns_only_pull_requests():
    first = {
        "total_count": 101,
        "incomplete_results": False,
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
        "incomplete_results": False,
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


@pytest.mark.parametrize(
    "response,message",
    [
        (
            {"total_count": 1, "incomplete_results": True, "items": []},
            "incomplete",
        ),
        (
            {"total_count": 1001, "incomplete_results": False, "items": []},
            "1,000",
        ),
        (
            {"total_count": "one", "incomplete_results": False, "items": []},
            "total_count",
        ),
    ],
)
def test_search_fails_closed_on_incomplete_or_capped_evidence(response, message):
    github = GitHubClient("token", transport=FakeTransport([response]))

    with pytest.raises(ApiError, match=message):
        github.search_merged_labeled_pull_requests(
            "ROCm", "TheRock", "cherry-pick:train"
        )


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


def test_network_deny_transport_raises_a_typed_local_review_error():
    transport = clients.DenyNetworkTransport()

    with pytest.raises(clients.RemoteAccessDisabled) as raised:
        transport.request("GET", "https://api.example", headers={}, body=b"ignored")

    assert raised.value.status == 0
    assert "network access is disabled" in raised.value.message


class FakeUrlResponse:
    def __init__(self, payload):
        self.payload = payload
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=None):
        self.read_sizes.append(size)
        if size is None:
            return self.payload
        return self.payload[:size]


class FakeUrlOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def install_fake_url_opener(monkeypatch, responses):
    opener = FakeUrlOpener(responses)
    handlers = []

    def build_opener(*configured_handlers):
        handlers.extend(configured_handlers)
        return opener

    monkeypatch.setattr(clients.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(
        clients.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("UrlLibTransport bypassed its opener"),
    )
    return opener, handlers


def test_urllib_transport_decodes_json_and_empty_responses(monkeypatch):
    first = FakeUrlResponse(b'{"ok":true}')
    second = FakeUrlResponse(b"")
    opener, handlers = install_fake_url_opener(
        monkeypatch,
        (first, second),
    )
    transport = clients.UrlLibTransport()

    assert transport.request("GET", "https://api.example") == {"ok": True}
    assert transport.request("DELETE", "https://api.example") is None
    assert len(handlers) == 2
    assert all(isinstance(handler, clients._NoRedirect) for handler in handlers)
    assert first.read_sizes == [clients.MAX_RESPONSE_BYTES + 1]
    assert second.read_sizes == [clients.MAX_RESPONSE_BYTES + 1]
    assert all(timeout == 30 for _request, timeout in opener.requests)


def test_urllib_transport_normalizes_http_url_and_json_errors(monkeypatch):
    http_error = clients.urllib.error.HTTPError(
        "https://api.example", 418, "teapot", {}, io.BytesIO(b"detail")
    )
    responses = (
        http_error,
        clients.urllib.error.URLError("offline"),
        FakeUrlResponse(b"not-json"),
    )
    install_fake_url_opener(monkeypatch, responses)
    transport = clients.UrlLibTransport()

    with pytest.raises(ApiError, match="HTTP 418"):
        transport.request("GET", "https://api.example")
    with pytest.raises(ApiError, match="offline"):
        transport.request("GET", "https://api.example")
    with pytest.raises(ApiError, match="not valid JSON"):
        transport.request("GET", "https://api.example")


def test_urllib_transport_no_redirect_handler_refuses_cross_origin_redirects(
    monkeypatch,
):
    opener, handlers = install_fake_url_opener(
        monkeypatch, (FakeUrlResponse(b'{"ok":true}'),)
    )

    result = clients.UrlLibTransport().request(
        "GET",
        "https://api.github.com/repos/ROCm/TheRock",
        headers={"Authorization": "Bearer secret"},
    )

    assert result == {"ok": True}
    assert len(handlers) == 1
    assert isinstance(handlers[0], clients._NoRedirect)
    assert (
        handlers[0].redirect_request(
            opener.requests[0][0],
            None,
            302,
            "Found",
            {},
            "https://attacker.example/steal",
        )
        is None
    )


def test_urllib_transport_rejects_oversized_success_response_without_parsing(
    monkeypatch,
):
    marker = b"sensitive-response-content"
    response = FakeUrlResponse(marker + b"x" * clients.MAX_RESPONSE_BYTES)
    install_fake_url_opener(monkeypatch, (response,))

    with pytest.raises(ApiError, match="response was too large") as raised:
        clients.UrlLibTransport().request("GET", "https://api.example")

    assert raised.value.status == 0
    assert marker.decode() not in raised.value.message
    assert response.read_sizes == [clients.MAX_RESPONSE_BYTES + 1]


class TrackingBytesIO(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def test_urllib_transport_rejects_oversized_http_error_without_disclosing_body(
    monkeypatch,
):
    marker = b"sensitive-error-content"
    error_body = TrackingBytesIO(marker + b"x" * clients.MAX_RESPONSE_BYTES)
    http_error = clients.urllib.error.HTTPError(
        "https://api.example", 413, "large", {}, error_body
    )
    install_fake_url_opener(monkeypatch, (http_error,))

    with pytest.raises(ApiError, match="HTTP 413") as raised:
        clients.UrlLibTransport().request("GET", "https://api.example")

    assert raised.value.status == 413
    assert raised.value.message == "response was too large"
    assert marker.decode() not in str(raised.value)
    assert error_body.read_sizes == [clients.MAX_RESPONSE_BYTES + 1]


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"base_delay_seconds": -0.1}, "base_delay_seconds"),
    ],
)
def test_retry_policy_rejects_unbounded_or_negative_settings(kwargs, message):
    with pytest.raises(ValueError, match=message):
        retry_policy(**kwargs)


def test_response_shape_helpers_fail_closed():
    with pytest.raises(ApiError, match="not an object"):
        clients._object([], "example")
    with pytest.raises(ApiError, match="not an array"):
        clients._array({}, "example")


def test_pull_and_pull_commits_validate_and_paginate_payloads():
    first_page = [{"sha": f"{number:040x}"} for number in range(100)]
    transport = FakeTransport([{"number": 7}, first_page, [{"sha": "f" * 40}]])
    github = GitHubClient("token", transport=transport)

    assert github.pull("ROCm", "TheRock", 7)["number"] == 7
    commits = github.pull_commits("ROCm", "TheRock", 7)
    assert len(commits) == 101
    assert commits[-1] == "f" * 40
    assert "page=2" in transport.requests[-1][1]

    malformed = GitHubClient("token", transport=FakeTransport([[{"sha": 3}]]))
    with pytest.raises(ApiError, match="omitted sha"):
        malformed.pull_commits("ROCm", "TheRock", 7)


def test_branch_rejects_non_string_commit_sha():
    github = GitHubClient("token", transport=FakeTransport([{"commit": {"sha": 7}}]))

    with pytest.raises(ApiError, match="invalid sha"):
        github.branch("ROCm", "TheRock", "release/test")


def test_destination_policy_ignores_malformed_optional_parameters():
    github = GitHubClient(
        "token",
        transport=FakeTransport(
            [
                [
                    {
                        "type": "pull_request",
                        "ruleset_id": "not-an-int",
                        "parameters": [],
                    },
                    {
                        "type": "pull_request",
                        "ruleset_id": 4,
                        "parameters": {
                            "required_approving_review_count": "one",
                            "require_last_push_approval": False,
                            "allowed_merge_methods": ["merge", 3, "merge"],
                        },
                    },
                ]
            ]
        ),
    )

    policy = github.destination_policy("ROCm", "TheRock", "release/test")
    assert policy.rule_ids == (4,)
    assert policy.required_approvals == 0
    assert policy.require_last_push_approval is False
    assert policy.allowed_merge_methods == ("merge",)


def test_pull_for_head_returns_exact_ref_or_none():
    matching = {
        "number": 1,
        "head": {
            "ref": "shared/cherry-pick/train/1",
            "repo": {"full_name": "ROCm/TheRock"},
        },
    }
    github = GitHubClient("token", transport=FakeTransport([[matching], [matching]]))

    assert (
        github.pull_for_head(
            "ROCm",
            "TheRock",
            head="shared/cherry-pick/train/1",
            base="release/test",
        )
        == matching
    )
    assert (
        github.pull_for_head("ROCm", "TheRock", head="different", base="release/test")
        is None
    )


def test_pull_for_head_ignores_same_named_branch_from_a_fork_or_malformed_head():
    branch = "shared/cherry-pick/train/1"
    fork = {
        "number": 1,
        "head": {"ref": branch, "repo": {"full_name": "someone/TheRock"}},
    }
    missing_repo = {"number": 2, "head": {"ref": branch}}
    matching = {
        "number": 3,
        "head": {"ref": branch, "repo": {"full_name": "ROCm/TheRock"}},
    }
    github = GitHubClient(
        "token", transport=FakeTransport([[fork, missing_repo, matching]])
    )

    assert (
        github.pull_for_head("ROCm", "TheRock", head=branch, base="release/test")
        == matching
    )


def test_commit_and_compare_encode_revisions_and_validate_objects():
    transport = FakeTransport([{"sha": "a" * 40}, {"status": "ahead"}])
    github = GitHubClient("token", transport=transport)

    assert github.commit("ROCm", "TheRock", "feature/name")["sha"] == "a" * 40
    assert github.compare("ROCm", "TheRock", "base/name", "head/name") == {
        "status": "ahead"
    }
    assert "feature%2Fname" in transport.requests[0][1]
    assert "base%2Fname...head%2Fname" in transport.requests[1][1]


def test_upsert_comment_creates_when_missing_and_rejects_invalid_match_id():
    create_transport = FakeTransport([[], None])
    GitHubClient("token", transport=create_transport).upsert_comment(
        "ROCm", "TheRock", 7, marker="<!-- marker -->", body="new"
    )
    assert create_transport.requests[-1][0] == "POST"

    malformed = GitHubClient(
        "token",
        transport=FakeTransport([[{"id": "bad", "body": "<!-- marker -->"}]]),
    )
    with pytest.raises(ApiError, match="omitted id"):
        malformed.upsert_comment(
            "ROCm", "TheRock", 7, marker="<!-- marker -->", body="new"
        )


def test_create_pull_rejects_non_drafts_and_missing_result_url():
    github = GitHubClient("token", transport=FakeTransport([]))
    with pytest.raises(ValueError, match="draft PRs only"):
        github.create_pull(
            "ROCm",
            "TheRock",
            title="title",
            body="body",
            head="branch",
            base="release/test",
            draft=False,
        )

    malformed = GitHubClient("token", transport=FakeTransport([{}]))
    with pytest.raises(ApiError, match="omitted html_url"):
        malformed.create_pull(
            "ROCm",
            "TheRock",
            title="title",
            body="body",
            head="branch",
            base="release/test",
        )


def test_ensure_label_updates_creates_and_propagates_other_errors():
    updated = FakeTransport([None])
    GitHubClient("token", transport=updated).ensure_label(
        "ROCm", "TheRock", name="cherry-pick:test", description="test"
    )
    assert updated.requests == [
        (
            "PATCH",
            "https://api.github.com/repos/ROCm/TheRock/labels/cherry-pick%3Atest",
            updated.requests[0][2],
            updated.requests[0][3],
        )
    ]

    created = FakeTransport([ApiError(404, "missing"), None])
    GitHubClient("token", transport=created).ensure_label(
        "ROCm", "TheRock", name="cherry-pick:test", description="test"
    )
    assert [request[0] for request in created.requests] == ["PATCH", "POST"]

    with pytest.raises(ApiError, match="forbidden"):
        GitHubClient(
            "token", transport=FakeTransport([ApiError(403, "forbidden")])
        ).ensure_label("ROCm", "TheRock", name="cherry-pick:test", description="test")


def test_upsert_check_run_rejects_unsupported_conclusion_and_malformed_match():
    github = GitHubClient("token", transport=FakeTransport([]))
    with pytest.raises(ValueError, match="unsupported Check conclusion"):
        github.upsert_check_run(
            "ROCm",
            "TheRock",
            head_sha="a" * 40,
            name="Cherry-pick",
            external_id="plan",
            conclusion="unknown",
            title="title",
            summary="summary",
        )

    malformed = GitHubClient(
        "token",
        transport=FakeTransport(
            [
                {
                    "check_runs": [
                        {"name": "Cherry-pick", "external_id": "plan", "id": True}
                    ]
                }
            ]
        ),
    )
    with pytest.raises(ApiError, match="omitted id"):
        malformed.upsert_check_run(
            "ROCm",
            "TheRock",
            head_sha="a" * 40,
            name="Cherry-pick",
            external_id="plan",
            conclusion="success",
            title="title",
            summary="summary",
        )


def test_upsert_check_run_paginates_updates_match_and_validates_result_url():
    nonmatches = [
        {"name": "Other", "external_id": f"other-{index}", "id": index}
        for index in range(100)
    ]
    transport = FakeTransport(
        [
            {"check_runs": nonmatches},
            {"check_runs": [{"name": "Cherry-pick", "external_id": "plan", "id": 7}]},
            {"html_url": "https://github.com/ROCm/TheRock/runs/7"},
        ]
    )
    url = GitHubClient("token", transport=transport).upsert_check_run(
        "ROCm",
        "TheRock",
        head_sha="a" * 40,
        name="Cherry-pick",
        external_id="plan",
        conclusion="success",
        title="title",
        summary="summary",
    )
    assert url.endswith("/runs/7")
    assert "page=2" in transport.requests[1][1]
    assert transport.requests[-1][0] == "PATCH"

    missing_url = GitHubClient(
        "token", transport=FakeTransport([{"check_runs": []}, {}])
    )
    with pytest.raises(ApiError, match="omitted html_url"):
        missing_url.upsert_check_run(
            "ROCm",
            "TheRock",
            head_sha="a" * 40,
            name="Cherry-pick",
            external_id="new-plan",
            conclusion="neutral",
            title="title",
            summary="summary",
        )
