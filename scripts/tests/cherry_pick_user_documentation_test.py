# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""User-facing documentation contracts for the packaged cherry-pick CLI."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "cherry-pick-automation" / "README.md"
CLI = ROOT / "skills" / "rocm-cherry-pick" / "scripts" / "rocm_cherry_pick.py"


def test_user_readme_covers_local_cli_and_agent_skill() -> None:
    """Keep the primary guide complete enough for a fresh engineer."""

    text = GUIDE.read_text(encoding="utf-8")
    required = (
        "# ROCm cherry-pick user guide",
        "## Use the local CLI",
        "## Use the agent skill",
        "developer-central.amd.com/settings/api-tokens",
        "read:evidence",
        "auth status",
        "auth login",
        " plan \\",
        " materialize \\",
        "draft_planned",
        "$rocm-cherry-pick",
        "no remote writes",
    )
    for phrase in required:
        assert phrase in text, f"missing user-guide contract: {phrase}"


def test_user_readme_local_links_resolve() -> None:
    """Reject broken relative Markdown links in the primary guide."""

    text = GUIDE.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if "://" in target or target.startswith("#"):
            continue
        relative = target.split("#", 1)[0]
        assert (GUIDE.parent / relative).resolve().exists(), target


def test_packaged_cli_help_matches_documented_surface() -> None:
    """Prove the documented commands exist and retain the no-write boundary."""

    completed = subprocess.run(
        [sys.executable, str(CLI), "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for command in ("auth", "plan", "materialize"):
        assert command in completed.stdout
