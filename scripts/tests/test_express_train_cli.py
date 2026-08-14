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

    def plan(self, source_pr, train_id, repo_dir):
        self.calls.append((source_pr, train_id, str(repo_dir)))
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
        ],
        environ=environment(),
        stdout=output,
        **dependencies(),
    )
    assert code == 0
    assert json.loads(output.getvalue())["status"] == "cherry_pick_required"
    assert FakeWriter.calls == []


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
