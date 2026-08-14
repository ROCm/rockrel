from unittest.mock import Mock

from scripts.express_train.config import ExpressTrainConfig, RepositoryConfig, TrainConfig
from scripts.express_train.models import Result, Status
from scripts.express_train.orchestrator import (
    Planner,
    automation_branch,
    identity_marker,
    render_pull_body,
)


SOURCE_URL = "https://github.com/ROCm/TheRock/pull/7282"
SOURCE_SHA = "a" * 40
TARGET_SHA = "b" * 40


def config(mode="validate"):
    train = TrainConfig(
        id="10.1-20260811",
        jira_fix_version="10.1.0a20260811",
        state="active",
        mode=mode,
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branch="main",
                target_branch="release/bkc/therock-10.1-20260811",
            )
        },
    )
    return ExpressTrainConfig(trains={train.id: train})


def github(pulls=None, merged=True):
    client = Mock()
    client.pull.return_value = {
        "number": 7282,
        "html_url": SOURCE_URL,
        "title": "chore(compiler): SMP for Compiler ww23.20 ROCM-29371",
        "body": "Take ROCM-29371",
        "state": "closed" if merged else "open",
        "merged": merged,
        "merge_commit_sha": SOURCE_SHA if merged else None,
        "base": {"ref": "main"},
        "labels": [{"name": "express-train:10.1-20260811"}],
    }
    client.label_actor.return_value = "operator"
    client.permission.return_value = "write"
    client.branch.return_value = {
        "exists": True,
        "protected": True,
        "sha": TARGET_SHA,
    }
    client.pulls.return_value = pulls or []
    return client


def jira():
    client = Mock()
    client.fix_versions.return_value = {"10.1.0a20260811"}
    return client


def test_open_pr_waits_without_calling_git(tmp_path):
    evaluator = Mock()
    result = Planner(config(), github(merged=False), jira(), evaluator=evaluator).plan(
        SOURCE_URL, "10.1-20260811", tmp_path
    )
    assert result.status is Status.WAITING_FOR_MERGE
    evaluator.assert_not_called()


def test_existing_marker_prevents_duplicate(tmp_path):
    marker = identity_marker("ROCm/TheRock", 7282, "10.1-20260811")
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/7357",
        "body": f"Existing coverage\n{marker}",
        "head": {"ref": "some-branch", "sha": "c" * 40},
    }
    evaluator = Mock()
    result = Planner(
        config(), github([existing]), jira(), evaluator=evaluator
    ).plan(SOURCE_URL, "10.1-20260811", tmp_path)
    assert result.status is Status.COVERED_BY_EXISTING_PR
    assert result.pull_request_url.endswith("/7357")
    evaluator.assert_not_called()


def test_deterministic_branch_prevents_duplicate_without_marker(tmp_path):
    existing = {
        "html_url": "https://github.com/ROCm/TheRock/pull/9000",
        "body": "",
        "head": {
            "ref": "shared/cherry-pick/10.1-20260811/7282",
            "sha": "c" * 40,
        },
    }
    result = Planner(config(), github([existing]), jira()).plan(
        SOURCE_URL, "10.1-20260811", tmp_path
    )
    assert result.status is Status.COVERED_BY_EXISTING_PR


def test_proven_covering_pull_prevents_duplicate_without_marker(tmp_path):
    candidate = {
        "number": 7357,
        "state": "open",
        "html_url": "https://github.com/ROCm/TheRock/pull/7357",
        "body": "independent compiler pin update",
        "head": {"ref": "users/compiler-update", "sha": "c" * 40},
    }
    coverage = Mock(
        return_value={
            "reason": "gitlink_cherry_pick_provenance",
            "paths": ["compiler/amd-llvm"],
            "pull_request_url": candidate["html_url"],
        }
    )
    evaluator = Mock()
    result = Planner(
        config(),
        github([candidate]),
        jira(),
        evaluator=evaluator,
        coverage_evaluator=coverage,
    ).plan(SOURCE_URL, "10.1-20260811", tmp_path)

    assert result.status is Status.COVERED_BY_EXISTING_PR
    assert result.reason_code == "gitlink_cherry_pick_provenance"
    assert result.pull_request_url.endswith("/7357")
    coverage.assert_called_once()
    evaluator.assert_not_called()


def test_clean_plan_includes_source_and_target_evidence(tmp_path):
    evaluator = Mock(
        return_value=Result(
            status=Status.CHERRY_PICK_REQUIRED,
            reason_code="clean_trial_application",
            message="clean",
            evidence={"source_commit": SOURCE_SHA, "target_head": TARGET_SHA},
        )
    )
    result = Planner(config(), github(), jira(), evaluator=evaluator).plan(
        SOURCE_URL, "10.1-20260811", tmp_path
    )
    assert result.status is Status.CHERRY_PICK_REQUIRED
    assert result.source_pr == SOURCE_URL
    assert result.target_branch == "release/bkc/therock-10.1-20260811"
    assert result.evidence["source_title"].startswith("chore(compiler)")
    evaluator.assert_called_once_with(tmp_path, SOURCE_SHA, TARGET_SHA)


def test_missing_train_label_is_invalid(tmp_path):
    client = github()
    client.pull.return_value["labels"] = []
    result = Planner(config(), client, jira()).plan(
        SOURCE_URL, "10.1-20260811", tmp_path
    )
    assert result.status is Status.INVALID
    assert result.reason_code == "train_label_missing"
    assert result.evidence["train_mode"] == "validate"


def test_unlabeled_event_is_cancelled_even_after_label_is_removed(tmp_path):
    client = github()
    client.pull.return_value["labels"] = []
    result = Planner(config(), client, jira()).plan(
        SOURCE_URL, "10.1-20260811", tmp_path, event_action="unlabeled"
    )
    assert result.status is Status.CANCELLED
    assert result.reason_code == "train_label_removed"
    assert result.evidence["train_mode"] == "validate"
    client.label_actor.assert_not_called()


def test_unlabeling_an_unrelated_label_does_not_cancel_present_train(tmp_path):
    evaluator = Mock(
        return_value=Result(
            status=Status.CHERRY_PICK_REQUIRED,
            reason_code="clean_trial_application",
            message="clean",
        )
    )
    result = Planner(config(), github(), jira(), evaluator=evaluator).plan(
        SOURCE_URL, "10.1-20260811", tmp_path, event_action="unlabeled"
    )

    assert result.status is Status.CHERRY_PICK_REQUIRED
    evaluator.assert_called_once()


def test_jira_transport_failure_blocks(tmp_path):
    jira_client = jira()
    jira_client.fix_versions.side_effect = RuntimeError("timeout")
    result = Planner(config(), github(), jira_client).plan(
        SOURCE_URL, "10.1-20260811", tmp_path
    )
    assert result.status is Status.BLOCKED
    assert result.reason_code == "evidence_unavailable"


def test_identity_and_pull_rendering_are_stable():
    assert automation_branch("10.1-20260811", 7282) == (
        "shared/cherry-pick/10.1-20260811/7282"
    )
    marker = identity_marker("ROCm/TheRock", 7282, "10.1-20260811")
    assert marker == "<!-- express-train:ROCm/TheRock#7282:10.1-20260811 -->"

    body = render_pull_body(
        marker=marker,
        source_url=SOURCE_URL,
        source_sha=SOURCE_SHA,
        train_id="10.1-20260811",
        source_body="Take ROCM-29371",
    )
    assert marker in body
    assert SOURCE_URL in body
    assert SOURCE_SHA in body
    assert "ROCM-29371" in body
    assert "remains a draft" in body
