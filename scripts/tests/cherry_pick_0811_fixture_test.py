# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

from scripts.cherry_pick.clients import parse_pull_request_url
from scripts.cherry_pick.core import CommitNode, CoreRequest


FIXTURE = Path(__file__).parent / "fixtures/cherry_pick_0811.json"
CORE_REQUEST_FIXTURE = (
    Path(__file__).parent / "fixtures/cherry_pick_10153_core_request.json"
)


def test_0811_fixture_contains_all_seven_unique_source_requests():
    fixture = json.loads(FIXTURE.read_text())
    cases = fixture["cases"]
    assert fixture["train_id"] == "10.1-20260811"
    assert fixture["destination_branch"] == "release/bkc/therock-10.1-20260811"
    assert len(cases) == 7
    assert len({case["source_pr"] for case in cases}) == 7
    assert len({case["covering_pr"] for case in cases}) == 7
    reasons = [case["expected_reason"] for case in cases]
    assert reasons.count("empty_trial_application") == 5
    assert reasons.count("gitlink_cherry_pick_provenance") == 1
    assert reasons.count("covered_by_existing_pr") == 1
    representations = [case["merge_representation"] for case in cases]
    assert representations.count("single") == 2
    assert representations.count("squash") == 3
    assert representations.count("merge_commit") == 2


def test_0811_covering_prs_stay_in_the_source_repository():
    fixture = json.loads(FIXTURE.read_text())
    for case in fixture["cases"]:
        source_owner, source_repo, _ = parse_pull_request_url(case["source_pr"])
        target_owner, target_repo, _ = parse_pull_request_url(case["covering_pr"])
        assert (source_owner, source_repo) == (target_owner, target_repo)


def test_0811_compiler_case_records_divergent_patch_provenance():
    fixture = json.loads(FIXTURE.read_text())
    compiler = next(
        case for case in fixture["cases"] if case["source_pr"].endswith("/7282")
    )
    assert compiler["expected_reason"] == "gitlink_cherry_pick_provenance"
    assert compiler["relationship"] == "diverged"
    assert compiler["source_desired_pin"] != compiler["covering_pin"]
    assert len(compiler["common_origin"]) == 40


def test_10153_regression_freezes_exact_coverage_and_ordered_prerequisite_evidence():
    fixture = json.loads(FIXTURE.read_text())
    case = next(
        item for item in fixture["cases"] if item["source_pr"].endswith("/9716")
    )

    assert case["covering_pr"].endswith("/10153")
    assert case["expected_reason"] == "covered_by_existing_pr"
    assert case["covering_head"] == "411a04e98648ef442751e8e219ab9fa1cfb228bf"
    assert case["planned_tree"] == "2b7467c293ea312349db32372bdc51a495fd419d"
    assert case["prerequisite_sequence"] == [
        "https://github.com/ROCm/rocm-systems/commit/"
        "3a3fb3206000a3b47e953fd6613571ae6ca0edb4",
        "https://github.com/ROCm/rocm-systems/pull/8221",
        "https://github.com/ROCm/rocm-systems/pull/9480",
    ]


def test_10153_regression_retains_a_complete_current_core_request():
    request = CoreRequest.from_dict(json.loads(CORE_REQUEST_FIXTURE.read_text()))

    assert request.schema_version == 3
    assert request.source.url.endswith("/9716")
    assert request.source.destination.head_sha == (
        "800045c8ab865991f4cec1549de2bb44e76b9904"
    )
    assert [item.url for item in request.prerequisites] == [
        "https://github.com/ROCm/rocm-systems/commit/"
        "3a3fb3206000a3b47e953fd6613571ae6ca0edb4",
        "https://github.com/ROCm/rocm-systems/pull/8221",
        "https://github.com/ROCm/rocm-systems/pull/9480",
    ]
    assert isinstance(request.prerequisites[0], CommitNode)
    assert [item.url for item in request.coverage_candidates] == [
        "https://github.com/ROCm/rocm-systems/pull/10153"
    ]
    assert request.coverage_candidates[0].head_sha == (
        "411a04e98648ef442751e8e219ab9fa1cfb228bf"
    )
