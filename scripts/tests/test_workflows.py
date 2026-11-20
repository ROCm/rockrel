# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""
Checks on .github/workflows that actionlint does not make.

Covers:
- boolean workflow inputs compared with string literals
"""
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).parent.parent.parent / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.y*ml"))

STRING_LITERAL = r"'(?:[^']|'')*'"


def boolean_inputs(workflow: dict) -> set[str]:
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, dict):
        return set()
    names = set()
    for trigger in ("workflow_dispatch", "workflow_call"):
        inputs = (triggers.get(trigger) or {}).get("inputs") or {}
        names.update(n for n, spec in inputs.items() if spec.get("type") == "boolean")
    return names


def boolean_string_comparisons(text: str) -> list[str]:
    found = []
    for name in sorted(boolean_inputs(yaml.safe_load(text))):
        operand = rf"(?<![\w.])inputs\.{re.escape(name)}\b"
        pattern = (
            rf"{operand}\s*[!=]=\s*{STRING_LITERAL}"
            rf"|{STRING_LITERAL}\s*[!=]=\s*{operand}"
        )
        found += [m.group(0) for m in re.finditer(pattern, text)]
    return found


WORKFLOW_TEMPLATE = """
on:
  workflow_dispatch:
    inputs:
      build: {type: boolean, default: true}
      ref: {type: string, default: ""}
jobs:
  job:
    runs-on: ubuntu-24.04
    steps:
      - run: echo "%s"
"""


class TestBooleanStringComparisons:
    @pytest.mark.parametrize(
        "expression",
        [
            "${{ inputs.build == '' && true || inputs.build }}",
            "${{ inputs.build != 'false' }}",
            "${{ 'true' == inputs.build }}",
        ],
    )
    def test_flags_boolean_input_against_string(self, expression):
        assert boolean_string_comparisons(WORKFLOW_TEMPLATE % expression)

    @pytest.mark.parametrize(
        "expression",
        [
            "${{ inputs.build || github.event_name == 'schedule' }}",
            "${{ inputs.ref == '' }}",
            "${{ github.event.inputs.build == 'true' }}",
            "${{ inputs.build_other == '' }}",
        ],
    )
    def test_allows_other_comparisons(self, expression):
        assert not boolean_string_comparisons(WORKFLOW_TEMPLATE % expression)

    @pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
    def test_workflow(self, path):
        # Mixed-type comparisons coerce both sides to numbers, so a dispatched
        # false equals '' and `inputs.x == ''` cannot tell false from unset.
        found = boolean_string_comparisons(path.read_text(encoding="utf-8"))
        assert not found
