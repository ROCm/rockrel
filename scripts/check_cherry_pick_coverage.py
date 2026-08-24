# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Enforce independent line and branch thresholds from coverage.py JSON."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO


class CoverageGateError(ValueError):
    """The coverage report or requested threshold is invalid."""


@dataclass(frozen=True)
class ModuleCoverageResult:
    """Record line and branch coverage for one critical module."""

    path: str
    line_percent: float
    branch_percent: float
    line_passed: bool
    branch_passed: bool

    @property
    def passed(self) -> bool:
        """Return whether every configured coverage threshold is satisfied."""

        return self.line_passed and self.branch_passed


@dataclass(frozen=True)
class CoverageGateResult:
    """Summarize repository-wide and critical-module coverage decisions."""

    covered_lines: int
    num_statements: int
    covered_branches: int
    num_branches: int
    line_percent: float
    branch_percent: float
    minimum_lines: float
    minimum_branches: float
    line_passed: bool
    branch_passed: bool
    module_results: tuple[ModuleCoverageResult, ...] = ()

    @property
    def passed(self) -> bool:
        """Return whether every configured coverage threshold is satisfied."""

        return (
            self.line_passed
            and self.branch_passed
            and all(item.passed for item in self.module_results)
        )

    def as_dict(self) -> dict[str, object]:
        """Serialize this coverage gate result into its stable dictionary contract."""

        return {
            "status": "passed" if self.passed else "failed",
            **asdict(self),
            "passed": self.passed,
        }


def _threshold(value: object, context: str) -> Decimal:
    """Validate and return one configured percentage threshold."""

    if isinstance(value, bool):
        raise CoverageGateError(f"{context} must be a number from 0 through 100")
    try:
        threshold = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CoverageGateError(
            f"{context} must be a number from 0 through 100"
        ) from exc
    if not threshold.is_finite() or threshold < 0 or threshold > 100:
        raise CoverageGateError(f"{context} must be a number from 0 through 100")
    return threshold


def _count(totals: Mapping[str, object], key: str) -> int:
    """Read a nonnegative coverage counter from the report payload."""

    value = totals.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CoverageGateError(f"coverage totals.{key} must be a non-negative integer")
    return value


def evaluate_coverage(
    payload: object,
    *,
    minimum_lines: float,
    minimum_branches: float,
    critical_modules: Sequence[str] = (),
    minimum_module_lines: float = 90.0,
    minimum_module_branches: float = 90.0,
) -> CoverageGateResult:
    """Validate one coverage.py JSON payload and evaluate both dimensions."""

    if not isinstance(payload, Mapping):
        raise CoverageGateError("coverage report must be an object")
    totals = payload.get("totals")
    if not isinstance(totals, Mapping):
        raise CoverageGateError("coverage report totals must be an object")

    covered_lines = _count(totals, "covered_lines")
    num_statements = _count(totals, "num_statements")
    covered_branches = _count(totals, "covered_branches")
    num_branches = _count(totals, "num_branches")
    if num_statements == 0 or covered_lines > num_statements:
        raise CoverageGateError("coverage line counts are impossible")
    if num_branches == 0 or covered_branches > num_branches:
        raise CoverageGateError("coverage branch counts are impossible")

    line_threshold = _threshold(minimum_lines, "minimum lines")
    branch_threshold = _threshold(minimum_branches, "minimum branches")
    module_line_threshold = _threshold(minimum_module_lines, "minimum module lines")
    module_branch_threshold = _threshold(
        minimum_module_branches, "minimum module branches"
    )
    line_passed = Decimal(covered_lines * 100) >= line_threshold * num_statements
    branch_passed = Decimal(covered_branches * 100) >= (branch_threshold * num_branches)
    files = payload.get("files")
    if critical_modules and not isinstance(files, Mapping):
        raise CoverageGateError("coverage report omitted critical module evidence")
    module_results: list[ModuleCoverageResult] = []
    for path in critical_modules:
        file_payload = files.get(path) if isinstance(files, Mapping) else None
        if not isinstance(file_payload, Mapping):
            raise CoverageGateError(
                f"coverage report omitted critical module evidence for {path}"
            )
        summary = file_payload.get("summary")
        if not isinstance(summary, Mapping):
            raise CoverageGateError(
                f"coverage report omitted critical module summary for {path}"
            )
        module_lines = _count(summary, "covered_lines")
        module_statements = _count(summary, "num_statements")
        module_covered_branches = _count(summary, "covered_branches")
        module_branches = _count(summary, "num_branches")
        if module_statements == 0 or module_lines > module_statements:
            raise CoverageGateError(
                f"critical module line counts are impossible for {path}"
            )
        if module_covered_branches > module_branches:
            raise CoverageGateError(
                f"critical module branch counts are impossible for {path}"
            )
        module_line_passed = (
            Decimal(module_lines * 100) >= module_line_threshold * module_statements
        )
        module_branch_passed = module_branches == 0 or (
            Decimal(module_covered_branches * 100)
            >= module_branch_threshold * module_branches
        )
        module_results.append(
            ModuleCoverageResult(
                path=path,
                line_percent=round(module_lines * 100 / module_statements, 4),
                branch_percent=(
                    round(module_covered_branches * 100 / module_branches, 4)
                    if module_branches
                    else 100.0
                ),
                line_passed=module_line_passed,
                branch_passed=module_branch_passed,
            )
        )
    return CoverageGateResult(
        covered_lines=covered_lines,
        num_statements=num_statements,
        covered_branches=covered_branches,
        num_branches=num_branches,
        line_percent=round(covered_lines * 100 / num_statements, 4),
        branch_percent=round(covered_branches * 100 / num_branches, 4),
        minimum_lines=float(line_threshold),
        minimum_branches=float(branch_threshold),
        line_passed=line_passed,
        branch_passed=branch_passed,
        module_results=tuple(module_results),
    )


def build_parser() -> argparse.ArgumentParser:
    """Define independent global and critical-module coverage gate options."""

    parser = argparse.ArgumentParser(
        description="Require independent line and branch coverage thresholds."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum-lines", type=float, default=90.0)
    parser.add_argument("--minimum-branches", type=float, default=90.0)
    parser.add_argument("--critical-module", action="append", default=[])
    parser.add_argument("--minimum-module-lines", type=float, default=90.0)
    parser.add_argument("--minimum-module-branches", type=float, default=90.0)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Evaluate one coverage report and return success, threshold, or input status."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.report.read_text())
        result = evaluate_coverage(
            payload,
            minimum_lines=args.minimum_lines,
            minimum_branches=args.minimum_branches,
            critical_modules=tuple(args.critical_module),
            minimum_module_lines=args.minimum_module_lines,
            minimum_module_branches=args.minimum_module_branches,
        )
    except (OSError, json.JSONDecodeError, CoverageGateError) as exc:
        print(
            json.dumps(
                {"status": "invalid_report", "message": str(exc)}, sort_keys=True
            ),
            file=errors,
        )
        return 2
    print(json.dumps(result.as_dict(), sort_keys=True), file=output)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
