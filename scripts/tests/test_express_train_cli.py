import io
import json

from scripts.express_train.__main__ import main
from scripts.express_train.models import Result, Status


class FakePlanner:
    result = Result(
        status=Status.CHERRY_PICK_REQUIRED,
        reason_code="clean_trial_application",
        message="clean",
        source_pr="https://github.com/ROCm/TheRock/pull/7282",
        train_id="10.1-20260811",
        target_branch="release/test",
        evidence={
            "source_repository": "ROCm/TheRock",
            "source_number": 7282,
            "source_title": "Change ROCM-29371",
            "source_body": "ROCM-29371",
            "source_merge_commit": "a" * 40,
            "target_head": "b" * 40,
        },
    )
    calls = []

    def __init__(self, config, github, jira):
        self.config = config

    def plan(self, source_pr, train_id, repo_dir, *, event_action=None):
        self.calls.append((source_pr, train_id, str(repo_dir), event_action))
        return self.result


class FakeWriter:
    calls = []

    def __init__(self, github):
        pass

    def create(self, repo_dir, train, plan):
        self.calls.append((str(repo_dir), train.id, plan.status))
        return Result(
            status=Status.DRAFT_CREATED,
            reason_code="draft_pull_created",
            message="created",
            source_pr=plan.source_pr,
            train_id=plan.train_id,
            target_branch=plan.target_branch,
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
        return [FakePlanner.result.source_pr]


class FakeJira:
    def __init__(self, base_url, token):
        self.base_url = base_url
        self.token = token


def config_file(tmp_path, mode="create-draft"):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trains": [
                    {
                        "id": "10.1-20260811",
                        "jira_fix_version": "10.1.0a20260811",
                        "state": "active",
                        "mode": mode,
                        "repositories": {
                            "ROCm/TheRock": {
                                "source_branch": "main",
                                "target_branch": "release/test",
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
            FakePlanner.result.source_pr,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
            "--event-action",
            "unlabeled",
        ],
        environ=environment(),
        stdout=output,
        **dependencies(),
    )
    assert code == 0
    assert json.loads(output.getvalue())["status"] == "cherry_pick_required"
    assert FakeWriter.calls == []
    assert FakePlanner.calls[-1][3] == "unlabeled"


def test_create_draft_runs_writer_and_publishes_sticky_status(tmp_path):
    FakeWriter.calls.clear()
    FakeGitHub.instances.clear()
    output = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "create-draft",
            "--source-pr",
            FakePlanner.result.source_pr,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            str(tmp_path),
            "--publish-status",
        ],
        environ=environment(),
        stdout=output,
        **dependencies(),
    )
    assert code == 0
    assert json.loads(output.getvalue())["status"] == "draft_created"
    assert FakeWriter.calls
    assert FakeGitHub.instances[-1].comments[0][3]["marker"].startswith(
        "<!-- express-train-status:"
    )


def test_sync_labels_adds_only_configured_train_label(tmp_path):
    FakeGitHub.instances.clear()
    output = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "sync-labels",
            "--train",
            "10.1-20260811",
        ],
        environ=environment(),
        stdout=output,
        **dependencies(),
    )
    assert code == 0
    label = FakeGitHub.instances[-1].labels[0]
    assert label[0:2] == ("ROCm", "TheRock")
    assert label[2]["name"] == "express-train:10.1-20260811"


def test_missing_credentials_fails_without_echoing_secret(tmp_path):
    output = io.StringIO()
    error = io.StringIO()
    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "plan",
            "--source-pr",
            FakePlanner.result.source_pr,
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


def test_reconcile_discovers_and_plans_labeled_merged_prs(tmp_path):
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
    assert code == 0
    payload = json.loads(output.getvalue())
    assert payload["status"] == "reconciled"
    assert payload["mode"] == "plan"
    assert payload["results"][0]["source_pr"].endswith("/pull/7282")
    assert FakePlanner.calls[-1][2] == str(tmp_path)


def test_publish_result_needs_no_jira_credentials_and_removes_invalid_label(tmp_path):
    FakeGitHub.instances.clear()
    invalid = Result(
        status=Status.INVALID,
        reason_code="jira_fix_version_mismatch",
        message="wrong train",
        source_pr=FakePlanner.result.source_pr,
        train_id="10.1-20260811",
        target_branch="release/test",
    )
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps(invalid.as_dict()))

    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "publish-result",
            "--result-file",
            str(result_file),
        ],
        environ={"GITHUB_TOKEN": "feedback-token"},
        **dependencies(),
    )

    assert code == 0
    github = FakeGitHub.instances[-1]
    assert github.comments[0][0:3] == ("ROCm", "TheRock", 7282)
    assert github.labels[-1] == (
        "ROCm",
        "TheRock",
        7282,
        "express-train:10.1-20260811",
    )


def test_publish_result_rejects_unknown_or_malformed_result(tmp_path):
    result_file = tmp_path / "result.json"
    result_file.write_text('{"status":"surprise"}')
    error = io.StringIO()

    code = main(
        [
            "--config",
            str(config_file(tmp_path)),
            "publish-result",
            "--result-file",
            str(result_file),
        ],
        environ={"GITHUB_TOKEN": "feedback-token"},
        stderr=error,
        **dependencies(),
    )

    assert code == 2
    assert "invalid result file" in error.getvalue()
