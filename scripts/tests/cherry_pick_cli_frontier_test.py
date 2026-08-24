# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json

from scripts.cherry_pick.__main__ import main
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.write_authority import is_valid_authority
from scripts.tests.cherry_pick_cli_test import FakeGitHub, config


ROOT_SOURCE = "https://github.com/ROCm/TheRock/pull/1"
DEPENDENCY_SOURCE = "https://github.com/ROCm/rocm-systems/pull/2"


def frontier_plan(source, repository):
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        evidence={
            "source_number": 2,
            "plan_fingerprint": "d" * 64,
            "root_plan_fingerprint": "f" * 64,
        },
        source_pr=source,
        source_repository=repository,
        train_id="train",
        destination_branch="release/test",
    )


def root_plan():
    dependency = frontier_plan(DEPENDENCY_SOURCE, "ROCm/rocm-systems")
    return Result(
        status=Status.AWAITING_DEPENDENCIES,
        reason_code="managed_dependency_frontier",
        message="dependency frontier",
        evidence={
            "train_mode": "create-draft",
            "plan_fingerprint": "f" * 64,
            "managed_frontier_results": [dependency.as_dict()],
        },
        source_pr=ROOT_SOURCE,
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
    )


class FrontierPlanner:
    def __init__(self, *_args, **_kwargs):
        pass

    def plan(self, *_args, **_kwargs):
        return root_plan()


class FrontierWriter:
    calls = []

    def __init__(self, _github, *, capability, scratch_root=None):
        self.capability = capability
        self.scratch_root = scratch_root

    def create(self, repo, _train, plan):
        assert is_valid_authority(self.capability, plan.evidence["plan_fingerprint"])
        self.calls.append((repo, plan))
        return Result(
            status=Status.DRAFT_CREATED,
            reason_code="draft_pull_created",
            message="created",
            evidence=plan.evidence,
            source_pr=plan.source_pr,
            source_repository=plan.source_repository,
            train_id=plan.train_id,
            destination_branch=plan.destination_branch,
            pull_request_url="https://github.com/ROCm/rocm-systems/pull/9000",
        )


def test_action_create_draft_materializes_the_exact_managed_frontier(tmp_path):
    config_path = config(tmp_path, mode="create-draft")
    payload = json.loads(config_path.read_text())
    payload["trains"][0]["dependency_mode"] = "managed_stack"
    payload["trains"][0]["repositories"]["ROCm/rocm-systems"] = {
        "source_branches": ["develop"],
        "destination_branch": "release/test",
    }
    config_path.write_text(json.dumps(payload))
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(root_plan().as_dict()))
    rock = tmp_path / "TheRock"
    systems = tmp_path / "rocm-systems"
    FrontierWriter.calls.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(config_path),
            "--config-revision",
            "a" * 40,
            "action-create-draft",
            "--source-pr",
            ROOT_SOURCE,
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={rock}",
            "--repo-dir",
            f"ROCm/rocm-systems={systems}",
            "--expected-result-file",
            str(expected),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FrontierPlanner,
        writer_factory=FrontierWriter,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert len(FrontierWriter.calls) == 1
    assert FrontierWriter.calls[0][0] == systems
    assert FrontierWriter.calls[0][1].source_pr == DEPENDENCY_SOURCE
    result = json.loads(stdout.getvalue())
    assert result["status"] == "awaiting_dependencies"
    assert result["reason_code"] == "managed_dependency_frontier"
    writes = result["evidence"]["managed_frontier_write_results"]
    assert [item["status"] for item in writes] == ["draft_created"]
    assert writes[0]["pull_request_url"].endswith("/9000")


def test_action_create_draft_rejects_a_changed_managed_frontier_before_writes(tmp_path):
    config_path = config(tmp_path, mode="create-draft")
    payload = json.loads(config_path.read_text())
    payload["trains"][0]["dependency_mode"] = "managed_stack"
    payload["trains"][0]["repositories"]["ROCm/rocm-systems"] = {
        "source_branches": ["develop"],
        "destination_branch": "release/test",
    }
    config_path.write_text(json.dumps(payload))
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(root_plan().as_dict()))

    class ChangedFrontierPlanner:
        def __init__(self, *_args, **_kwargs):
            pass

        def plan(self, *_args, **_kwargs):
            current = root_plan()
            current.evidence["managed_frontier_results"][0]["evidence"][
                "plan_fingerprint"
            ] = ("e" * 64)
            return current

    FrontierWriter.calls.clear()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config_path),
            "--config-revision",
            "a" * 40,
            "action-create-draft",
            "--source-pr",
            ROOT_SOURCE,
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={tmp_path / 'TheRock'}",
            "--repo-dir",
            f"ROCm/rocm-systems={tmp_path / 'rocm-systems'}",
            "--expected-result-file",
            str(expected),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=io.StringIO(),
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=ChangedFrontierPlanner,
        writer_factory=FrontierWriter,
    )

    assert code == 2
    assert "drift" in stderr.getvalue()
    assert FrontierWriter.calls == []


def test_action_reconciliation_materializes_the_same_exact_managed_frontier(tmp_path):
    config_path = config(tmp_path, mode="create-draft")
    payload = json.loads(config_path.read_text())
    payload["trains"][0]["dependency_mode"] = "managed_stack"
    payload["trains"][0]["repositories"]["ROCm/rocm-systems"] = {
        "source_branches": ["develop"],
        "destination_branch": "release/test",
    }
    config_path.write_text(json.dumps(payload))
    expected = tmp_path / "expected-reconciliation.json"
    expected.write_text(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "plan",
                "train_id": "train",
                "results": [root_plan().as_dict()],
            }
        )
    )
    rock = tmp_path / "TheRock"
    systems = tmp_path / "rocm-systems"
    FrontierWriter.calls.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(config_path),
            "--config-revision",
            "a" * 40,
            "action-reconcile",
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={rock}",
            "--repo-dir",
            f"ROCm/rocm-systems={systems}",
            "--expected-results-file",
            str(expected),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FrontierPlanner,
        writer_factory=FrontierWriter,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert len(FrontierWriter.calls) == 1
    assert FrontierWriter.calls[0][0] == systems
    artifact = json.loads(stdout.getvalue())
    assert artifact["status"] == "reconciled"
    assert artifact["mode"] == "action-create-draft"
    writes = artifact["results"][0]["evidence"]["managed_frontier_write_results"]
    assert [item["status"] for item in writes] == ["draft_created"]
