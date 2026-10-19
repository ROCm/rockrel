# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for discover_submodules.py.

Covers:
- get_submodule_url_map: .gitmodules parsing
- _collect_repos_from_clone: ROCm filtering, exclude/include list, SHA prefix stripping
- discover_repo_plan: read-only discovery over an already-checked-out clone
"""
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.discover_submodules import _collect_repos_from_clone, discover_repo_plan


# ---------------------------------------------------------------------------
# _collect_repos_from_clone
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
    """Patch run_command so _collect_repos_from_clone gets real .gitmodules data
    but git submodule status returns our fake output."""
    from scripts.discover_submodules import run_command as _original_run_command

    def side_effect(args, **kwargs):
        if "submodule" in args and "status" in args:
            return status
        return _original_run_command(args, **kwargs)

    return patch("scripts.discover_submodules.run_command", side_effect=side_effect)


class TestCollectRepos:
    def test_rocm_repos_included(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set())
        assert "hip" in plan
        assert "clr" in plan

    def test_non_rocm_repos_excluded(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set())
        assert "llvm-project" not in plan

    def test_sha_prefix_stripped(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set())
        assert plan["hip"].commit == "abcd1234abcd1234abcd1234abcd1234abcd1234"
        assert plan["clr"].commit == "beef5678beef5678beef5678beef5678beef5678"

    def test_exclude_list_honoured(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, {"hip"})
        assert "hip" not in plan
        assert "clr" in plan

    def test_therock_always_in_plan(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        commit_ref = "a" * 40
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, commit_ref, set())
        assert "TheRock" in plan
        assert plan["TheRock"].commit == commit_ref

    def test_submodule_status_failure_raises(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with patch(
            "scripts.discover_submodules.run_command",
            side_effect=subprocess.CalledProcessError(1, "git submodule"),
        ):
            with pytest.raises(RuntimeError, match="Failed to read submodule status"):
                _collect_repos_from_clone(clone_dir, "a" * 40, set())

    def test_include_list_limits_to_named_repos(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set(), {"hip"})
        assert "hip" in plan
        assert "clr" not in plan
        assert "llvm-project" not in plan
        assert "TheRock" not in plan

    def test_include_list_can_add_therock_back(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set(), {"hip", "TheRock"})
        assert "hip" in plan
        assert "TheRock" in plan

    def test_include_list_overrides_org_filter(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set(), {"llvm-project"})
        assert "llvm-project" in plan

    def test_exclude_list_applied_on_top_of_include_list(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, {"hip"}, {"hip", "clr"})
        assert "hip" not in plan
        assert "clr" in plan

    def test_exclude_list_can_remove_therock(self, tmp_path):
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, {"TheRock"})
        assert "TheRock" not in plan


# ---------------------------------------------------------------------------
# discover_repo_plan
# ---------------------------------------------------------------------------

class TestDiscoverRepoPlan:
    def test_is_read_only(self, tmp_path):
        """discover_repo_plan must not clone, checkout, or reset — only inspect."""
        clone_dir = _make_clone_dir(tmp_path)
        with _patch_collect(clone_dir, _SUBMODULE_STATUS):
            plan = discover_repo_plan(clone_dir, "a" * 40, set())
        assert "TheRock" in plan
        assert "hip" in plan
