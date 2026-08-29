# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.cherry_pick.core import CommitNode, CorePlanner, CoreRequest, ManifestError
from scripts.cherry_pick.git import ChangesetError, GitEvidenceError
from scripts.cherry_pick.models import Result, Status


ROOT_URL = "https://github.com/ROCm/TheRock/pull/10"
DEP_URL = "https://github.com/ROCm/rocm-systems/pull/20"
COMMIT_SHA = "3a3fb3206000a3b47e953fd6613571ae6ca0edb4"
COMMIT_URL = f"https://github.com/ROCm/rocm-systems/commit/{COMMIT_SHA}"


def node(url, repository, number, *, seed, destination_branch):
    values = {
        "a": ("a", "b", "c", "d", "e"),
        "f": ("1", "2", "3", "4", "5"),
    }[seed]
    return {
        "kind": "pull_request",
        "url": url,
        "repository": repository,
        "number": number,
        "base_branch": "main" if repository == "ROCm/TheRock" else "develop",
        "head_sha": values[0] * 40,
        "merge_sha": values[1] * 40,
        "ordered_commits": [values[2] * 40],
        "body_sha256": values[3] * 64,
        "destination": {
            "repository": repository,
            "branch": destination_branch,
            "head_sha": values[4] * 40,
        },
    }


def commit_node(*, destination_branch="release/systems"):
    return {
        "kind": "commit",
        "url": COMMIT_URL,
        "repository": "ROCm/rocm-systems",
        "commit_sha": COMMIT_SHA,
        "destination": {
            "repository": "ROCm/rocm-systems",
            "branch": destination_branch,
            "head_sha": "5" * 40,
        },
    }


def coverage_candidate(number=10153, *, head_sha="7" * 40, draft=False):
    return {
        "url": f"https://github.com/ROCm/TheRock/pull/{number}",
        "repository": "ROCm/TheRock",
        "number": number,
        "state": "open",
        "draft": draft,
        "base_branch": "release/root",
        "base_sha": "e" * 40,
        "head_repository": "ROCm/TheRock",
        "head_sha": head_sha,
    }


def manifest(*, prerequisites=None, edges=None, coverage_candidates=None):
    return {
        "schema_version": 3,
        "dependency_mode": "gate",
        "train_id": "train",
        "source": node(
            ROOT_URL,
            "ROCm/TheRock",
            10,
            seed="a",
            destination_branch="release/root",
        ),
        "prerequisites": prerequisites or [],
        "prerequisite_edges": edges or [],
        "coverage_candidates": coverage_candidates or [],
    }


def result(status, *, message="result", reason="reason"):
    return Result(
        status=status,
        reason_code=reason,
        message=message,
        evidence={},
    )


def test_manifest_round_trip_and_fingerprint_are_canonical():
    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    payload = manifest(
        prerequisites=[dep],
        edges=[{"from": ROOT_URL, "to": DEP_URL}],
    )
    request = CoreRequest.from_dict(payload)

    assert request.as_dict() == payload
    assert len(request.fingerprint()) == 64
    reordered = json.loads(json.dumps(payload, sort_keys=False))
    reordered = dict(reversed(list(reordered.items())))
    assert CoreRequest.from_dict(reordered).fingerprint() == request.fingerprint()

    changed = json.loads(json.dumps(payload))
    changed["source"]["destination"]["head_sha"] = "f" * 40
    assert CoreRequest.from_dict(changed).fingerprint() != request.fingerprint()


@pytest.mark.parametrize(
    "mutator,message",
    [
        (lambda value: value.update(schema_version=1), "schema_version"),
        (lambda value: value.update(extra=True), "unsupported"),
        (lambda value: value["source"].update(head_sha="short"), "head_sha"),
        (lambda value: value["source"].update(number=0), "number"),
        (
            lambda value: value["source"].update(
                url="https://github.com/ROCm/TheRock/issues/10"
            ),
            "url",
        ),
        (
            lambda value: value["source"]["destination"].update(
                repository="ROCm/rocm-systems"
            ),
            "destination repository",
        ),
    ],
)
def test_manifest_rejects_malformed_or_unknown_evidence(mutator, message):
    payload = manifest()
    mutator(payload)
    with pytest.raises(ManifestError, match=message):
        CoreRequest.from_dict(payload)


def test_manifest_rejects_every_malformed_nested_contract_shape():
    with pytest.raises(ManifestError, match="object"):
        CoreRequest.from_dict([])

    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    cases = []

    def case(change, message):
        payload = manifest()
        change(payload)
        cases.append((payload, message))

    case(lambda value: value.pop("train_id"), "omitted")
    case(lambda value: value.update(train_id=""), "non-empty")
    case(
        lambda value: value["source"]["destination"].update(repository="ROCm/unknown"),
        "unsupported",
    )
    case(
        lambda value: value["source"]["destination"].update(branch="bad branch"),
        "branch",
    )

    def mismatch_identity(value):
        value["source"]["repository"] = "ROCm/rocm-systems"
        value["source"]["destination"]["repository"] = "ROCm/rocm-systems"

    case(mismatch_identity, "identity")
    case(lambda value: value["source"].update(base_branch="bad branch"), "base_branch")
    case(lambda value: value["source"].update(ordered_commits=None), "non-empty array")
    case(
        lambda value: value["source"].update(ordered_commits=["short"]),
        "full lowercase",
    )
    case(lambda value: value["source"].update(body_sha256="short"), "SHA-256")
    case(lambda value: value.update(prerequisites={}), "prerequisites")
    case(lambda value: value.update(prerequisite_edges={}), "prerequisite_edges")
    case(lambda value: value.update(coverage_candidates={}), "coverage_candidates")
    case(
        lambda value: value.update(
            prerequisites=[deepcopy(dep)],
            prerequisite_edges=[
                {"from": ROOT_URL, "to": DEP_URL},
                {"from": ROOT_URL, "to": DEP_URL},
            ],
        ),
        "duplicate dependency edge",
    )
    case(lambda value: value.update(prerequisites=[deepcopy(dep)]), "unreachable")

    for payload, message in cases:
        with pytest.raises(ManifestError, match=message):
            CoreRequest.from_dict(payload)


def test_manifest_rejects_duplicate_missing_and_cyclic_prerequisite_edges():
    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    with pytest.raises(ManifestError, match="duplicate dependency"):
        CoreRequest.from_dict(manifest(prerequisites=[dep, dep]))

    with pytest.raises(ManifestError, match="unknown dependency node"):
        CoreRequest.from_dict(manifest(edges=[{"from": ROOT_URL, "to": DEP_URL}]))

    with pytest.raises(ManifestError, match="cycle"):
        CoreRequest.from_dict(
            manifest(
                prerequisites=[dep],
                edges=[
                    {"from": ROOT_URL, "to": DEP_URL},
                    {"from": DEP_URL, "to": ROOT_URL},
                ],
            )
        )


def test_manifest_accepts_typed_commit_leaf_and_rejects_commit_with_outgoing_edge():
    commit = commit_node()
    request = CoreRequest.from_dict(
        manifest(
            prerequisites=[commit],
            edges=[{"from": ROOT_URL, "to": COMMIT_URL}],
        )
    )

    assert request.prerequisites[0].kind == "commit"
    assert request.prerequisites[0].commit_sha == COMMIT_SHA

    with pytest.raises(ManifestError, match="commit_prerequisite_not_leaf"):
        CoreRequest.from_dict(
            manifest(
                prerequisites=[commit],
                edges=[
                    {"from": ROOT_URL, "to": COMMIT_URL},
                    {"from": COMMIT_URL, "to": ROOT_URL},
                ],
            )
        )


def test_manifest_strictly_validates_open_pull_coverage_candidates():
    payload = manifest(coverage_candidates=[coverage_candidate()])
    request = CoreRequest.from_dict(payload)
    assert request.as_dict() == payload

    for change, message in (
        (lambda candidate: candidate.update(state="closed"), "state"),
        (lambda candidate: candidate.update(head_sha="short"), "head_sha"),
        (lambda candidate: candidate.update(draft="false"), "draft"),
        (
            lambda candidate: candidate.update(repository="ROCm/rocm-systems"),
            "identity",
        ),
        (lambda candidate: candidate.update(extra=True), "unsupported"),
    ):
        candidate = coverage_candidate()
        change(candidate)
        with pytest.raises(ManifestError, match=message):
            CoreRequest.from_dict(manifest(coverage_candidates=[candidate]))

    with pytest.raises(ManifestError, match="duplicate coverage candidate"):
        CoreRequest.from_dict(
            manifest(coverage_candidates=[coverage_candidate(), coverage_candidate()])
        )


def test_manifest_rejects_typed_node_and_candidate_identity_confusion():
    cases = []

    source_kind = manifest()
    source_kind["source"]["kind"] = "commit"
    cases.append((source_kind, "kind must be pull_request"))

    source_commit_url = manifest()
    source_commit_url["source"]["url"] = COMMIT_URL
    cases.append((source_commit_url, "must identify a pull request"))

    bad_candidate_url = coverage_candidate()
    bad_candidate_url["url"] = "https://github.com/ROCm/TheRock/issues/10153"
    cases.append(
        (manifest(coverage_candidates=[bad_candidate_url]), "candidate.*url is invalid")
    )

    bad_candidate_branch = coverage_candidate()
    bad_candidate_branch["base_branch"] = "bad branch"
    cases.append(
        (manifest(coverage_candidates=[bad_candidate_branch]), "base_branch is invalid")
    )

    bad_candidate_owner = coverage_candidate()
    bad_candidate_owner["head_repository"] = "ROCm/rocm-systems"
    cases.append(
        (manifest(coverage_candidates=[bad_candidate_owner]), "head_repository")
    )

    other_repository = coverage_candidate()
    other_repository.update(
        {
            "url": "https://github.com/ROCm/rocm-systems/pull/10153",
            "repository": "ROCm/rocm-systems",
            "head_repository": "ROCm/rocm-systems",
        }
    )
    cases.append(
        (manifest(coverage_candidates=[other_repository]), "request pull destination")
    )

    other_destination = coverage_candidate()
    other_destination["base_branch"] = "release/other"
    cases.append(
        (manifest(coverage_candidates=[other_destination]), "request pull destination")
    )

    for payload, message in cases:
        with pytest.raises(ManifestError, match=message):
            CoreRequest.from_dict(payload)


def test_commit_node_rejects_wrong_kind_url_identity_and_destination():
    cases = []
    wrong_kind = commit_node()
    wrong_kind["kind"] = "pull_request"
    cases.append((wrong_kind, "kind must be commit"))

    bad_url = commit_node()
    bad_url["url"] = "https://github.com/ROCm/rocm-systems/issues/1"
    cases.append((bad_url, "url is invalid"))

    mismatched_sha = commit_node()
    mismatched_sha["commit_sha"] = "d" * 40
    cases.append((mismatched_sha, "identity does not match commit"))

    mismatched_destination = commit_node()
    mismatched_destination["destination"]["repository"] = "ROCm/TheRock"
    cases.append((mismatched_destination, "destination repository"))

    for payload, message in cases:
        with pytest.raises(ManifestError, match=message):
            CommitNode.from_dict(payload, "commit")


def test_manifest_compatibility_views_are_exact_typed_graph_aliases():
    request = CoreRequest.from_dict(
        manifest(
            prerequisites=[commit_node()],
            edges=[{"from": ROOT_URL, "to": COMMIT_URL}],
        )
    )

    assert request.dependencies is request.prerequisites
    assert request.dependency_edges is request.prerequisite_edges


def test_planner_waits_for_clean_but_not_contained_prerequisite(tmp_path):
    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    request = CoreRequest.from_dict(
        manifest(
            prerequisites=[dep],
            edges=[{"from": ROOT_URL, "to": DEP_URL}],
        )
    )
    evaluated = []

    def build(repo, change):
        assert isinstance(repo, Path)
        return change.url

    def evaluate(repo, changeset, destination, identity, scratch_root):
        assert scratch_root is None
        evaluated.append((changeset, destination, identity.repository))
        return result(Status.DRAFT_PLANNED, reason="clean_trial_application")

    planner = CorePlanner(changeset_builder=build, evaluator=evaluate)
    actual = planner.plan(
        request,
        {
            "ROCm/TheRock": tmp_path / "root",
            "ROCm/rocm-systems": tmp_path / "systems",
        },
    )

    assert actual.status is Status.AWAITING_DEPENDENCIES
    assert actual.reason_code == "dependencies_not_contained"
    assert [item[0] for item in evaluated] == [DEP_URL]
    assert actual.evidence["prerequisite_results"][0]["status"] == "draft_planned"


def test_planner_evaluates_root_after_every_prerequisite_is_contained(tmp_path):
    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    request = CoreRequest.from_dict(
        manifest(
            prerequisites=[dep],
            edges=[{"from": ROOT_URL, "to": DEP_URL}],
        )
    )
    evaluated = []

    def evaluate(repo, changeset, destination, identity, scratch_root):
        assert scratch_root is None
        evaluated.append(changeset.url)
        if changeset.url == DEP_URL:
            return result(Status.ALREADY_CONTAINED, reason="ancestor")
        return Result(
            status=Status.DRAFT_PLANNED,
            reason_code="clean_trial_application",
            message="clean",
            evidence={"planned_tree": "9" * 40},
        )

    planner = CorePlanner(
        changeset_builder=lambda repo, change: change, evaluator=evaluate
    )
    actual = planner.plan(
        request,
        {
            "ROCm/TheRock": tmp_path / "root",
            "ROCm/rocm-systems": tmp_path / "systems",
        },
    )

    assert actual.status is Status.DRAFT_PLANNED
    assert evaluated == [DEP_URL, ROOT_URL]
    assert actual.source_pr == ROOT_URL
    assert actual.destination_branch == "release/root"
    assert actual.evidence["plan_fingerprint"] == request.fingerprint()
    assert actual.evidence["prerequisite_results"][0]["status"] == "already_contained"
    assert actual.evidence["assurance"] == {
        "scope": "git_only",
        "ci_checks": "not_evaluated",
        "semantic_readiness": "human_review_required",
    }


def test_planner_evaluates_standalone_commit_prerequisite_before_root(tmp_path):
    request = CoreRequest.from_dict(
        manifest(
            prerequisites=[commit_node()],
            edges=[{"from": ROOT_URL, "to": COMMIT_URL}],
        )
    )
    evaluated = []

    def evaluate(_repo, changeset, _destination, identity, _scratch):
        evaluated.append((changeset.url, type(identity).__name__))
        if changeset.url == COMMIT_URL:
            assert identity.commit_sha == COMMIT_SHA
            return result(
                Status.ALREADY_CONTAINED, reason="complete_changeset_ancestor"
            )
        return Result(
            status=Status.DRAFT_PLANNED,
            reason_code="clean_trial_application",
            message="clean",
            evidence={"planned_tree": "9" * 40},
        )

    actual = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=evaluate,
    ).plan(
        request,
        {
            "ROCm/TheRock": tmp_path / "root",
            "ROCm/rocm-systems": tmp_path / "systems",
        },
    )

    assert actual.status is Status.DRAFT_PLANNED
    assert evaluated == [
        (COMMIT_URL, "CommitIdentity"),
        (ROOT_URL, "SourceIdentity"),
    ]


@pytest.mark.parametrize("draft", [False, True])
def test_exact_open_manual_or_draft_pull_suppresses_duplicate_automation(
    tmp_path, draft
):
    candidate = coverage_candidate(draft=draft)
    request = CoreRequest.from_dict(manifest(coverage_candidates=[candidate]))

    def evaluate(*_args):
        return Result(
            status=Status.DRAFT_PLANNED,
            reason_code="clean_trial_application",
            message="clean",
            evidence={"planned_tree": "9" * 40},
        )

    def cover(
        _repo, _changeset, destination, actual, _identity, planned_tree, _scratch
    ):
        assert destination == "e" * 40
        assert actual.url == candidate["url"]
        assert planned_tree == "9" * 40
        return Result(
            status=Status.COVERED_BY_EXISTING_PR,
            reason_code="exact_existing_pull_coverage",
            message="covered",
            evidence={"candidate_head": actual.head_sha},
            pull_request_url=actual.url,
        )

    actual = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=evaluate,
        coverage_evaluator=cover,
    ).plan(request, {"ROCm/TheRock": tmp_path})

    assert actual.status is Status.COVERED_BY_EXISTING_PR
    assert actual.reason_code == "covered_by_existing_pr"
    assert actual.pull_request_url == candidate["url"]
    assert len(actual.evidence["coverage_snapshot_sha256"]) == 64
    assert actual.evidence["coverage_results"][0]["outcome"] == "exact"


def test_unrelated_open_pull_is_ignored_and_ambiguous_candidate_blocks(tmp_path):
    candidates = [
        coverage_candidate(10153),
        coverage_candidate(10154, head_sha="8" * 40),
    ]
    request = CoreRequest.from_dict(manifest(coverage_candidates=candidates))
    root = Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        evidence={"planned_tree": "9" * 40},
    )

    def cover(_repo, _changeset, _destination, candidate, *_rest):
        if candidate.number == 10153:
            return None
        return Result(
            status=Status.BLOCKED_AMBIGUOUS_CHANGESET,
            reason_code="existing_pull_exact_tree_without_attribution",
            message="ambiguous",
            evidence={"candidate_head": candidate.head_sha},
        )

    actual = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=lambda *_args: root,
        coverage_evaluator=cover,
    ).plan(request, {"ROCm/TheRock": tmp_path})

    assert actual.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert actual.reason_code == "existing_pull_coverage_ambiguous"
    assert [item["outcome"] for item in actual.evidence["coverage_results"]] == [
        "unrelated",
        "ambiguous",
    ]


def test_all_unrelated_open_pulls_leave_the_clean_root_plan_unchanged(tmp_path):
    request = CoreRequest.from_dict(
        manifest(coverage_candidates=[coverage_candidate()])
    )
    root = Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        evidence={"planned_tree": "9" * 40},
    )

    actual = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=lambda *_args: root,
        coverage_evaluator=lambda *_args: None,
    ).plan(request, {"ROCm/TheRock": tmp_path})

    assert actual.status is Status.DRAFT_PLANNED
    assert actual.evidence["coverage_results"][0]["outcome"] == "unrelated"


def test_open_pull_coverage_blocks_when_root_plan_omits_exact_tree(tmp_path):
    request = CoreRequest.from_dict(
        manifest(coverage_candidates=[coverage_candidate()])
    )

    actual = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=lambda *_args: Result(
            status=Status.DRAFT_PLANNED,
            reason_code="clean_trial_application",
            message="clean but incomplete",
            evidence={},
        ),
    ).plan(request, {"ROCm/TheRock": tmp_path})

    assert actual.status is Status.BLOCKED_EVIDENCE
    assert actual.reason_code == "coverage_proof_input_missing"


def test_multiple_exact_covering_pulls_fail_closed(tmp_path):
    request = CoreRequest.from_dict(
        manifest(
            coverage_candidates=[
                coverage_candidate(10153),
                coverage_candidate(10154, head_sha="8" * 40),
            ]
        )
    )
    root = Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        evidence={"planned_tree": "9" * 40},
    )

    def cover(_repo, _changeset, _destination, candidate, *_rest):
        return Result(
            status=Status.COVERED_BY_EXISTING_PR,
            reason_code="exact_existing_pull_coverage",
            message="covered",
            evidence={},
            pull_request_url=candidate.url,
        )

    actual = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=lambda *_args: root,
        coverage_evaluator=cover,
    ).plan(request, {"ROCm/TheRock": tmp_path})

    assert actual.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert actual.reason_code == "multiple_existing_pull_coverage"


def test_planner_forwards_explicit_scratch_root_to_git_evaluator(tmp_path):
    request = CoreRequest.from_dict(manifest())
    scratch_root = tmp_path / "disk-scratch"
    received = []

    def evaluate(repo, changeset, destination, identity, scratch):
        received.append(scratch)
        return Result(
            status=Status.DRAFT_PLANNED,
            reason_code="clean_trial_application",
            message="clean",
            evidence={"planned_tree": "9" * 40},
        )

    actual = CorePlanner(
        changeset_builder=lambda repo, change: change,
        evaluator=evaluate,
    ).plan(
        request,
        {"ROCm/TheRock": tmp_path / "root"},
        scratch_root=scratch_root,
    )

    assert actual.status is Status.DRAFT_PLANNED
    assert received == [scratch_root]


@pytest.mark.parametrize(
    "dependency_status",
    [
        Status.BLOCKED_AMBIGUOUS_CHANGESET,
        Status.BLOCKED_CONFLICT,
    ],
)
def test_planner_converts_unreliable_prerequisite_evaluation_to_block(
    dependency_status, tmp_path
):
    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    request = CoreRequest.from_dict(
        manifest(prerequisites=[dep], edges=[{"from": ROOT_URL, "to": DEP_URL}])
    )
    planner = CorePlanner(
        changeset_builder=lambda repo, change: change,
        evaluator=lambda *args: result(dependency_status),
    )

    actual = planner.plan(
        request,
        {
            "ROCm/TheRock": tmp_path / "root",
            "ROCm/rocm-systems": tmp_path / "systems",
        },
    )
    assert actual.status is Status.BLOCKED_DEPENDENCY
    assert actual.reason_code == "dependency_evaluation_blocked"


def test_planner_preserves_incomplete_local_object_prerequisite_evidence(tmp_path):
    dep = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    request = CoreRequest.from_dict(
        manifest(prerequisites=[dep], edges=[{"from": ROOT_URL, "to": DEP_URL}])
    )
    diagnostic = "fatal: could not fetch " + "a" * 40 + " from promisor remote"
    planner = CorePlanner(
        changeset_builder=lambda repo, change: change,
        evaluator=lambda *args: Result(
            status=Status.BLOCKED_EVIDENCE,
            reason_code="local_objects_incomplete",
            message="Required Git objects are missing locally.",
            evidence={"git_stderr": diagnostic},
        ),
    )

    actual = planner.plan(
        request,
        {
            "ROCm/TheRock": tmp_path / "root",
            "ROCm/rocm-systems": tmp_path / "systems",
        },
    )

    assert actual.status is Status.BLOCKED_EVIDENCE
    assert actual.reason_code == "local_objects_incomplete"
    assert actual.evidence["git_stderr"] == diagnostic
    assert actual.evidence["prerequisite_results"][0]["status"] == "blocked_evidence"


def test_planner_fails_closed_for_missing_repository_mapping(tmp_path):
    request = CoreRequest.from_dict(manifest())
    planner = CorePlanner(changeset_builder=lambda repo, change: change)
    actual = planner.plan(request, {})
    assert actual.status is Status.BLOCKED_EVIDENCE
    assert actual.reason_code == "local_repository_missing"


def test_core_requires_manual_review_for_unattributed_patch_equivalence(tmp_path):
    request = CoreRequest.from_dict(manifest())
    planner = CorePlanner(
        changeset_builder=lambda _repo, change: change,
        evaluator=lambda *_args: Result(
            status=Status.ALREADY_CONTAINED,
            reason_code="complete_changeset_already_applied",
            message="the full application is empty",
            evidence={"patch_equivalent": True},
        ),
    )

    actual = planner.plan(request, {"ROCm/TheRock": tmp_path})

    assert actual.status is Status.BLOCKED_AMBIGUOUS_CHANGESET
    assert actual.reason_code == "patch_equivalent_review_required"
    assert actual.evidence["patch_equivalent"] is True


def test_planner_reports_incomplete_local_objects_as_blocked_evidence(tmp_path):
    request = CoreRequest.from_dict(manifest())
    diagnostic = (
        "warning: lazy fetching disabled; some objects may not be available\n"
        "fatal: could not fetch " + "a" * 40 + " from promisor remote"
    )

    def fail(_repo, _change):
        raise GitEvidenceError(
            "local_objects_incomplete",
            "Required Git objects are missing locally.",
            diagnostic,
        )

    actual = CorePlanner(changeset_builder=fail).plan(
        request, {"ROCm/TheRock": tmp_path}
    )

    assert actual.status is Status.BLOCKED_EVIDENCE
    assert actual.reason_code == "local_objects_incomplete"
    assert actual.message == "Required Git objects are missing locally."
    assert actual.evidence["git_stderr"] == diagnostic


@pytest.mark.parametrize(
    "error,expected_status,expected_reason",
    [
        (
            ChangesetError("ambiguous source"),
            Status.BLOCKED_AMBIGUOUS_CHANGESET,
            "changeset_proof_failed",
        ),
        (
            RuntimeError("git failed"),
            Status.BLOCKED_EVIDENCE,
            "git_evidence_unavailable",
        ),
    ],
)
def test_planner_structures_changeset_builder_failures(
    tmp_path, error, expected_status, expected_reason
):
    request = CoreRequest.from_dict(manifest())

    def fail(_repo, _change):
        raise error

    actual = CorePlanner(changeset_builder=fail).plan(
        request, {"ROCm/TheRock": tmp_path}
    )
    assert actual.status is expected_status
    assert actual.reason_code == expected_reason


def test_core_source_has_no_github_jira_network_or_environment_dependency():
    source = Path("scripts/cherry_pick/core.py").read_text()
    for forbidden in ("GitHubClient", "Jira", "urllib", "requests", "GITHUB_TOKEN"):
        assert forbidden not in source
