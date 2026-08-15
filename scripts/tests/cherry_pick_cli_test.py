# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json

from scripts.cherry_pick.__main__ import main
from scripts.cherry_pick.models import Result, Status


SOURCE_URL = "https://github.com/ROCm/TheRock/pull/7282"
TEST_WRITE_CAPABILITY = object()


def planned_result():
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        source_pr=SOURCE_URL,
        source_repository="ROCm/TheRock",
        train_id="10.1-20260811",
        destination_branch="release/test",
        evidence={
            "source_number": 7282,
            "source_title": "Change ROCM-29371",
            "source_body": "ROCM-29371",
            "source_head": "a" * 40,
            "source_merge_commit": "a" * 40,
            "destination_head": "b" * 40,
            "changeset_kind": "single",
            "ordered_commits": ["a" * 40],
            "mainline": None,
        },
    )


class FakePlanner:
    calls = []
    jira_clients = []

    def __init__(self, config, github, jira):
        self.config = config
        self.jira_clients.append(jira)

    def plan(self, source_pr, train_id, repo_dir, *, event_action=None):
        self.calls.append((source_pr, train_id, str(repo_dir), event_action))
        return planned_result()


class FakeWriter:
    calls = []

    def __init__(self, github, *, capability):
        assert capability is TEST_WRITE_CAPABILITY

    def create(self, repo_dir, train, plan):
        self.calls.append((str(repo_dir), train.id, plan.status))
        return Result(
            status=Status.DRAFT_CREATED,
            reason_code="draft_pull_created",
            message="created",
            source_pr=plan.source_pr,
            source_repository=plan.source_repository,
            train_id=plan.train_id,
            destination_branch=plan.destination_branch,
            pull_request_url="https://github.com/ROCm/TheRock/pull/9000",
        )


class FakeGitHub:
    instances = []

    def __init__(self, token):
        self.token = token
        self.labels = []
        self.comments = []
        self.instances.append(self)

    def ensure_label(self, owner, repo, **kwargs):
        self.labels.append((owner, repo, kwargs))

    def upsert_comment(self, owner, repo, number, **kwargs):
        self.comments.append((owner, repo, number, kwargs))

    def remove_label(self, owner, repo, number, label):
        self.labels.append((owner, repo, number, label))

    def search_merged_labeled_pull_requests(self, owner, repo, label):
        return [SOURCE_URL]


class FakeJira:
    def __init__(self, base_url, token):
        self.base_url = base_url
        self.token = token


def config_file(tmp_path, mode="create-draft", *, require_jira=True):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "trains": [
                    {
                        "id": "10.1-20260811",
                        "label": "cherry-pick:10.1-20260811",
                        "state": "active",
                        "mode": mode,
                        "requirements": (
                            {
                                "jira_fix_version": "10.1.0a20260811",
                                "block_on_dependencies": True,
                            }
                            if require_jira
                            else {}
                        ),
                        "repositories": {
                            "ROCm/TheRock": {
                                "source_branches": ["main"],
                                "destination_branch": "release/test",
                            }
                        },
                    }
                ],
            }
        )
    )
    return path


def dependencies():
    return {
        "github_factory": FakeGitHub,
        "jira_factory": FakeJira,
        "planner_factory": FakePlanner,
        "writer_factory": FakeWriter,
    }


def environment():
    return {
        "GITHUB_TOKEN": "github-token",
        "JIRA_URL": "https://jira.example",
        "JIRA_TOKEN": "jira-token",
    }


def test_plan_emits_json_and_never_constructs_writer(tmp_path):
    FakePlanner.calls.clear()
    FakeWriter.calls.clear()
    output = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "plan",
            "--source-pr",
            SOURCE_URL,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
            "--event-action",
            "manual",
        ],
        environ=environment(),
        stdout=output,
        **dependencies(),
    )
    assert code == 0
    assert json.loads(output.getvalue())["status"] == "draft_planned"
    assert FakeWriter.calls == []


def test_write_commands_fail_closed_without_injected_capability(tmp_path):
    for command in (
        [
            "create-draft",
            "--source-pr",
            SOURCE_URL,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
        ],
        ["sync-labels", "--train", "10.1-20260811"],
        [
            "reconcile",
            "--train",
            "10.1-20260811",
            "--repo-dir",
            f"ROCm/TheRock={tmp_path}",
            "--create-drafts",
        ],
    ):
        error = io.StringIO()
        code = main(
            ["--config", str(config_file(tmp_path)), *command],
            environ=environment(),
            stderr=error,
            **dependencies(),
        )
        assert code == 2
        assert "remote write capability" in error.getvalue().lower()
    assert FakeWriter.calls == []


def test_injected_test_capability_exercises_draft_path_only_with_fakes(tmp_path):
    FakeWriter.calls.clear()
    output = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "create-draft",
            "--source-pr",
            SOURCE_URL,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
        ],
        environ=environment(),
        stdout=output,
        write_capability=TEST_WRITE_CAPABILITY,
        **dependencies(),
    )
    assert code == 0
    assert json.loads(output.getvalue())["status"] == "draft_created"
    assert FakeWriter.calls


def test_plan_without_jira_requirement_needs_no_jira_credentials(tmp_path):
    FakePlanner.jira_clients.clear()
    output = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path, require_jira=False)),
            "plan",
            "--source-pr",
            SOURCE_URL,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
        ],
        environ={"GITHUB_TOKEN": "github-token"},
        stdout=output,
        **dependencies(),
    )
    assert code == 0
    assert FakePlanner.jira_clients[-1] is None


def test_reconcile_plan_is_read_only_and_discovers_labeled_prs(tmp_path):
    FakePlanner.calls.clear()
    output = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "reconcile",
            "--train",
            "10.1-20260811",
            "--repo-dir",
            f"ROCm/TheRock={tmp_path}",
        ],
        environ=environment(),
        stdout=output,
        **dependencies(),
    )
    payload = json.loads(output.getvalue())
    assert code == 0
    assert payload["mode"] == "plan"
    assert payload["results"][0]["status"] == "draft_planned"


def test_malformed_configuration_and_results_are_structured_cli_errors(tmp_path):
    bad_config = tmp_path / "bad.json"
    bad_config.write_text("{}")
    error = io.StringIO()
    code = main(
        [
            "--config",
            str(bad_config),
            "plan",
            "--source-pr",
            SOURCE_URL,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path),
        ],
        environ=environment(),
        stderr=error,
        **dependencies(),
    )
    assert code == 2
    assert "configuration" in error.getvalue().lower()
    assert "traceback" not in error.getvalue().lower()


def test_missing_credentials_fails_without_echoing_values(tmp_path):
    output = io.StringIO()
    error = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "plan",
            "--source-pr",
            SOURCE_URL,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
        ],
        environ={},
        stdout=output,
        stderr=error,
        **dependencies(),
    )
    assert code == 2
    assert "GITHUB_TOKEN" in error.getvalue()
    assert output.getvalue() == ""
