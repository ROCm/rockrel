#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Location-independent launcher for the packaged ROCm cherry-pick CLI."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

from cherry_pick.marketplace_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
