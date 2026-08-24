# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json

import pytest

from scripts.check_cherry_pick_coverage import (
    CoverageGateError,
    evaluate_coverage,
    main,
)


def report(*, covered_lines=90, statements=100, covered_branches=45, branches=50):
    return {
        "totals": {
            "covered_lines": covered_lines,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }
    }


def test_gate_requires_lines_and_branches_to_pass_independently():
    passing = evaluate_coverage(report(), minimum_lines=90, minimum_branches=90)
    assert passing.passed is True
    assert passing.line_percent == 90
    assert passing.branch_percent == 90

    low_branches = evaluate_coverage(
        report(covered_lines=95, covered_branches=44),
        minimum_lines=90,
        minimum_branches=90,
    )
    assert low_branches.passed is False
    assert low_branches.line_passed is True
    assert low_branches.branch_passed is False

    low_lines = evaluate_coverage(
        report(covered_lines=89, covered_branches=50),
        minimum_lines=90,
        minimum_branches=90,
    )
    assert low_lines.passed is False
    assert low_lines.line_passed is False
    assert low_lines.branch_passed is True


def test_gate_does_not_round_a_value_below_the_threshold_up_to_passing():
    result = evaluate_coverage(
        report(
            covered_lines=8955,
            statements=10000,
            covered_branches=8999,
            branches=10000,
        ),
        minimum_lines=90,
        minimum_branches=90,
    )

    assert result.line_percent == 89.55
    assert result.branch_percent == 89.99
    assert result.passed is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"totals": []},
        report(statements=0),
        report(branches=0),
        report(covered_lines=-1),
        report(covered_branches=51),
        {"totals": {"covered_lines": True}},
    ],
)
def test_gate_rejects_missing_impossible_or_boolean_counts(payload):
    with pytest.raises(CoverageGateError):
        evaluate_coverage(payload, minimum_lines=90, minimum_branches=90)


def test_cli_emits_machine_readable_result_and_distinct_exit_codes(tmp_path, capsys):
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(report()))

    assert main([str(report_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "passed"
    assert output["line_percent"] == 90
    assert output["branch_percent"] == 90

    report_path.write_text(json.dumps(report(covered_branches=44)))
    assert main([str(report_path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"

    report_path.write_text("not-json")
    assert main([str(report_path)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "invalid_report"


def test_gate_enforces_each_named_safety_critical_module_independently():
    payload = report(covered_lines=99, covered_branches=49)
    payload["files"] = {
        "safe.py": {
            "summary": {
                "covered_lines": 90,
                "num_statements": 100,
                "covered_branches": 45,
                "num_branches": 50,
            }
        },
        "masked.py": {
            "summary": {
                "covered_lines": 89,
                "num_statements": 100,
                "covered_branches": 50,
                "num_branches": 50,
            }
        },
    }

    passing = evaluate_coverage(
        payload,
        minimum_lines=95,
        minimum_branches=90,
        critical_modules=("safe.py",),
        minimum_module_lines=90,
        minimum_module_branches=90,
    )
    assert passing.passed is True
    assert passing.module_results[0].path == "safe.py"

    failing = evaluate_coverage(
        payload,
        minimum_lines=95,
        minimum_branches=90,
        critical_modules=("safe.py", "masked.py"),
        minimum_module_lines=90,
        minimum_module_branches=90,
    )
    assert failing.passed is False
    assert failing.module_results[1].line_passed is False


def test_gate_rejects_missing_critical_module_evidence():
    with pytest.raises(CoverageGateError, match="critical module"):
        evaluate_coverage(
            report(),
            minimum_lines=90,
            minimum_branches=90,
            critical_modules=("missing.py",),
        )
