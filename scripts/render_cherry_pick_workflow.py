#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Render the source-repository caller with one immutable rockrel revision."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PLACEHOLDER = "ROCKREL_AUTOMATION_SHA"
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class RenderError(ValueError):
    """The template or requested automation revision is unsafe."""


def render_workflow(template: str | Path, sha: str) -> str:
    if FULL_SHA_RE.fullmatch(sha) is None:
        raise RenderError("automation SHA must be a full lowercase 40-character SHA")
    text = Path(template).read_text()
    if PLACEHOLDER not in text:
        raise RenderError(f"template does not contain {PLACEHOLDER} placeholder")
    rendered = text.replace(PLACEHOLDER, sha)
    if PLACEHOLDER in rendered:
        raise RenderError("template placeholder was not fully replaced")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).parents[1] / "templates/cherry_pick_request.yml",
    )
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rendered = render_workflow(args.template, args.sha)
    args.output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
