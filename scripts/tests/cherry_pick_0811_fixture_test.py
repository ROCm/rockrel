import json
from pathlib import Path

from scripts.cherry_pick.clients import parse_pull_request_url


FIXTURE = Path(__file__).parent / "fixtures/cherry_pick_0811.json"


def test_0811_fixture_contains_all_seven_unique_source_requests():
    fixture = json.loads(FIXTURE.read_text())
    cases = fixture["cases"]
    assert fixture["train_id"] == "10.1-20260811"
    assert fixture["destination_branch"] == "release/bkc/therock-10.1-20260811"
    assert len(cases) == 7
    assert len({case["source_pr"] for case in cases}) == 7
    assert len({case["covering_pr"] for case in cases}) == 7
    reasons = [case["expected_reason"] for case in cases]
    assert reasons.count("empty_trial_application") == 6
    assert reasons.count("gitlink_cherry_pick_provenance") == 1


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
