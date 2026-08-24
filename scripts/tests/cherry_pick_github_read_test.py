# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
from types import SimpleNamespace

import pytest

from scripts.cherry_pick import github_read


class FakeReadTransport:
    """Record GET-only calls made through the Marketplace client."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, *, headers=None):
        self.requests.append((url, headers or {}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Response(io.BytesIO):
    """Provide the context-manager interface used by urllib responses."""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_read_client_exposes_only_get_transport_and_no_mutation_methods():
    transport = FakeReadTransport([{"permission": "maintain"}])
    client = github_read.GitHubReadClient("token", transport=transport)

    assert client.permission("ROCm", "TheRock", "operator") == "maintain"
    assert len(transport.requests) == 1
    assert transport.requests[0][0].endswith(
        "/repos/ROCm/TheRock/collaborators/operator/permission"
    )
    for name in (
        "create_pull",
        "ensure_label",
        "issue_comments",
        "upsert_check_run",
        "upsert_comment",
    ):
        assert not hasattr(client, name)
    assert not hasattr(transport, "request")


def test_read_transport_is_get_only_no_redirect_bounded_and_timed(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, *, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return Response(b'{"ok": true}')

    def build_opener(handler):
        seen["handler"] = handler
        return Opener()

    monkeypatch.setattr(github_read.urllib.request, "build_opener", build_opener)

    result = github_read.UrlLibReadTransport().get(
        "https://api.github.com/repos/ROCm/TheRock",
        headers={"Authorization": "Bearer secret"},
    )

    assert result == {"ok": True}
    assert seen["request"].method == "GET"
    assert seen["timeout"] == github_read.DEFAULT_TIMEOUT_SECONDS
    assert type(seen["handler"]).__name__ == "_NoRedirect"


def test_read_transport_rejects_oversized_response(monkeypatch):
    class Opener:
        def open(self, _request, *, timeout):
            assert timeout == github_read.DEFAULT_TIMEOUT_SECONDS
            return Response(b"x" * (github_read.MAX_RESPONSE_BYTES + 1))

    monkeypatch.setattr(
        github_read.urllib.request,
        "build_opener",
        lambda _handler: Opener(),
    )

    with pytest.raises(github_read.ApiError, match="too large"):
        github_read.UrlLibReadTransport().get("https://api.github.com/example")


def test_gh_factory_returns_read_only_client_without_mutating_gh_state():
    seen = []

    def run(arguments, **kwargs):
        seen.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout="token-value\n", stderr="")

    client = github_read.gh_github_read_client({}, run=run)

    assert isinstance(client, github_read.GitHubReadClient)
    assert type(client.transport) is github_read.UrlLibReadTransport
    assert seen[0][0] == ["gh", "auth", "token", "--hostname", "github.com"]
    assert seen[0][1]["stdin"] is not None


@pytest.mark.parametrize(
    "environment",
    ({"GITHUB_ACTIONS": "true"}, {"GH_HOST": "github.example.com"}),
)
def test_gh_factory_rejects_unsupported_auth_context_before_subprocess(environment):
    with pytest.raises(github_read.ReadOnlyGitHubError):
        github_read.gh_github_read_client(
            environment,
            run=lambda *_args, **_kwargs: pytest.fail("gh must not be called"),
        )
