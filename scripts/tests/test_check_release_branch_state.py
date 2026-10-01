# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for check_release_branch_state.py.

Covers:
- check_ssh_auth: success, failure, timeout
- check_repo: reachable/branch-exists, timeout, unreachable
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.check_release_branch_state import check_repo, check_ssh_auth


# ---------------------------------------------------------------------------
# check_ssh_auth
# ---------------------------------------------------------------------------

class TestCheckSshAuth:
    def test_returns_true_on_success(self, capsys):
        mock_result = MagicMock()
        mock_result.stdout = b"Hi user! You've successfully authenticated"
        with patch("scripts.check_release_branch_state.subprocess.run", return_value=mock_result):
            result = check_ssh_auth()
        assert result is True
        assert "[OK]" in capsys.readouterr().out

    def test_returns_false_on_auth_failure(self, capsys):
        mock_result = MagicMock()
        mock_result.stdout = b"Permission denied (publickey)."
        with patch("scripts.check_release_branch_state.subprocess.run", return_value=mock_result):
            result = check_ssh_auth()
        assert result is False
        assert "[FAIL]" in capsys.readouterr().out

    def test_returns_false_on_timeout(self, capsys):
        with patch(
            "scripts.check_release_branch_state.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ssh", 15),
        ):
            result = check_ssh_auth()
        assert result is False
        assert "[FAIL]" in capsys.readouterr().out

    def test_returns_false_when_ssh_not_found(self, capsys):
        with patch(
            "scripts.check_release_branch_state.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = check_ssh_auth()
        assert result is False
        assert "[FAIL]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# check_repo
# ---------------------------------------------------------------------------

class TestCheckRepo:
    def test_branch_does_not_exist(self):
        with patch("scripts.check_release_branch_state.run_command", return_value=""):
            result = check_repo("hip", "https://github.com/ROCm/hip.git", "release/6.4")
        assert result["reachable"] is True
        assert result["branch_exists"] is False
        assert result["error"] is None

    def test_branch_exists(self):
        with patch(
            "scripts.check_release_branch_state.run_command",
            return_value="abc123\trefs/heads/release/6.4",
        ):
            result = check_repo("hip", "https://github.com/ROCm/hip.git", "release/6.4")
        assert result["reachable"] is True
        assert result["branch_exists"] is True
        assert result["error"] is None

    def test_timeout_recorded_as_error(self):
        with patch(
            "scripts.check_release_branch_state.run_command",
            side_effect=subprocess.TimeoutExpired("git", 60),
        ):
            result = check_repo("hip", "https://github.com/ROCm/hip.git", "release/6.4")
        assert result["reachable"] is False
        assert "Timed out" in result["error"]

    def test_unreachable_remote_recorded_as_error(self):
        with patch(
            "scripts.check_release_branch_state.run_command",
            side_effect=subprocess.CalledProcessError(128, "git ls-remote"),
        ):
            result = check_repo("hip", "https://github.com/ROCm/hip.git", "release/6.4")
        assert result["reachable"] is False
        assert "Remote unreachable" in result["error"]

    def test_https_url_converted_to_ssh(self):
        with patch("scripts.check_release_branch_state.run_command", return_value="") as mock_run:
            check_repo("hip", "https://github.com/ROCm/hip.git", "release/6.4")
        cmd = mock_run.call_args[0][0]
        assert "git@github.com:ROCm/hip.git" in cmd
