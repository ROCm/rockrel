# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.cherry_pick.core import CorePlanner, CoreRequest, ManifestError
from scripts.cherry_pick.models import Result, Status
from scripts.tests.cherry_pick_core_test import (
    COMMIT_URL,
    DEP_URL,
    ROOT_URL,
    commit_node,
    manifest,
    node,
)


MID_URL = "https://github.com/ROCm/rocm-systems/pull/30"


def v3_manifest(*, dependency_mode, prerequisites=None, edges=None):
    value = manifest(prerequisites=prerequisites, edges=edges)
    value["schema_version"] = 3
    value["dependency_mode"] = dependency_mode
    return value


def mid_node():
    value = deepcopy(
        node(
            DEP_URL,
            "ROCm/rocm-systems",
            20,
            seed="f",
            destination_branch="release/systems",
        )
    )
    value.update(
        url=MID_URL,
        number=30,
        head_sha="6" * 40,
        merge_sha="7" * 40,
        ordered_commits=["8" * 40],
        body_sha256="9" * 64,
    )
    return value


def fake_planner(status_by_url):
    def build(_repo, change):
        return change.url

    def evaluate(_repo, change_url, _destination, _identity, _scratch_root):
        status = status_by_url[change_url]
        return Result(
            status=status,
            reason_code=(
                "clean_trial_application"
                if status is Status.DRAFT_PLANNED
                else (
                    "exact_source_ancestor"
                    if status is Status.ALREADY_CONTAINED
                    else "ambiguous"
                )
            ),
            message="evaluated",
            evidence={
                "planned_tree": change_url[-2:].encode().hex().ljust(40, "0")[:40],
                "ordered_commits": ["a" * 40],
                "changeset_kind": "single",
                "mainline": None,
                "proof_method": "normalized_patch_identity",
            },
        )

    return CorePlanner(changeset_builder=build, evaluator=evaluate)


def dependency_nodes():
    leaf = node(
        DEP_URL,
        "ROCm/rocm-systems",
        20,
        seed="f",
        destination_branch="release/systems",
    )
    middle = mid_node()
    return leaf, middle


def test_manifest_v3_requires_an_explicit_reviewed_dependency_mode():
    payload = v3_manifest(dependency_mode="gate")
    request = CoreRequest.from_dict(payload)
    assert request.schema_version == 3
    assert request.dependency_mode == "gate"
    assert request.as_dict() == payload

    for mode in (None, "", "automatic", True):
        invalid = v3_manifest(dependency_mode=mode)
        with pytest.raises(ManifestError, match="dependency_mode"):
            CoreRequest.from_dict(invalid)

    legacy = manifest()
    legacy["schema_version"] = 2
    legacy.pop("dependency_mode")
    with pytest.raises(ManifestError, match="schema_version"):
        CoreRequest.from_dict(legacy)


def test_gate_mode_preserves_the_no_write_dependency_barrier():
    leaf, _middle = dependency_nodes()
    request = CoreRequest.from_dict(
        v3_manifest(
            dependency_mode="gate",
            prerequisites=[leaf],
            edges=[{"from": ROOT_URL, "to": DEP_URL}],
        )
    )
    planner = fake_planner(
        {
            DEP_URL: Status.DRAFT_PLANNED,
            ROOT_URL: Status.DRAFT_PLANNED,
        }
    )

    result = planner.plan(
        request,
        {
            "ROCm/TheRock": Path("/disk/therock"),
            "ROCm/rocm-systems": Path("/disk/systems"),
        },
        scratch_root=Path("/disk/scratch"),
    )

    assert result.status is Status.AWAITING_DEPENDENCIES
    assert result.reason_code == "dependencies_not_contained"
    assert "dependency_frontier" not in result.evidence


def test_managed_stack_emits_only_the_unblocked_topological_frontier():
    leaf, middle = dependency_nodes()
    request = CoreRequest.from_dict(
        v3_manifest(
            dependency_mode="managed_stack",
            prerequisites=[middle, leaf],
            edges=[
                {"from": ROOT_URL, "to": MID_URL},
                {"from": MID_URL, "to": DEP_URL},
            ],
        )
    )
    planner = fake_planner(
        {
            DEP_URL: Status.DRAFT_PLANNED,
            MID_URL: Status.DRAFT_PLANNED,
            ROOT_URL: Status.DRAFT_PLANNED,
        }
    )

    result = planner.plan(
        request,
        {
            "ROCm/TheRock": Path("/disk/therock"),
            "ROCm/rocm-systems": Path("/disk/systems"),
        },
        scratch_root=Path("/disk/scratch"),
    )

    assert result.status is Status.AWAITING_DEPENDENCIES
    assert result.reason_code == "managed_dependency_frontier"
    assert [item["url"] for item in result.evidence["dependency_frontier"]] == [DEP_URL]
    frontier = result.evidence["dependency_frontier"][0]
    assert frontier["kind"] == "pull_request"
    assert frontier["repository"] == "ROCm/rocm-systems"
    assert frontier["destination_branch"] == "release/systems"
    assert frontier["status"] == "draft_planned"
    assert frontier["reason_code"] == "clean_trial_application"
    assert result.evidence["dependency_mode"] == "managed_stack"


def test_managed_stack_advances_exactly_one_wave_after_containment():
    leaf, middle = dependency_nodes()
    request = CoreRequest.from_dict(
        v3_manifest(
            dependency_mode="managed_stack",
            prerequisites=[leaf, middle],
            edges=[
                {"from": ROOT_URL, "to": MID_URL},
                {"from": MID_URL, "to": DEP_URL},
            ],
        )
    )
    planner = fake_planner(
        {
            DEP_URL: Status.ALREADY_CONTAINED,
            MID_URL: Status.DRAFT_PLANNED,
            ROOT_URL: Status.DRAFT_PLANNED,
        }
    )

    result = planner.plan(
        request,
        {
            "ROCm/TheRock": Path("/disk/therock"),
            "ROCm/rocm-systems": Path("/disk/systems"),
        },
    )

    assert [item["url"] for item in result.evidence["dependency_frontier"]] == [MID_URL]
    assert result.evidence["prerequisite_order"] == [DEP_URL, MID_URL]


def test_managed_stack_never_advances_past_a_blocked_dependency():
    leaf, middle = dependency_nodes()
    request = CoreRequest.from_dict(
        v3_manifest(
            dependency_mode="managed_stack",
            prerequisites=[middle, leaf],
            edges=[
                {"from": ROOT_URL, "to": MID_URL},
                {"from": MID_URL, "to": DEP_URL},
            ],
        )
    )
    planner = fake_planner(
        {
            DEP_URL: Status.BLOCKED_CONFLICT,
            MID_URL: Status.DRAFT_PLANNED,
            ROOT_URL: Status.DRAFT_PLANNED,
        }
    )

    result = planner.plan(
        request,
        {
            "ROCm/TheRock": Path("/disk/therock"),
            "ROCm/rocm-systems": Path("/disk/systems"),
        },
    )

    assert result.status is Status.BLOCKED_DEPENDENCY
    assert result.reason_code == "dependency_evaluation_blocked"
    assert "dependency_frontier" not in result.evidence


def test_root_is_evaluated_only_after_every_prerequisite_is_contained():
    leaf, middle = dependency_nodes()
    evaluated = []

    def build(_repo, change):
        return change.url

    def evaluate(_repo, change_url, _destination, _identity, _scratch_root):
        evaluated.append(change_url)
        return Result(
            status=(
                Status.DRAFT_PLANNED
                if change_url == ROOT_URL
                else Status.ALREADY_CONTAINED
            ),
            reason_code="clean_trial_application",
            message="evaluated",
            evidence={"planned_tree": "a" * 40},
        )

    request = CoreRequest.from_dict(
        v3_manifest(
            dependency_mode="managed_stack",
            prerequisites=[middle, leaf],
            edges=[
                {"from": ROOT_URL, "to": MID_URL},
                {"from": MID_URL, "to": DEP_URL},
            ],
        )
    )
    result = CorePlanner(
        changeset_builder=build,
        evaluator=evaluate,
    ).plan(
        request,
        {
            "ROCm/TheRock": Path("/disk/therock"),
            "ROCm/rocm-systems": Path("/disk/systems"),
        },
    )

    assert result.status is Status.DRAFT_PLANNED
    assert evaluated == [DEP_URL, MID_URL, ROOT_URL]
    assert result.evidence["dependency_frontier"] == []


def test_managed_stack_frontier_preserves_standalone_commit_identity():
    commit = commit_node()
    request = CoreRequest.from_dict(
        v3_manifest(
            dependency_mode="managed_stack",
            prerequisites=[commit],
            edges=[{"from": ROOT_URL, "to": COMMIT_URL}],
        )
    )
    planner = fake_planner(
        {
            COMMIT_URL: Status.DRAFT_PLANNED,
            ROOT_URL: Status.DRAFT_PLANNED,
        }
    )

    result = planner.plan(
        request,
        {
            "ROCm/TheRock": Path("/disk/therock"),
            "ROCm/rocm-systems": Path("/disk/systems"),
        },
    )

    assert result.status is Status.AWAITING_DEPENDENCIES
    assert result.reason_code == "managed_dependency_frontier"
    frontier = result.evidence["dependency_frontier"]
