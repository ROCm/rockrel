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

from scripts.discover_submodules import _collect_repos_from_clone, discover_repo_plan, main


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
# include-list matching against nested submodule paths (BKC-style selection)
# ---------------------------------------------------------------------------

_BKC_SUBMODULE_STATUS = """\
 aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111 compiler/amd-llvm (v1.0)
 bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222 debug-tools/rocgdb/source (v1.0)
 cccc3333cccc3333cccc3333cccc3333cccc3333 rocm-libraries (v1.0)
 dddd4444dddd4444dddd4444dddd4444dddd4444 rocm-systems (v1.0)
"""

_BKC_GITMODULES = textwrap.dedent("""\
    [submodule "compiler/amd-llvm"]
        path = compiler/amd-llvm
        url = https://github.com/ROCm/llvm-project.git
    [submodule "debug-tools/rocgdb/source"]
        path = debug-tools/rocgdb/source
        url = https://github.com/ROCm/rocgdb.git
    [submodule "rocm-libraries"]
        path = rocm-libraries
        url = https://github.com/ROCm/rocm-libraries.git
    [submodule "rocm-systems"]
        path = rocm-systems
        url = https://github.com/ROCm/rocm-systems.git
""")


def _make_bkc_clone_dir(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "TheRock"
    clone_dir.mkdir()
    (clone_dir / ".gitmodules").write_text(_BKC_GITMODULES)
    return clone_dir


class TestIncludeListBkcSelection:
    """Regression test for the exact BKC include list that previously
    matched only 2 of 4 intended repos: 'compiler/amd-llvm' and
    'debug-tools/rocgdb/source' were matched against Path(path).name
    ('amd-llvm', 'source') instead of the full submodule path, so passing
    the basenames silently dropped them."""

    def test_full_paths_select_all_four_repos(self, tmp_path):
        clone_dir = _make_bkc_clone_dir(tmp_path)
        include = {"compiler/amd-llvm", "debug-tools/rocgdb/source", "rocm-libraries", "rocm-systems"}
        with _patch_collect(clone_dir, _BKC_SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set(), include)
        assert "amd-llvm" in plan
        assert "source" in plan
        assert "rocm-libraries" in plan
        assert "rocm-systems" in plan

    def test_basenames_also_select_all_four_repos(self, tmp_path):
        clone_dir = _make_bkc_clone_dir(tmp_path)
        include = {"amd-llvm", "source", "rocm-libraries", "rocm-systems"}
        with _patch_collect(clone_dir, _BKC_SUBMODULE_STATUS):
            plan = _collect_repos_from_clone(clone_dir, "a" * 40, set(), include)
        assert "amd-llvm" in plan
        assert "source" in plan
        assert "rocm-libraries" in plan
        assert "rocm-systems" in plan

    def test_unmatched_include_entry_raises_instead_of_silently_dropping(self, tmp_path):
        clone_dir = _make_bkc_clone_dir(tmp_path)
        include = {"amd-llvm", "does-not-exist"}
        with _patch_collect(clone_dir, _BKC_SUBMODULE_STATUS):
            with pytest.raises(RuntimeError, match="does-not-exist"):
                _collect_repos_from_clone(clone_dir, "a" * 40, set(), include)


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


# ---------------------------------------------------------------------------
# main() with a relative --clone-dir (real git, no mocking of run_command)
# ---------------------------------------------------------------------------

class TestMainRelativeCloneDir:
    """Regression test: get_submodule_url_map builds gitmodules = repo_dir /
    ".gitmodules" and passes it to `git config --file`, while also setting
    cwd=repo_dir. If repo_dir is relative, git resolves that relative --file
    path against the already-changed cwd, doubling the path segment and
    failing to find .gitmodules at all. main() must resolve --clone-dir to
    an absolute path before this ever happens."""

    def test_relative_clone_dir_finds_all_submodules(self, tmp_path, monkeypatch, capsys):
        clone_dir = tmp_path / "TheRock"
        clone_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=clone_dir, check=True)
        subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=clone_dir, check=True)
        subprocess.run(["git", "config", "user.name", "a"], cwd=clone_dir, check=True)
        (clone_dir / ".gitmodules").write_text(_GITMODULES)
        (clone_dir / "dummy").write_text("x")
        subprocess.run(["git", "add", "."], cwd=clone_dir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=clone_dir, check=True)

        from scripts.discover_submodules import run_command as _original_run_command

        def side_effect(args, **kwargs):
            if "submodule" in args and "status" in args:
                return _SUBMODULE_STATUS
            return _original_run_command(args, **kwargs)

        monkeypatch.chdir(tmp_path)
        with patch("scripts.discover_submodules.run_command", side_effect=side_effect):
            rc = main(["--clone-dir", "TheRock", "--commit-ref", "a" * 40])

        assert rc == 0
        out = capsys.readouterr().out
        assert "hip" in out
        assert "clr" in out
