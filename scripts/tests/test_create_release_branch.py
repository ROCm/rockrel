#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for create_release_branches.py and release_utils.py.

Covers:
- convert_to_ssh URL conversion
- ROCm org filtering logic
- get_submodule_url_map parsing
- execute_plan behaviour with mocked subprocess calls
- commitid validation
"""
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.release_utils import RepoInfo, convert_to_ssh, get_submodule_url_map
from scripts.create_release_branches import execute_plan


_FAKE_COMMIT = "a" * 40


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


# ---------------------------------------------------------------------------
# convert_to_ssh
# ---------------------------------------------------------------------------

class TestConvertToSsh:
    def test_https_converted(self):
        assert convert_to_ssh("https://github.com/ROCm/hip.git") == \
            "git@github.com:ROCm/hip.git"

    def test_https_without_dot_git(self):
        assert convert_to_ssh("https://github.com/ROCm/clr") == \
            "git@github.com:ROCm/clr"

    def test_ssh_url_passthrough(self):
        url = "git@github.com:ROCm/hip.git"
        assert convert_to_ssh(url) == url

    def test_non_github_url_passthrough(self):
        url = "https://gitlab.com/someorg/repo.git"
        assert convert_to_ssh(url) == url


# ---------------------------------------------------------------------------
# ROCm org filter logic
# ---------------------------------------------------------------------------

class TestRocmOrgFilter:
    @pytest.mark.parametrize("url,is_rocm", [
        ("https://github.com/ROCm/hip.git", True),
        ("https://github.com/rocm/hip.git", True),
        ("git@github.com:ROCm/clr.git", True),
        ("git@github.com:rocm/clr.git", True),
        ("https://github.com/llvm/llvm-project.git", False),
        ("https://github.com/other/repo.git", False),
        ("https://gitlab.com/ROCm/hip.git", False),
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
        assert get_submodule_url_map(tmp_path) == {}

    def test_parses_paths_and_urls(self, tmp_path):
        (tmp_path / ".gitmodules").write_text(textwrap.dedent("""\
            [submodule "external/hip"]
                path = external/hip
                url = https://github.com/ROCm/hip.git
            [submodule "external/clr"]
                path = external/clr
                url = https://github.com/ROCm/clr.git
        """))
        url_map = get_submodule_url_map(tmp_path)
        assert url_map["external/hip"] == "https://github.com/ROCm/hip.git"
        assert url_map["external/clr"] == "https://github.com/ROCm/clr.git"

    def test_missing_url_entry_skipped(self, tmp_path):
        (tmp_path / ".gitmodules").write_text(textwrap.dedent("""\
            [submodule "external/hip"]
                path = external/hip
        """))
        url_map = get_submodule_url_map(tmp_path)
        assert "external/hip" not in url_map


# ---------------------------------------------------------------------------
# execute_plan
# ---------------------------------------------------------------------------

class TestExecutePlan:
    def test_dry_run_does_not_push(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=False), \
             patch("scripts.create_release_branches.run_command") as mock_run:
            execute_plan(plan, "release/6.4", dry_run=True)
        for c in mock_run.call_args_list:
            assert "push" not in c.args[0], f"Unexpected push in dry-run: {c}"

    def test_existing_remote_branch_skipped(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=True), \
             patch("scripts.create_release_branches.run_command") as mock_run:
            execute_plan(plan, "release/6.4", dry_run=True)
        # No git checkout or push should be called
        for c in mock_run.call_args_list:
            assert "checkout" not in c.args[0]
            assert "push" not in c.args[0]

    def test_missing_repo_path_recorded_as_failure(self, tmp_path):
        plan = {
            "missing": RepoInfo(
                url="https://github.com/ROCm/missing.git",
                commit="c" * 40,
                path=tmp_path / "nonexistent",
            )
        }
        rc = execute_plan(plan, "release/6.4", dry_run=True)
        assert rc == 1

    def test_setup_remote_failure_recorded_not_raised(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch(
            "scripts.create_release_branches.setup_remote",
            side_effect=subprocess.CalledProcessError(1, "git remote"),
        ):
            rc = execute_plan(plan, "release/6.4", dry_run=True)
        assert rc == 1

    def test_create_branch_failure_recorded_not_raised(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=False), \
             patch(
                 "scripts.create_release_branches.run_command",
                 side_effect=subprocess.CalledProcessError(1, "git checkout"),
             ):
            rc = execute_plan(plan, "release/6.4", dry_run=True)
        assert rc == 1

    def test_successful_dry_run_returns_zero(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=False), \
             patch("scripts.create_release_branches.run_command"):
            rc = execute_plan(plan, "release/6.4", dry_run=True)
        assert rc == 0

    def test_no_dry_run_calls_push(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=False), \
             patch("scripts.create_release_branches.run_command") as mock_run:
            execute_plan(plan, "release/6.4", dry_run=False)
        push_calls = [c for c in mock_run.call_args_list if "push" in c.args[0]]
        assert len(push_calls) == 1


# ---------------------------------------------------------------------------
# commitid validation (tested via main())
# ---------------------------------------------------------------------------

class TestCommitidValidation:
    def test_valid_sha_accepted(self):
        from scripts.create_release_branches import main
        with patch("scripts.create_release_branches.build_plan", return_value={}):
            rc = main(["-B", "release/6.4", "-C", "a" * 40])
        assert rc == 0

    def test_short_sha_rejected(self):
        from scripts.create_release_branches import main
        rc = main(["-B", "release/6.4", "-C", "abc123"])
        assert rc == 1

    def test_uppercase_sha_rejected(self):
        from scripts.create_release_branches import main
        rc = main(["-B", "release/6.4", "-C", "A" * 40])
        assert rc == 1

    def test_non_hex_rejected(self):
        from scripts.create_release_branches import main
        rc = main(["-B", "release/6.4", "-C", "z" * 40])
        assert rc == 1
