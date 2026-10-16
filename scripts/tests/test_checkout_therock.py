# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for checkout_therock.py.

Covers:
- _ensure_clone: clone, fetch, force-clone, invalid cache dir
- _ensure_clean_worktree: dirty vs clean worktree
- update_submodules: fetch_sources.py invocation and failure propagation
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.checkout_therock import (
    _ensure_clean_worktree,
    _ensure_clone,
    update_submodules,
)


# ---------------------------------------------------------------------------
# _ensure_clone
# ---------------------------------------------------------------------------

class TestEnsureClone:
    def test_clones_when_dir_missing(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        with patch("scripts.checkout_therock.run_command") as mock_run:
            _ensure_clone(clone_dir, tmp_path, force_clone=False)
        mock_run.assert_called_once()
        assert "clone" in mock_run.call_args[0][0]

    def test_fetches_when_valid_clone_exists(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        (clone_dir / ".git").mkdir()
        with patch("scripts.checkout_therock.run_command", return_value="https://github.com/ROCm/TheRock.git") as mock_run:
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
        with patch("scripts.checkout_therock.run_command") as mock_run, \
             patch("scripts.checkout_therock.shutil.rmtree") as mock_rmtree:
            _ensure_clone(clone_dir, tmp_path, force_clone=True)
        mock_rmtree.assert_called_once_with(clone_dir)
        assert any("clone" in c[0][0] for c in mock_run.call_args_list)

    def test_raises_when_existing_remote_not_therock(self, tmp_path):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        (clone_dir / ".git").mkdir()
        with patch("scripts.checkout_therock.run_command", return_value="https://github.com/other/repo.git"):
            with pytest.raises(RuntimeError, match="does not look like TheRock"):
                _ensure_clone(clone_dir, tmp_path, force_clone=False)


# ---------------------------------------------------------------------------
# _ensure_clean_worktree
# ---------------------------------------------------------------------------

class TestEnsureCleanWorktree:
    def test_raises_on_dirty_worktree(self, tmp_path):
        with patch("scripts.checkout_therock.run_command", return_value=" M some_file.py"):
            with pytest.raises(RuntimeError, match="uncommitted changes"):
                _ensure_clean_worktree(tmp_path)

    def test_passes_on_clean_worktree(self, tmp_path):
        with patch("scripts.checkout_therock.run_command", return_value=""):
            _ensure_clean_worktree(tmp_path)  # should not raise


# ---------------------------------------------------------------------------
# update_submodules
# ---------------------------------------------------------------------------

class TestUpdateSubmodules:
    def test_uses_fetch_sources_when_present(self, tmp_path):
        fetch_script = tmp_path / "build_tools" / "fetch_sources.py"
        fetch_script.parent.mkdir(parents=True)
        fetch_script.touch()
        with patch("scripts.checkout_therock.run_command") as mock_run:
            update_submodules(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "fetch_sources.py" in " ".join(str(a) for a in cmd)
        assert cmd[0] == sys.executable

    def test_raises_when_fetch_sources_missing(self, tmp_path):
        with pytest.raises(RuntimeError, match="fetch_sources.py not found"):
            update_submodules(tmp_path)

    def test_propagates_when_fetch_sources_fails(self, tmp_path):
        fetch_script = tmp_path / "build_tools" / "fetch_sources.py"
        fetch_script.parent.mkdir(parents=True)
        fetch_script.touch()
        with patch("scripts.checkout_therock.run_command",
                   side_effect=subprocess.CalledProcessError(1, "fetch_sources.py")):
            with pytest.raises(subprocess.CalledProcessError):
                update_submodules(tmp_path)
