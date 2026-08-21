#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for release_utils.py.

Covers:
- extract_owner_repo
- get_gh_token
- fetch_lightweight_plan
- check_permissions
- RockBase.convert_to_ssh
- RockBase.get_submodule_url_map
- RockBase.build_plan (clone / reuse / submodule parsing)
"""
import base64
import json
import subprocess
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.release_utils import (
    RepoInfo,
    RockBase,
    check_permissions,
    extract_owner_repo,
    fetch_lightweight_plan,
    get_gh_token,
    ROCK_URL,
    TIMEOUT_LONG,
)


_FAKE_COMMIT = "a" * 40


def make_base(**kwargs) -> RockBase:
    """Instantiate a bare RockBase (concrete enough for testing shared methods)."""
    defaults = dict(
        branch_name="release/6.4",
        commitid=_FAKE_COMMIT,
        dry_run=True,
        exclude_list=[],
        force_clone=False,
        cache_dir=None,
    )
    defaults.update(kwargs)
    return RockBase(SimpleNamespace(**defaults))


def _make_mock_response(payload: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# extract_owner_repo
# ---------------------------------------------------------------------------

class TestExtractOwnerRepo:
    @pytest.mark.parametrize("url,owner,repo", [
        ("https://github.com/ROCm/hip.git",     "ROCm", "hip"),
        ("https://github.com/ROCm/hip",         "ROCm", "hip"),
        ("git@github.com:ROCm/hip.git",         "ROCm", "hip"),
        ("git@github.com:ROCm/hip",             "ROCm", "hip"),
        ("https://github.com/ROCm/TheRock.git", "ROCm", "TheRock"),
        ("https://github.com/llvm/llvm-project.git", "llvm", "llvm-project"),
    ])
    def test_valid_urls(self, url, owner, repo):
        assert extract_owner_repo(url) == (owner, repo)

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot extract owner/repo"):
            extract_owner_repo("not-a-url")

    def test_gitlab_url_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_owner_repo("https://gitlab.com/ROCm/hip.git")

    def test_bare_hostname_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_owner_repo("https://github.com/ROCm")


# ---------------------------------------------------------------------------
# get_gh_token
# ---------------------------------------------------------------------------

class TestGetGhToken:
    def test_returns_token_on_success(self):
        mock_result = MagicMock()
        mock_result.stdout = "ghp_faketoken\n"
        with patch("subprocess.run", return_value=mock_result):
            token = get_gh_token()
        assert token == "ghp_faketoken"

    def test_gh_not_found_raises_system_exit(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(SystemExit, match="gh CLI not found"):
                get_gh_token()

    def test_not_authenticated_raises_system_exit(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ):
            with pytest.raises(SystemExit, match="Not authenticated"):
                get_gh_token()

    def test_empty_token_raises_system_exit(self):
        mock_result = MagicMock()
        mock_result.stdout = "   "
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit, match="empty token"):
                get_gh_token()


# ---------------------------------------------------------------------------
# fetch_lightweight_plan
# ---------------------------------------------------------------------------

def _gitmodules_content(entries: list[tuple[str, str]]) -> str:
    """Build a .gitmodules-style string from (path, url) pairs."""
    blocks = []
    for path, url in entries:
        name = Path(path).name
        blocks.append(
            f'[submodule "{path}"]\n'
            f"\tpath = {path}\n"
            f"\turl = {url}\n"
        )
    return "\n".join(blocks)


def _make_gitmodules_response(entries: list[tuple[str, str]]) -> MagicMock:
    raw = _gitmodules_content(entries)
    content_b64 = base64.b64encode(raw.encode()).decode()
    return _make_mock_response({"content": content_b64})


class TestFetchLightweightPlan:
    def test_rocm_repos_included(self):
        entries = [
            ("external/hip", "https://github.com/ROCm/hip.git"),
            ("external/clr", "https://github.com/ROCm/clr.git"),
        ]
        mock_resp = _make_gitmodules_response(entries)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_lightweight_plan("token", _FAKE_COMMIT, set())

        assert "hip" in result
        assert "clr" in result
        assert result["hip"] == "https://github.com/ROCm/hip.git"

    def test_therock_always_included(self):
        mock_resp = _make_gitmodules_response([])
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_lightweight_plan("token", _FAKE_COMMIT, set())

        assert "TheRock" in result
        assert result["TheRock"] == ROCK_URL

    def test_non_rocm_repos_excluded(self):
        entries = [
            ("external/llvm", "https://github.com/llvm/llvm-project.git"),
            ("external/hip",  "https://github.com/ROCm/hip.git"),
        ]
        mock_resp = _make_gitmodules_response(entries)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_lightweight_plan("token", _FAKE_COMMIT, set())

        assert "llvm-project" not in result
        assert "hip" in result

    def test_exclude_list_respected(self):
        entries = [
            ("external/hip", "https://github.com/ROCm/hip.git"),
            ("external/clr", "https://github.com/ROCm/clr.git"),
        ]
        mock_resp = _make_gitmodules_response(entries)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_lightweight_plan("token", _FAKE_COMMIT, {"hip"})

        assert "hip" not in result
        assert "clr" in result

    def test_http_error_raises_system_exit(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url=None, code=404, msg="Not Found", hdrs=None, fp=None
            ),
        ):
            with pytest.raises(SystemExit, match="HTTP 404"):
                fetch_lightweight_plan("token", _FAKE_COMMIT, set())

    def test_network_error_raises_system_exit(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("name resolution failed"),
        ):
            with pytest.raises(SystemExit, match="Network error"):
                fetch_lightweight_plan("token", _FAKE_COMMIT, set())

    def test_ssh_urls_in_gitmodules_included(self):
        entries = [
            ("external/hip", "git@github.com:ROCm/hip.git"),
        ]
        mock_resp = _make_gitmodules_response(entries)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_lightweight_plan("token", _FAKE_COMMIT, set())

        assert "hip" in result


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------

class TestCheckPermissions:
    def _logger(self):
        import logging
        return logging.getLogger("test")

    def test_push_access_passes(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        mock_resp = _make_mock_response({"permissions": {"push": True, "admin": False}})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            check_permissions("token", repo_map, self._logger())  # must not raise

    def test_admin_access_passes(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        mock_resp = _make_mock_response({"permissions": {"push": False, "admin": True}})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            check_permissions("token", repo_map, self._logger())  # must not raise

    def test_no_access_raises_system_exit(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        mock_resp = _make_mock_response({"permissions": {"push": False, "admin": False}})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger())
        assert "hip" in str(exc_info.value)

    def test_http_403_recorded_as_failure(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url=None, code=403, msg="Forbidden", hdrs=None, fp=None
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger())
        assert "hip" in str(exc_info.value)

    def test_http_404_recorded_as_failure(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url=None, code=404, msg="Not Found", hdrs=None, fp=None
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger())
        assert "hip" in str(exc_info.value)

    def test_other_http_error_recorded_as_failure(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url=None, code=500, msg="Server Error", hdrs=None, fp=None
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger())
        assert "hip" in str(exc_info.value)

    def test_network_error_recorded_as_failure(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger())
        assert "hip" in str(exc_info.value)

    def test_all_repos_checked_before_abort(self):
        """All repos are checked even if the first one fails."""
        repo_map = {
            "hip": "https://github.com/ROCm/hip.git",
            "clr": "https://github.com/ROCm/clr.git",
        }
        mock_resp = _make_mock_response({"permissions": {"push": False, "admin": False}})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger())
        msg = str(exc_info.value)
        assert "hip" in msg
        assert "clr" in msg

    def test_invalid_url_recorded_as_failure(self):
        repo_map = {"bad": "not-a-url"}
        with pytest.raises(SystemExit) as exc_info:
            check_permissions("token", repo_map, self._logger())
        assert "bad" in str(exc_info.value)

    def test_action_label_appears_in_abort_message(self):
        repo_map = {"hip": "https://github.com/ROCm/hip.git"}
        mock_resp = _make_mock_response({"permissions": {"push": False, "admin": False}})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(SystemExit) as exc_info:
                check_permissions("token", repo_map, self._logger(), action="tags")
        assert "tags" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RockBase.convert_to_ssh
# ---------------------------------------------------------------------------

class TestConvertToSsh:
    def test_https_converted(self):
        base = make_base()
        assert base.convert_to_ssh("https://github.com/ROCm/hip.git") == \
            "git@github.com:ROCm/hip.git"

    def test_https_without_dot_git(self):
        base = make_base()
        assert base.convert_to_ssh("https://github.com/ROCm/clr") == \
            "git@github.com:ROCm/clr"

    def test_ssh_passthrough(self):
        base = make_base()
        url = "git@github.com:ROCm/hip.git"
        assert base.convert_to_ssh(url) == url

    def test_non_github_passthrough(self):
        base = make_base()
        url = "https://gitlab.com/org/repo.git"
        assert base.convert_to_ssh(url) == url


# ---------------------------------------------------------------------------
# RockBase.get_submodule_url_map
# ---------------------------------------------------------------------------

class TestGetSubmoduleUrlMap:
    def test_no_gitmodules_returns_empty(self, tmp_path):
        base = make_base()
        assert base.get_submodule_url_map(tmp_path) == {}

    def test_parses_paths_and_urls(self, tmp_path):
        (tmp_path / ".gitmodules").write_text(textwrap.dedent("""\
            [submodule "external/hip"]
                path = external/hip
                url = https://github.com/ROCm/hip.git
            [submodule "external/clr"]
                path = external/clr
                url = https://github.com/ROCm/clr.git
        """))
        base = make_base()
        url_map = base.get_submodule_url_map(tmp_path)
        assert url_map["external/hip"] == "https://github.com/ROCm/hip.git"
        assert url_map["external/clr"] == "https://github.com/ROCm/clr.git"

    def test_missing_url_entry_skipped(self, tmp_path):
        (tmp_path / ".gitmodules").write_text(textwrap.dedent("""\
            [submodule "external/hip"]
                path = external/hip
        """))
        base = make_base()
        assert "external/hip" not in base.get_submodule_url_map(tmp_path)


# ---------------------------------------------------------------------------
# RockBase.build_plan — submodule parsing logic
# ---------------------------------------------------------------------------

class TestBuildPlanSubmoduleParsing:
    """Test the submodule-filtering logic inside build_plan without a real clone."""

    def _run_build_plan(self, tmp_path, submodule_status, url_map, exclude_list=()):
        """Drive build_plan with fully mocked git operations."""
        base = make_base(exclude_list=list(exclude_list))
        base.cache_root = tmp_path

        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()

        with patch.object(base, "_prepare_clone", return_value=clone_dir), \
             patch.object(base, "_checkout_and_update_submodules"), \
             patch.object(
                 base, "run_command_output",
                 return_value="\n".join(submodule_status),
             ), \
             patch.object(base, "get_submodule_url_map", return_value=url_map):
            return base.build_plan()

    def test_rocm_submodule_included(self, tmp_path):
        plan = self._run_build_plan(
            tmp_path,
            submodule_status=[f" {'b'*40} external/hip (v6.0)"],
            url_map={"external/hip": "https://github.com/ROCm/hip.git"},
        )
        assert "hip" in plan
        assert "TheRock" in plan

    def test_non_rocm_submodule_excluded(self, tmp_path):
        plan = self._run_build_plan(
            tmp_path,
            submodule_status=[f" {'b'*40} external/llvm (v17)"],
            url_map={"external/llvm": "https://github.com/llvm/llvm-project.git"},
        )
        assert "llvm-project" not in plan

    def test_excluded_submodule_skipped(self, tmp_path):
        plan = self._run_build_plan(
            tmp_path,
            submodule_status=[f" {'b'*40} external/hip (v6.0)"],
            url_map={"external/hip": "https://github.com/ROCm/hip.git"},
            exclude_list=["hip"],
        )
        assert "hip" not in plan

    def test_missing_url_in_map_skipped(self, tmp_path):
        plan = self._run_build_plan(
            tmp_path,
            submodule_status=[f" {'b'*40} external/hip (v6.0)"],
            url_map={},  # no entry for external/hip
        )
        assert "hip" not in plan

    def test_therock_always_in_plan(self, tmp_path):
        plan = self._run_build_plan(
            tmp_path,
            submodule_status=[],
            url_map={},
        )
        assert "TheRock" in plan
        assert plan["TheRock"].commit == _FAKE_COMMIT

    def test_sha_prefix_chars_stripped(self, tmp_path):
        """Leading -, + in git submodule status output are stripped from the SHA."""
        plan = self._run_build_plan(
            tmp_path,
            submodule_status=[f"-{'b'*40} external/hip"],
            url_map={"external/hip": "https://github.com/ROCm/hip.git"},
        )
        assert plan["hip"].commit == "b" * 40


# ---------------------------------------------------------------------------
# RockBase.run_command — streaming and buffered modes
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_buffered_success_logs_stdout(self, tmp_path):
        base = make_base()
        logged = []
        base._logger.info = lambda msg, *a: logged.append(msg % a if a else msg)

        result = MagicMock()
        result.stdout = b"hello stdout"
        result.stderr = b""
        with patch("subprocess.run", return_value=result):
            base.run_command(["echo", "hi"], cwd=tmp_path)

        assert any("hello stdout" in m for m in logged)

    def test_buffered_failure_raises(self, tmp_path):
        base = make_base()
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                base.run_command(["git", "fail"], cwd=tmp_path)

    def test_stream_success(self, tmp_path):
        base = make_base()
        proc = MagicMock()
        proc.stdout = iter(["line1\n", "line2\n"])
        proc.wait.return_value = 0
        with patch("subprocess.Popen", return_value=proc):
            base.run_command(["git", "fetch"], cwd=tmp_path, stream=True)

    def test_stream_nonzero_exit_raises(self, tmp_path):
        base = make_base()
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = 1
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(subprocess.CalledProcessError):
                base.run_command(["git", "fetch"], cwd=tmp_path, stream=True)

    def test_stream_timeout_kills_process(self, tmp_path):
        base = make_base()
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.side_effect = subprocess.TimeoutExpired("git", 10)
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(subprocess.TimeoutExpired):
                base.run_command(["git", "fetch"], cwd=tmp_path, stream=True, timeout=10)
        proc.kill.assert_called_once()
