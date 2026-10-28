#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for create_release_branch.py.

Covers:
- convert_to_ssh URL conversion
- ROCm org filtering logic
- get_submodule_url_map parsing
- execute_plan behaviour with mocked subprocess calls
"""
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.create_release_branch import RepoInfo, RockBranchingAutomation


_FAKE_COMMIT = "a" * 40


def make_automation(**kwargs) -> RockBranchingAutomation:
    defaults = dict(
        branch_name="release/6.4",
        commitid=_FAKE_COMMIT,
        dry_run=True,
        exclude_list=[],
        force_clone=False,
        cache_dir=None,
    )
    defaults.update(kwargs)
    return RockBranchingAutomation(SimpleNamespace(**defaults))


# ---------------------------------------------------------------------------
# convert_to_ssh
# ---------------------------------------------------------------------------

class TestConvertToSsh:
    def test_https_converted(self):
        auto = make_automation()
        assert auto.convert_to_ssh("https://github.com/ROCm/hip.git") == \
            "git@github.com:ROCm/hip.git"

    def test_https_without_dot_git(self):
        auto = make_automation()
        assert auto.convert_to_ssh("https://github.com/ROCm/clr") == \
            "git@github.com:ROCm/clr"

    def test_ssh_url_passthrough(self):
        auto = make_automation()
        url = "git@github.com:ROCm/hip.git"
        assert auto.convert_to_ssh(url) == url

    def test_non_github_url_passthrough(self):
        auto = make_automation()
        url = "https://gitlab.com/someorg/repo.git"
        assert auto.convert_to_ssh(url) == url


# ---------------------------------------------------------------------------
# ROCm org filter logic (mirrors the logic in build_plan)
# ---------------------------------------------------------------------------

class TestRocmOrgFilter:
    @pytest.mark.parametrize("url,is_rocm", [
        ("https://github.com/ROCm/hip.git", True),
        ("https://github.com/rocm/hip.git", True),   # case-insensitive
        ("git@github.com:ROCm/clr.git", True),
        ("git@github.com:rocm/clr.git", True),
        ("https://github.com/llvm/llvm-project.git", False),
        ("https://github.com/other/repo.git", False),
        ("https://gitlab.com/ROCm/hip.git", False),  # wrong host
    ])
    def test_rocm_org_detection(self, url, is_rocm):
        url_lower = url.lower()
        result = (
            "github.com/rocm/" in url_lower
            or "github.com:rocm/" in url_lower
        )
        assert result == is_rocm


# ---------------------------------------------------------------------------
# get_submodule_url_map
# ---------------------------------------------------------------------------

class TestGetSubmoduleUrlMap:
    def test_no_gitmodules_returns_empty(self, tmp_path):
        auto = make_automation()
        assert auto.get_submodule_url_map(tmp_path) == {}

    def test_parses_paths_and_urls(self, tmp_path):
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(textwrap.dedent("""\
            [submodule "external/hip"]
                path = external/hip
                url = https://github.com/ROCm/hip.git
            [submodule "external/clr"]
                path = external/clr
                url = https://github.com/ROCm/clr.git
        """))

        auto = make_automation()
        url_map = auto.get_submodule_url_map(tmp_path)

        assert url_map["external/hip"] == "https://github.com/ROCm/hip.git"
        assert url_map["external/clr"] == "https://github.com/ROCm/clr.git"

    def test_missing_url_entry_skipped(self, tmp_path):
        # A path entry with no corresponding URL entry should be silently skipped.
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(textwrap.dedent("""\
            [submodule "external/hip"]
                path = external/hip
        """))

        auto = make_automation()
        url_map = auto.get_submodule_url_map(tmp_path)
        assert "external/hip" not in url_map


# ---------------------------------------------------------------------------
# execute_plan — integration tests with mocked subprocess
# ---------------------------------------------------------------------------

def _make_plan(tmp_path: Path) -> dict[str, RepoInfo]:
    repo_dir = tmp_path / "hip"
    repo_dir.mkdir()
    return {
        "hip": RepoInfo(
            url="https://github.com/ROCm/hip.git",
            commit="b" * 40,
            path=repo_dir,
        )
    }


class TestExecutePlan:
    def test_dry_run_does_not_push(self, tmp_path):
        auto = make_automation(dry_run=True)
        plan = _make_plan(tmp_path)

        with patch.object(auto, "_setup_remote"), \
             patch.object(auto, "_remote_branch_exists", return_value=False), \
             patch.object(auto, "_create_branch"), \
             patch.object(auto, "run_command") as mock_run:
            auto.execute_plan(plan)

        for c in mock_run.call_args_list:
            assert "push" not in c.args[0], \
                f"Unexpected push call in dry-run: {c}"

    def test_existing_remote_branch_goes_to_skipped_not_failed(self, tmp_path):
        auto = make_automation(dry_run=True)
        plan = _make_plan(tmp_path)

        with patch.object(auto, "_setup_remote"), \
             patch.object(auto, "_remote_branch_exists", return_value=True), \
             patch.object(auto, "_create_branch") as mock_create, \
             patch.object(auto, "_push_branch") as mock_push:
            auto.execute_plan(plan)

        mock_create.assert_not_called()
        mock_push.assert_not_called()

    def test_missing_repo_path_recorded_as_failure(self, tmp_path):
        auto = make_automation(dry_run=True)
        plan = {
            "missing": RepoInfo(
                url="https://github.com/ROCm/missing.git",
                commit="c" * 40,
                path=tmp_path / "nonexistent",
            )
        }
        # Must not raise; logs the failure and moves on.
        auto.execute_plan(plan)

    def test_setup_remote_failure_recorded_not_raised(self, tmp_path):
        auto = make_automation(dry_run=True)
        plan = _make_plan(tmp_path)

        with patch.object(
            auto, "_setup_remote",
            side_effect=subprocess.CalledProcessError(1, "git remote"),
        ):
            auto.execute_plan(plan)  # must not raise

    def test_create_branch_failure_recorded_not_raised(self, tmp_path):
        auto = make_automation(dry_run=True)
        plan = _make_plan(tmp_path)

        with patch.object(auto, "_setup_remote"), \
             patch.object(auto, "_remote_branch_exists", return_value=False), \
             patch.object(
                 auto, "_create_branch",
                 side_effect=subprocess.CalledProcessError(1, "git checkout"),
             ):
            auto.execute_plan(plan)  # must not raise

    def test_successful_dry_run_calls_create_branch(self, tmp_path):
        auto = make_automation(dry_run=True)
        plan = _make_plan(tmp_path)

        with patch.object(auto, "_setup_remote"), \
             patch.object(auto, "_remote_branch_exists", return_value=False), \
             patch.object(auto, "_create_branch") as mock_create, \
             patch.object(auto, "_push_branch") as mock_push:
            auto.execute_plan(plan)

        mock_create.assert_called_once_with("b" * 40, plan["hip"].path)
        mock_push.assert_called_once_with("hip", plan["hip"].path)

    def test_no_dry_run_calls_push(self, tmp_path):
        auto = make_automation(dry_run=False)
        plan = _make_plan(tmp_path)

        with patch.object(auto, "_setup_remote"), \
             patch.object(auto, "_remote_branch_exists", return_value=False), \
             patch.object(auto, "_create_branch"), \
             patch.object(auto, "run_command") as mock_run:
            auto.execute_plan(plan)

        push_calls = [
            c for c in mock_run.call_args_list
            if "push" in c.args[0]
        ]
        assert len(push_calls) == 1


# ---------------------------------------------------------------------------
# commitid validation
# ---------------------------------------------------------------------------

class TestCommitidValidation:
    def test_valid_sha_accepted(self):
        make_automation(commitid="a" * 40)  # should not raise

    def test_short_sha_rejected(self):
        with pytest.raises(SystemExit):
            make_automation(commitid="abc123")

    def test_uppercase_sha_rejected(self):
        with pytest.raises(SystemExit):
            make_automation(commitid="A" * 40)

    def test_non_hex_rejected(self):
        with pytest.raises(SystemExit):
            make_automation(commitid="z" * 40)
