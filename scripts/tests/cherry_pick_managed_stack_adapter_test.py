# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from scripts.cherry_pick.core import CoreRequest
from scripts.cherry_pick.managed_stack import (
    ManagedFrontierError,
    build_frontier_results,
)
from scripts.cherry_pick.models import Result, Status
from scripts.tests.cherry_pick_orchestrator_test import (
    COMMIT_SHA,
    COMMIT_URL,
    DESTINATION_SHA,
    SOURCE_URL,
    SYSTEMS_URL,
    config,
    github,
    planner,
    pull,
    repos,
)


def managed_core_result():
    return Result(
        status=Status.AWAITING_DEPENDENCIES,
        reason_code="managed_dependency_frontier",
        message="one dependency wave is ready",
        evidence={
            "dependency_mode": "managed_stack",
            "dependency_frontier": [
                {
                    "kind": "pull_request",
                    "url": SYSTEMS_URL,
                    "repository": "ROCm/rocm-systems",
                    "destination_branch": "release/bkc/therock-10.1-20260811",
                    "destination_head": DESTINATION_SHA,
                    "status": "draft_planned",
                    "reason_code": "clean_trial_application",
                    "evidence": {
                        "changeset_kind": "single",
                        "ordered_commits": ["2" * 40],
                        "mainline": None,
                        "proof_method": "normalized_patch_identity",
                        "planned_tree": "9" * 40,
                    },
                }
            ],
            "prerequisite_results": [],
            "prerequisite_order": [SYSTEMS_URL],
            "prerequisite_edges": [{"from": SOURCE_URL, "to": SYSTEMS_URL}],
            "coverage_snapshot_sha256": "f" * 64,
        },
    )


def test_adapter_builds_v3_request_with_reviewed_dependency_mode(tmp_path):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    controller, core = planner(
        catalog=config(mode="shadow", dependency_mode="managed_stack"),
        client=github(source=source),
    )
    core.plan.return_value = managed_core_result()

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    request = core.plan.call_args.args[0]
    assert request.schema_version == 3
    assert request.dependency_mode == "managed_stack"
    assert result.evidence["request_manifest"]["schema_version"] == 3
    assert result.evidence["request_manifest"]["dependency_mode"] == "managed_stack"


def test_adapter_enriches_frontier_with_complete_write_evidence_and_root_authority(
    tmp_path,
):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    dependency = pull(
        repository="ROCm/rocm-systems",
        number=8793,
        body="Dependency source body",
        head="e" * 40,
        merge="f" * 40,
    )
    client = github(source=source, dependency=dependency)
    client.pulls.side_effect = lambda owner, repo, **_kwargs: []
    controller, core = planner(
        catalog=config(mode="shadow", dependency_mode="managed_stack"),
        client=client,
    )
    core.plan.return_value = managed_core_result()

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.AWAITING_DEPENDENCIES
    assert result.reason_code == "managed_dependency_frontier"
    frontier = result.evidence["managed_frontier_results"]
    assert len(frontier) == 1
    planned = Result.from_dict(frontier[0])
    assert planned.status is Status.DRAFT_PLANNED
    assert planned.source_pr == SYSTEMS_URL
    assert planned.source_repository == "ROCm/rocm-systems"
    assert planned.destination_branch == "release/bkc/therock-10.1-20260811"
    assert planned.evidence["source_kind"] == "pull_request"
    assert planned.evidence["source_number"] == 8793
    assert planned.evidence["source_title"] == "Compiler update"
    assert planned.evidence["source_body"] == "Dependency source body"
    assert planned.evidence["source_head"] == "e" * 40
    assert planned.evidence["source_merge_commit"] == "f" * 40
    assert planned.evidence["destination_head"] == DESTINATION_SHA
    assert planned.evidence["planned_tree"] == "9" * 40
    assert planned.evidence["authorization"] == result.evidence["authorization"]
    assert planned.evidence["authorization_root_source"] == SOURCE_URL
    assert len(planned.evidence["plan_fingerprint"]) == 64
    assert (
        planned.evidence["root_plan_fingerprint"] == result.evidence["plan_fingerprint"]
    )
    assert planned.evidence["request_manifest"] == result.evidence["request_manifest"]

    assert client.pulls.call_count == 2
    assert {
        (call.args[0], call.args[1], call.kwargs["base"])
        for call in client.pulls.call_args_list
    } == {
        ("ROCm", "TheRock", "release/bkc/therock-10.1-20260811"),
        ("ROCm", "rocm-systems", "release/bkc/therock-10.1-20260811"),
    }


def test_adapter_rejects_malformed_or_nonfrontier_core_evidence(tmp_path):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    client = github(source=source)
    for frontier in (
        [{}],
        [
            {
                **managed_core_result().evidence["dependency_frontier"][0],
                "url": SOURCE_URL,
            }
        ],
        [
            {
                **managed_core_result().evidence["dependency_frontier"][0],
                "status": "already_contained",
            }
        ],
    ):
        core = Mock()
        invalid = managed_core_result()
        invalid.evidence["dependency_frontier"] = frontier
        core.plan.return_value = invalid
        controller, core_used = planner(
            catalog=config(mode="shadow", dependency_mode="managed_stack"),
            client=client,
            core=core,
        )
        core_used.plan.return_value = invalid

        result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

        assert result.status is Status.BLOCKED_EVIDENCE
        assert result.reason_code == "managed_frontier_evidence_invalid"
        assert "managed_frontier_results" not in result.evidence


def direct_frontier_inputs(tmp_path):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    dependency = pull(
        repository="ROCm/rocm-systems",
        number=8793,
        body="Dependency source body",
        head="e" * 40,
        merge="f" * 40,
    )
    controller, core = planner(
        catalog=config(mode="shadow", dependency_mode="managed_stack"),
        client=github(source=source, dependency=dependency),
    )
    core.plan.return_value = managed_core_result()
    controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    return core.plan.call_args.args[0], dependency


def build_direct_frontier(request, dependency, **overrides):
    arguments = {
        "request": request,
        "core_result": managed_core_result(),
        "records": {SYSTEMS_URL: dependency},
        "authorization": {"fingerprint": "c" * 64},
        "execution_context": "github-app",
        "train_mode": "shadow",
        "root_plan_fingerprint": "a" * 64,
        "core_request_fingerprint": "b" * 64,
    }
    arguments.update(overrides)
    return build_frontier_results(**arguments)


def test_managed_frontier_builder_rejects_nonfrontier_roots(tmp_path):
    request, dependency = direct_frontier_inputs(tmp_path)
    invalid_status = replace(managed_core_result(), status=Status.DRAFT_PLANNED)

    for invalid_request, invalid_result in (
        (replace(request, dependency_mode="gate"), managed_core_result()),
        (request, invalid_status),
        (
            request,
            Result(
                status=Status.AWAITING_DEPENDENCIES,
                reason_code="prerequisites_not_contained",
                message="blocked",
                evidence=managed_core_result().evidence,
            ),
        ),
    ):
        with pytest.raises(ManagedFrontierError, match="not a managed"):
            build_direct_frontier(
                invalid_request,
                dependency,
                core_result=invalid_result,
            )


def test_managed_frontier_builder_rejects_fingerprints_and_empty_wave(tmp_path):
    request, dependency = direct_frontier_inputs(tmp_path)
    with pytest.raises(ManagedFrontierError, match="fingerprints"):
        build_direct_frontier(request, dependency, root_plan_fingerprint="bad")

    empty = managed_core_result()
    empty.evidence["dependency_frontier"] = []
    with pytest.raises(ManagedFrontierError, match="non-empty"):
        build_direct_frontier(request, dependency, core_result=empty)


def test_managed_frontier_builder_rejects_incomplete_write_evidence(tmp_path):
    request, dependency = direct_frontier_inputs(tmp_path)
    not_object = managed_core_result()
    not_object.evidence["dependency_frontier"][0]["evidence"] = None
    with pytest.raises(ManagedFrontierError, match="evidence is invalid"):
        build_direct_frontier(request, dependency, core_result=not_object)

    incomplete = managed_core_result()
    incomplete.evidence["dependency_frontier"][0]["evidence"].pop("planned_tree")
    with pytest.raises(ManagedFrontierError, match="core evidence is incomplete"):
        build_direct_frontier(request, dependency, core_result=incomplete)


def test_managed_frontier_builder_requires_exact_pull_metadata(tmp_path):
    request, dependency = direct_frontier_inputs(tmp_path)
    with pytest.raises(ManagedFrontierError, match="metadata is unavailable"):
        build_direct_frontier(request, dependency, records={})

    malformed = deepcopy(dependency)
    malformed["title"] = None
    with pytest.raises(ManagedFrontierError, match="metadata is malformed"):
        build_direct_frontier(
            request,
            dependency,
            records={SYSTEMS_URL: malformed},
        )


def test_managed_frontier_builder_preserves_standalone_commit_write_identity(tmp_path):
    request, dependency = direct_frontier_inputs(tmp_path)
    payload = request.as_dict()
    payload["prerequisites"] = [
        {
            "kind": "commit",
            "url": COMMIT_URL,
            "repository": "ROCm/rocm-systems",
            "commit_sha": COMMIT_SHA,
            "destination": {
                "repository": "ROCm/rocm-systems",
                "branch": "release/bkc/therock-10.1-20260811",
                "head_sha": DESTINATION_SHA,
            },
        }
    ]
    payload["prerequisite_edges"] = [{"from": SOURCE_URL, "to": COMMIT_URL}]
    request = CoreRequest.from_dict(payload)
    core_result = managed_core_result()
    frontier = core_result.evidence["dependency_frontier"][0]
    frontier.update(
        {
            "kind": "commit",
            "url": COMMIT_URL,
            "repository": "ROCm/rocm-systems",
        }
    )
    frontier["evidence"]["ordered_commits"] = [COMMIT_SHA]

    (result,) = build_direct_frontier(
        request, dependency, core_result=core_result, records={}
    )

    assert result.source_pr == COMMIT_URL
    assert result.evidence["source_kind"] == "commit"
    assert result.evidence["source_commit"] == COMMIT_SHA
    assert result.evidence["source_title"] == f"Cherry-pick {COMMIT_SHA[:12]}"
    assert result.evidence["source_body"] == ""


def test_managed_frontier_builder_rejects_duplicate_and_mismatched_nodes(tmp_path):
    request, dependency = direct_frontier_inputs(tmp_path)
    duplicate = managed_core_result()
    duplicate.evidence["dependency_frontier"].append(
        deepcopy(duplicate.evidence["dependency_frontier"][0])
    )
    with pytest.raises(ManagedFrontierError, match="identity is invalid"):
        build_direct_frontier(request, dependency, core_result=duplicate)

    mismatch = managed_core_result()
    mismatch.evidence["dependency_frontier"][0]["destination_head"] = "0" * 40
    with pytest.raises(ManagedFrontierError, match="does not match"):
        build_direct_frontier(request, dependency, core_result=mismatch)
