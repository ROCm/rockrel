# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Tests for check_github_permissions.py.

Covers:
- check_permissions pass/fail logic
"""
import urllib.error
from unittest.mock import patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.check_github_permissions import check_permissions

# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------

def _repo_map() -> dict[str, str]:
    return {
        "hip": "https://github.com/ROCm/hip.git",
        "clr": "https://github.com/ROCm/clr.git",
    }

class TestCheckPermissions:
    def _api_response(self, push: bool, admin: bool = False) -> dict:
        return {"permissions": {"push": push, "admin": admin}}

    def test_all_pass_returns_zero(self, capsys):
        with patch(
            "scripts.check_github_permissions._api_request",
            return_value=self._api_response(push=True),
        ):
            rc = check_permissions("token", _repo_map())
        assert rc == 0
        assert "All permission checks passed" in capsys.readouterr().out

    def test_admin_access_also_passes(self, capsys):
        with patch(
            "scripts.check_github_permissions._api_request",
            return_value=self._api_response(push=False, admin=True),
        ):
            rc = check_permissions("token", _repo_map())
        assert rc == 0

    def test_insufficient_permissions_returns_one(self, capsys):
        with patch(
            "scripts.check_github_permissions._api_request",
            return_value=self._api_response(push=False, admin=False),
        ):
            rc = check_permissions("token", _repo_map())
        assert rc == 1
        assert "Permission check failed" in capsys.readouterr().out

    def test_http_403_recorded_as_failure(self, capsys):
        with patch(
            "scripts.check_github_permissions._api_request",
            side_effect=urllib.error.HTTPError(None, 403, "Forbidden", {}, None),
        ):
            rc = check_permissions("token", {"hip": "https://github.com/ROCm/hip.git"})
        assert rc == 1
        assert "403" in capsys.readouterr().out

    def test_http_404_recorded_as_failure(self, capsys):
        with patch(
            "scripts.check_github_permissions._api_request",
            side_effect=urllib.error.HTTPError(None, 404, "Not Found", {}, None),
        ):
            rc = check_permissions("token", {"hip": "https://github.com/ROCm/hip.git"})
        assert rc == 1

    def test_invalid_url_recorded_as_failure(self, capsys):
        rc = check_permissions("token", {"bad": "https://gitlab.com/org/repo.git"})
        assert rc == 1
        assert "URL parse error" in capsys.readouterr().out
