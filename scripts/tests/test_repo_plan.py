# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for repo_plan.py.

Covers:
- _ensure_clone: clone, fetch, force-clone, invalid cache dir
- update_submodules: fetch_sources.py vs git submodule update fallback
- _collect_repos: ROCm filtering, exclude list, SHA prefix stripping
"""
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import call, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.repo_plan import (
    _collect_repos,
    _ensure_clone,
    update_submodules,
)


# ---------------------------------------------------------------------------
# _ensure_clone
# ---------------------------------------------------------------------------

class TestEnsureClone:
    def test_clones_when_dir_missing(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        with patch("scripts.repo_plan.run_command") as mock_run:
            _ensure_clone(clone_dir, tmp_path, force_clone=False)
        mock_run.assert_called_once()
        assert "clone" in mock_run.call_args[0][0]

    def test_fetches_when_valid_clone_exists(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        (clone_dir / ".git").mkdir()
        with patch("scripts.repo_plan.run_command", return_value="https://github.com/ROCm/TheRock.git") as mock_run:
            _ensure_clone(clone_dir, tmp_path, force_clone=False)
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert any("fetch" in cmd for cmd in cmds)
        assert not any("clone" in cmd for cmd in cmds)

    def test_raises_when_dir_exists_but_not_git_repo(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        with pytest.raises(RuntimeError, match="not a git repo"):
            _ensure_clone(clone_dir, tmp_path, force_clone=False)

    def test_force_clone_removes_and_reclones(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        with patch("scripts.repo_plan.run_command") as mock_run, \
             patch("scripts.repo_plan.shutil.rmtree") as mock_rmtree:
            _ensure_clone(clone_dir, tmp_path, force_clone=True)
        mock_rmtree.assert_called_once_with(clone_dir)
        assert any("clone" in c[0][0] for c in mock_run.call_args_list)

    def test_raises_when_existing_remote_not_therock(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        (clone_dir / ".git").mkdir()
        with patch("scripts.repo_plan.run_command", return_value="https://github.com/other/repo.git"):
            with pytest.raises(RuntimeError, match="does not look like TheRock"):
                _ensure_clone(clone_dir, tmp_path, force_clone=False)


# ---------------------------------------------------------------------------
# update_submodules
# ---------------------------------------------------------------------------

class TestUpdateSubmodules:
    def test_uses_fetch_sources_when_present(self, tmp_path):
        fetch_script = tmp_path / "build_tools" / "fetch_sources.py"
        fetch_script.parent.mkdir(parents=True)
        fetch_script.touch()
        with patch("scripts.repo_plan.run_command") as mock_run:
            update_submodules(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "fetch_sources.py" in " ".join(str(a) for a in cmd)

    def test_falls_back_to_git_submodule_update(self, tmp_path):
        with patch("scripts.repo_plan.run_command") as mock_run:
            update_submodules(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "submodule" in cmd
        assert "update" in cmd

    def test_falls_back_when_fetch_sources_fails(self, tmp_path):
        fetch_script = tmp_path / "build_tools" / "fetch_sources.py"
        fetch_script.parent.mkdir(parents=True)
        fetch_script.touch()
        with patch("scripts.repo_plan.run_command",
                   side_effect=[subprocess.CalledProcessError(1, "python3"), None]) as mock_run:
            update_submodules(tmp_path)
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert any("submodule" in cmd for cmd in cmds)


# ---------------------------------------------------------------------------
# _collect_repos
# ---------------------------------------------------------------------------

_SUBMODULE_STATUS = """\
 abcd1234abcd1234abcd1234abcd1234abcd1234 external/hip (v1.0)
-beef5678beef5678beef5678beef5678beef5678 external/clr
+cafe9012cafe9012cafe9012cafe9012cafe9012 external/llvm-project (heads/main)
"""

_GITMODULES = textwrap.dedent("""\
    [submodule "external/hip"]
        path = external/hip
        url = https://github.com/ROCm/hip.git
    [submodule "external/clr"]
        path = external/clr
        url = https://github.com/ROCm/clr.git
    [submodule "external/llvm-project"]
        path = external/llvm-project
        url = https://github.com/llvm/llvm-project.git
""")


def _make_clone_dir(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "TheRock"
    clone_dir.mkdir()
    (clone_dir / ".gitmodules").write_text(_GITMODULES)
    return clone_dir


def _patch_collect(clone_dir: Path, status: str):
    """Patch run_command so _collect_repos gets real .gitmodules data
    but git submodule status returns our fake output."""
    from scripts.repo_plan import run_command as _original_run_command

    def side_effect(args, **kwargs):
        if "submodule" in args and "status" in args:
            return status
        return _original_run_command(args, **kwargs)

    return patch("scripts.repo_plan.run_command", side_effect=side_effect)


class TestCollectRepos:
    def test_rocm_repos_included(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos(clone_dir, "a" * 40, set())
        assert "hip" in plan
        assert "clr" in plan

    def test_non_rocm_repos_excluded(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos(clone_dir, "a" * 40, set())
        assert "llvm-project" not in plan

    def test_sha_prefix_stripped(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos(clone_dir, "a" * 40, set())
        assert plan["hip"].commit == "abcd1234abcd1234abcd1234abcd1234abcd1234"
        assert plan["clr"].commit == "beef5678beef5678beef5678beef5678beef5678"

    def test_exclude_list_honoured(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos(clone_dir, "a" * 40, {"hip"})
        assert "hip" not in plan
        assert "clr" in plan

    def test_therock_always_in_plan(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        commitid = "a" * 40
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos(clone_dir, commitid, set())
        assert "TheRock" in plan
        assert plan["TheRock"].commit == commitid

    def test_submodule_status_failure_raises(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with patch(
            "scripts.repo_plan.run_command",
            side_effect=subprocess.CalledProcessError(1, "git submodule"),
        ):
            with pytest.raises(RuntimeError, match="Failed to read submodule status"):
                _collect_repos(clone_dir, "a" * 40, set())
