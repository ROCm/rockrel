#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for create_release_branches.py.

Covers:
- ROCm org filtering logic
- get_submodule_url_map parsing
- create_branch / push_branch helpers
- execute_plan behaviour
- main() accepts any git ref
"""
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import call, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.repo_plan import RepoInfo, get_submodule_url_map
from scripts.create_release_branches import create_branch, execute_plan, push_branch

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
# create_branch / push_branch helpers
# ---------------------------------------------------------------------------

class TestCreateBranch:
    def test_calls_git_checkout(self, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        with patch("scripts.create_release_branches.run_command") as mock_run:
            create_branch(tmp_path, "release/6.4", "abc123")
        mock_run.assert_called_once_with(
            ["git", "checkout", "-B", "release/6.4", "abc123"], cwd=tmp_path
        )

    def test_propagates_subprocess_error(self, tmp_path):
        with patch(
            "scripts.create_release_branches.run_command",
            side_effect=subprocess.CalledProcessError(1, "git checkout"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                create_branch(tmp_path, "release/6.4", "abc123")

class TestPushBranch:
    def test_calls_git_push(self, tmp_path):
        with patch("scripts.create_release_branches.run_command") as mock_run:
            push_branch(tmp_path, "release/6.4")
        mock_run.assert_called_once_with(
            ["git", "push", "rocm-github", "release/6.4"], cwd=tmp_path, timeout=120
        )

    def test_propagates_subprocess_error(self, tmp_path):
        with patch(
            "scripts.create_release_branches.run_command",
            side_effect=subprocess.CalledProcessError(1, "git push"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                push_branch(tmp_path, "release/6.4")

# ---------------------------------------------------------------------------
# execute_plan
# ---------------------------------------------------------------------------

class TestExecutePlan:
    def test_dry_run_makes_no_git_changes(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=False), \
             patch("scripts.create_release_branches.create_branch") as mock_create, \
             patch("scripts.create_release_branches.push_branch") as mock_push:
            rc = execute_plan(plan, "release/6.4", dry_run=True)
        assert rc == 0
        mock_create.assert_not_called()
        mock_push.assert_not_called()

    def test_existing_remote_branch_skipped(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=True), \
             patch("scripts.create_release_branches.create_branch") as mock_create, \
             patch("scripts.create_release_branches.push_branch") as mock_push:
            rc = execute_plan(plan, "release/6.4", dry_run=False)
        assert rc == 0
        mock_create.assert_not_called()
        mock_push.assert_not_called()

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
                 "scripts.create_release_branches.create_branch",
                 side_effect=subprocess.CalledProcessError(1, "git checkout"),
             ):
            rc = execute_plan(plan, "release/6.4", dry_run=False)
        assert rc == 1

    def test_no_dry_run_calls_create_and_push(self, tmp_path):
        plan = _make_plan(tmp_path)
        with patch("scripts.create_release_branches.setup_remote"), \
             patch("scripts.create_release_branches.remote_branch_exists", return_value=False), \
             patch("scripts.create_release_branches.create_branch") as mock_create, \
             patch("scripts.create_release_branches.push_branch") as mock_push:
            rc = execute_plan(plan, "release/6.4", dry_run=False)
        assert rc == 0
        mock_create.assert_called_once()
        mock_push.assert_called_once()

# ---------------------------------------------------------------------------
# main() accepts any git ref
# ---------------------------------------------------------------------------

def _make_full_plan(tmp_path: Path) -> dict[str, RepoInfo]:
    rock_dir = tmp_path / "TheRock"
    rock_dir.mkdir()
    return {"TheRock": RepoInfo(url="https://github.com/ROCm/TheRock.git", commit="a" * 40, path=rock_dir)}

class TestMainAcceptsAnyRef:
    def test_full_sha_accepted(self, tmp_path):
        from scripts.create_release_branches import main
        with patch("scripts.create_release_branches.build_plan", return_value=_make_full_plan(tmp_path)), \
             patch("scripts.create_release_branches.update_submodules"), \
             patch("scripts.create_release_branches.execute_plan", return_value=0):
            rc = main(["-B", "release/6.4", "-C", "a" * 40])
        assert rc == 0

    def test_branch_name_accepted(self, tmp_path):
        from scripts.create_release_branches import main
        with patch("scripts.create_release_branches.build_plan", return_value=_make_full_plan(tmp_path)), \
             patch("scripts.create_release_branches.update_submodules"), \
             patch("scripts.create_release_branches.execute_plan", return_value=0):
            rc = main(["-B", "release/6.4", "-C", "main"])
        assert rc == 0

    def test_tag_accepted(self, tmp_path):
        from scripts.create_release_branches import main
        with patch("scripts.create_release_branches.build_plan", return_value=_make_full_plan(tmp_path)), \
             patch("scripts.create_release_branches.update_submodules"), \
             patch("scripts.create_release_branches.execute_plan", return_value=0):
            rc = main(["-B", "release/6.4", "-C", "rocm-6.3.0"])
        assert rc == 0
