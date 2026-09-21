# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for release_utils.py.

Covers:
- extract_owner_repo URL parsing
- get_gh_token success and error cases
- _api_request HTTP handling
- fetch_repo_map .gitmodules parsing and ROCm filtering
"""
import base64
import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.release_utils import (
    ROCK_URL,
    _api_request,
    extract_owner_repo,
    fetch_repo_map,
    get_gh_token,
)

# ---------------------------------------------------------------------------
# extract_owner_repo
# ---------------------------------------------------------------------------

class TestExtractOwnerRepo:
    def test_https_url(self):
        assert extract_owner_repo("https://github.com/ROCm/hip.git") == ("ROCm", "hip")

    def test_https_url_without_dot_git(self):
        assert extract_owner_repo("https://github.com/ROCm/clr") == ("ROCm", "clr")

    def test_ssh_url(self):
        assert extract_owner_repo("git@github.com:ROCm/hip.git") == ("ROCm", "hip")

    def test_ssh_url_without_dot_git(self):
        assert extract_owner_repo("git@github.com:ROCm/clr") == ("ROCm", "clr")

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot extract owner/repo"):
            extract_owner_repo("https://gitlab.com/someorg/repo.git")

# ---------------------------------------------------------------------------
# get_gh_token
# ---------------------------------------------------------------------------

class TestGetGhToken:
    def test_returns_token_on_success(self):
        with patch("scripts.release_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="mytoken\n", returncode=0)
            assert get_gh_token() == "mytoken"

    def test_raises_on_gh_not_found(self):
        with patch("scripts.release_utils.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(SystemExit, match="gh CLI not found"):
                get_gh_token()

    def test_raises_on_auth_failure(self):
        with patch(
            "scripts.release_utils.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ):
            with pytest.raises(SystemExit, match="Not authenticated"):
                get_gh_token()

    def test_raises_on_empty_token(self):
        with patch("scripts.release_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="   \n", returncode=0)
            with pytest.raises(SystemExit, match="empty token"):
                get_gh_token()

# ---------------------------------------------------------------------------
# _api_request
# ---------------------------------------------------------------------------

class TestApiRequest:
    def test_returns_parsed_json(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("scripts.release_utils.urllib.request.urlopen", return_value=mock_resp):
            result = _api_request("https://api.github.com/repos/ROCm/hip", "token")
        assert result == {"key": "value"}

    def test_propagates_http_error(self):
        with patch(
            "scripts.release_utils.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(None, 403, "Forbidden", {}, None),
        ):
            with pytest.raises(urllib.error.HTTPError):
                _api_request("https://api.github.com/repos/ROCm/hip", "token")

# ---------------------------------------------------------------------------
# fetch_repo_map
# ---------------------------------------------------------------------------

_GITMODULES_CONTENT = """\
[submodule "external/hip"]
    path = external/hip
    url = https://github.com/ROCm/hip.git
[submodule "external/clr"]
    path = external/clr
    url = https://github.com/ROCm/clr.git
[submodule "external/llvm"]
    path = external/llvm
    url = https://github.com/llvm/llvm-project.git
"""

def _make_api_response(content: str) -> dict:
    return {"content": base64.b64encode(content.encode()).decode()}

class TestFetchRepoMap:
    def _patch_api(self, content: str):
        return patch(
            "scripts.release_utils._api_request",
            return_value=_make_api_response(content),
        )

    def test_filters_to_rocm_repos_only(self):
        with self._patch_api(_GITMODULES_CONTENT):
            repo_map = fetch_repo_map("token", "main", set())
        assert "hip" in repo_map
        assert "clr" in repo_map
        assert "llvm-project" not in repo_map

    def test_therock_always_included(self):
        with self._patch_api(_GITMODULES_CONTENT):
            repo_map = fetch_repo_map("token", "main", set())
        assert repo_map["TheRock"] == ROCK_URL

    def test_exclude_list_honoured(self):
        with self._patch_api(_GITMODULES_CONTENT):
            repo_map = fetch_repo_map("token", "main", {"hip", "TheRock"})
        assert "hip" not in repo_map
        assert "TheRock" not in repo_map
        assert "clr" in repo_map

    def test_http_error_raises_systemexit(self):
        with patch(
            "scripts.release_utils._api_request",
            side_effect=urllib.error.HTTPError(None, 404, "Not Found", {}, None),
        ):
            with pytest.raises(SystemExit, match="Failed to fetch .gitmodules"):
                fetch_repo_map("token", "nonexistent-ref", set())

    def test_network_error_raises_systemexit(self):
        with patch(
            "scripts.release_utils._api_request",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(SystemExit, match="Network error"):
                fetch_repo_map("token", "main", set())
