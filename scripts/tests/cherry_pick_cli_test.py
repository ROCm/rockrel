# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json
from pathlib import Path

import pytest

import scripts.cherry_pick.__main__ as cli
from scripts.cherry_pick.__main__ import main
from scripts.cherry_pick.action_runtime import ActionRuntimeError
from scripts.cherry_pick.config import load_config
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.write_authority import is_valid_authority
from scripts.tests.cherry_pick_core_test import manifest as core_manifest

SOURCE = "https://github.com/ROCm/TheRock/pull/1"


class FakePlanner:
    calls = []
    clients = []
    revisions = []
    scratch_roots = []

    contexts = []
    control_plane_snapshots = []

    def __init__(
        self,
        catalog,
        github,
        *,
        config_revision="0" * 40,
        execution_context="github-app",
        control_plane_snapshot=None,
    ):
        self.catalog = catalog
        self.github = github
        self.clients.append(github)
        self.revisions.append(config_revision)
        self.contexts.append(execution_context)
        self.control_plane_snapshots.append(control_plane_snapshot)

    def plan(
        self,
        source,
        train,
        repositories,
        *,
        event_action=None,
        scratch_root=None,
    ):
        self.calls.append((source, train, repositories, event_action))
        self.scratch_roots.append(scratch_root)
        return Result(
            status=Status.DRAFT_PLANNED,
            reason_code="clean_trial_application",
            message="clean",
            evidence={
                "train_mode": self.catalog.train(train).mode,
                "source_number": 1,
                "plan_fingerprint": "f" * 64,
                "core_request_fingerprint": "e" * 64,
                "destination_head": "d" * 40,
                "planned_tree": "c" * 40,
                "authorization": {"fingerprint": "b" * 64},
                "request_manifest": core_manifest(),
            },
            source_pr=source,
            source_repository="ROCm/TheRock",
            train_id=train,
            destination_branch="release/test",
        )


class FakeWriter:
    calls = []
    git_environments = []

    def __init__(self, github, *, capability, scratch_root=None, git_environment=None):
        self.github = github
        self.capability = capability
        self.scratch_root = scratch_root
        self.git_environment = git_environment
        self.git_environments.append(git_environment)

    def create(self, repo, train, plan):
        self.calls.append((repo, train, plan, self.capability))
        return Result(
            **{
                **plan.__dict__,
                "status": Status.DRAFT_CREATED,
                "reason_code": "draft_pull_created",
                "pull_request_url": "https://github.com/ROCm/TheRock/pull/2",
            }
        )


class FakeGitHub:
    instances = []

    def __init__(self, token):
        self.token = token
        self.comments = []
        self.labels = []
        self.instances.append(self)

    def search_merged_labeled_pull_requests(self, owner, repo, label):
        return (SOURCE,)

    def upsert_comment(self, owner, repo, number, *, marker, body):
        self.comments.append((owner, repo, number, marker, body))

    def ensure_label(self, owner, repo, *, name, description):
        self.labels.append((owner, repo, name, description))


def config(tmp_path, *, mode="validate"):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "authorization": {
                    "minimum_human_permission": "write",
                    "trusted_app_ids": [123456],
                    "executor_app_id": None,
                },
                "dependency_policy": {"max_nodes": 64, "max_depth": 16},
                "coverage_policy": {"max_open_pull_requests": 128},
                "trains": [
                    {
                        "id": "train",
                        "label": "cherry-pick:train",
                        "state": "active",
                        "mode": mode,
                        "dependency_mode": "gate",
                        "prerequisite_overrides": [],
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


def invoke(tmp_path, args, *, env=None, authority=None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = main(
        ["--config", str(config(tmp_path)), *args],
        environ={"GITHUB_TOKEN": "token"} if env is None else env,
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
        write_authority=authority,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


def test_plan_emits_json_and_never_constructs_writer(tmp_path):
    FakeWriter.calls.clear()
    code, stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
        ],
    )
    assert code == 0
    assert json.loads(stdout)["status"] == "draft_planned"
    assert stderr == ""
    assert FakeWriter.calls == []


def test_plan_accepts_explicit_cross_repository_maps(tmp_path):
    FakePlanner.calls.clear()
    rock = tmp_path / "TheRock"
    systems = tmp_path / "rocm-systems"
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={rock}",
            "--repo-dir",
            f"ROCm/rocm-systems={systems}",
        ],
    )
    assert code == 0
    assert stderr == ""
    assert FakePlanner.calls[-1][2] == {
        "ROCm/TheRock": rock,
        "ROCm/rocm-systems": systems,
    }


@pytest.mark.parametrize(
    "mappings",
    [
        ["ROCm/TheRock=/tmp/one", "missing-separator"],
        ["=missing-repository"],
        ["ROCm/TheRock="],
        ["ROCm/TheRock=/tmp/one", "ROCm/TheRock=/tmp/two"],
        ["ROCm/rocm-systems=/tmp/systems"],
    ],
)
def test_plan_rejects_mixed_invalid_duplicate_or_missing_source_maps(
    tmp_path, mappings
):
    args = ["plan", "--source-pr", SOURCE, "--train", "train"]
    for mapping in mappings:
        args.extend(["--repo-dir", mapping])
    code, _stdout, stderr = invoke(tmp_path, args)
    assert code == 2
    assert "repo-dir" in stderr


@pytest.mark.parametrize("command", ["create-draft", "sync-labels", "publish-result"])
def test_remote_write_commands_fail_closed_without_injected_authority(
    tmp_path, command
):
    args = [command]
    if command == "create-draft":
        args += ["--source-pr", SOURCE, "--train", "train", "--repo-dir", str(tmp_path)]
    elif command == "sync-labels":
        args += ["--train", "train"]
    else:
        result = tmp_path / "result.json"
        result.write_text("{}")
        args += ["--result-file", str(result)]
    code, _stdout, stderr = invoke(tmp_path, args)
    assert code == 2
    assert "remote write authority is unavailable" in stderr


def test_injected_test_authority_exercises_only_fake_draft_path(tmp_path):
    authority = object()
    FakeWriter.calls.clear()
    code, stdout, stderr = invoke(
        tmp_path,
        [
            "create-draft",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path),
        ],
        authority=authority,
    )
    assert code == 0
    assert json.loads(stdout)["status"] == "draft_created"
    assert stderr == ""
    assert FakeWriter.calls[-1][-1] is authority


def test_no_jira_credentials_or_factory_exist_in_control_plane_cli(tmp_path):
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path),
        ],
        env={"GITHUB_TOKEN": "token"},
    )
    assert code == 0
    assert stderr == ""
    source = Path("scripts/cherry_pick/__main__.py").read_text().lower()
    assert "jira" not in source


def test_missing_github_credential_fails_without_echoing_environment(tmp_path):
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path),
        ],
        env={},
    )
    assert code == 2
    assert "GITHUB_TOKEN" in stderr


def test_default_control_plane_client_is_constructed_only_by_action_runtime(
    tmp_path, monkeypatch
):
    constructed = []
    client = FakeGitHub("action-token")

    def action_client(environment):
        constructed.append(environment)
        if environment.get("GITHUB_ACTIONS") != "true":
            raise ActionRuntimeError("not in Actions")
        return client

    monkeypatch.setattr(cli, "action_github_client", action_client)
    stdout = io.StringIO()
    stderr = io.StringIO()
    environment = {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "action-token"}
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
        ],
        environ=environment,
        stdout=stdout,
        stderr=stderr,
        planner_factory=FakePlanner,
    )

    assert code == 0
    assert constructed == [environment]
    assert FakePlanner.clients[-1] is client
    assert stderr.getvalue() == ""


def test_default_control_plane_client_fails_closed_outside_actions(
    tmp_path, monkeypatch
):
    def reject(_environment):
        raise ActionRuntimeError("production transport requires GitHub Actions")

    monkeypatch.setattr(cli, "action_github_client", reject)
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
        ],
        environ={"GITHUB_TOKEN": "local-token"},
        stdout=stdout,
        stderr=stderr,
        planner_factory=FakePlanner,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert "requires GitHub Actions" in stderr.getvalue()


def test_local_plan_uses_gh_credentials_without_github_token(tmp_path):
    client = FakeGitHub("gh-token")
    calls = []

    def local_client(environment):
        calls.append(environment)
        return client

    stdout = io.StringIO()
    stderr = io.StringIO()
    FakePlanner.contexts.clear()
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "--auth",
            "gh",
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
        ],
        environ={},
        stdout=stdout,
        stderr=stderr,
        local_github_factory=local_client,
        planner_factory=FakePlanner,
    )

    assert code == 0
    assert calls == [{}]
    assert FakePlanner.clients[-1] is client
    assert FakePlanner.contexts[-1] == "local-gh"
    assert stderr.getvalue() == ""


def test_local_materialize_is_one_read_only_control_command_then_local_git(tmp_path):
    calls = []
    FakePlanner.scratch_roots.clear()

    def materialize(**kwargs):
        calls.append(kwargs)
        return {
            "status": "local_materialized",
            "local_path": str(kwargs["output_repo"]),
            "local_branch": kwargs["branch"],
            "commands": ["git -c core.hooksPath=/dev/null cherry-pick -x " + "a" * 40],
        }

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "--auth",
            "gh",
            "local-materialize",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--scratch-root",
            str(tmp_path / "scratch"),
            "--output-repo",
            str(tmp_path / "output"),
            "--branch",
            "local/cherry-pick/train/1",
        ],
        environ={},
        stdout=stdout,
        stderr=stderr,
        local_github_factory=lambda _environment: FakeGitHub("gh-token"),
        planner_factory=FakePlanner,
        local_materializer=materialize,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "local_materialized"
    assert FakePlanner.contexts[-1] == "local-materialize"
    assert FakePlanner.scratch_roots[-1] == tmp_path / "scratch"
    assert calls[0]["branch"] == "local/cherry-pick/train/1"
    assert stderr.getvalue() == ""


def test_local_materialize_does_not_expose_remote_status_publication():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--auth",
                "gh",
                "local-materialize",
                "--source-pr",
                SOURCE,
                "--train",
                "train",
                "--repo-dir",
                "/tmp/repo",
                "--scratch-root",
                "/tmp/scratch",
                "--output-repo",
                "/tmp/output",
                "--branch",
                "local/cherry-pick/train/1",
                "--publish-status",
            ]
        )


def test_local_materialize_requires_gh_auth_and_never_accepts_write_authority(tmp_path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "local-materialize",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--scratch-root",
            str(tmp_path / "scratch"),
            "--output-repo",
            str(tmp_path / "output"),
            "--branch",
            "local/cherry-pick/train/1",
        ],
        environ={"GITHUB_TOKEN": "token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
        write_authority=object(),
    )
    assert code == 2
    assert "requires --auth gh" in stderr.getvalue()


def test_local_create_draft_requires_literal_confirmation_before_planning(tmp_path):
    FakePlanner.calls.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path, mode="create-draft")),
            "--auth",
            "gh",
            "local-create-draft",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--expected-result-file",
            str(tmp_path / "reviewed.json"),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={},
        stdout=stdout,
        stderr=stderr,
        local_github_factory=lambda _environment: FakeGitHub("gh-token"),
        planner_factory=FakePlanner,
    )

    assert code == 2
    assert FakePlanner.calls == []
    assert "confirmation must be the literal CREATE_DRAFT" in stderr.getvalue()


def test_local_create_draft_replans_reviewed_artifact_and_uses_local_authority(
    tmp_path,
):
    catalog_path = config(tmp_path, mode="create-draft")
    expected = FakePlanner(load_config(catalog_path), FakeGitHub("token")).plan(
        SOURCE, "train", {"ROCm/TheRock": tmp_path / "repo"}
    )
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(json.dumps(expected.as_dict()))
    stdout = io.StringIO()
    stderr = io.StringIO()
    FakeWriter.calls.clear()

    code = main(
        [
            "--config",
            str(catalog_path),
            "--auth",
            "gh",
            "local-create-draft",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--expected-result-file",
            str(expected_file),
            "--scratch-root",
            str(tmp_path / "scratch"),
            "--confirm-remote-write",
            "CREATE_DRAFT",
        ],
        environ={},
        stdout=stdout,
        stderr=stderr,
        local_github_factory=lambda _environment: FakeGitHub("gh-token"),
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "draft_created"
    assert is_valid_authority(FakeWriter.calls[-1][-1], "f" * 64)
    assert FakePlanner.contexts[-1] == "local-gh"
    assert FakeWriter.git_environments[-1]["GIT_CONFIG_VALUE_0"] == (
        "!gh auth git-credential"
    )
    assert "GITHUB_TOKEN" not in FakeWriter.git_environments[-1]
    assert stderr.getvalue() == ""


@pytest.mark.parametrize("confirmation", [None, "yes"])
def test_local_create_draft_requires_literal_confirmation(tmp_path, confirmation):
    arguments = [
        "--config",
        str(config(tmp_path, mode="create-draft")),
        "--auth",
        "gh",
        "local-create-draft",
        "--source-pr",
        SOURCE,
        "--train",
        "train",
        "--repo-dir",
        str(tmp_path / "repo"),
        "--expected-result-file",
        str(tmp_path / "expected.json"),
        "--scratch-root",
        str(tmp_path / "scratch"),
    ]
    if confirmation is not None:
        arguments.extend(("--confirm-remote-write", confirmation))
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        arguments,
        environ={},
        stdout=stdout,
        stderr=stderr,
        local_github_factory=lambda _environment: FakeGitHub("gh-token"),
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
    )
    assert code == 2
    assert "confirmation" in stderr.getvalue()


def test_action_only_commands_reject_gh_auth_mode(tmp_path):
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "--auth",
            "gh",
            "action-sync-labels",
            "--train",
            "train",
        ],
        environ={},
        stdout=io.StringIO(),
        stderr=(stderr := io.StringIO()),
        local_github_factory=lambda _environment: FakeGitHub("gh-token"),
    )
    assert code == 2
    assert "GitHub Actions authentication" in stderr.getvalue()


def test_action_create_draft_replans_then_uses_exact_plan_bound_authority(tmp_path):
    expected = FakePlanner(
        load_config(config(tmp_path, mode="create-draft")), FakeGitHub("token")
    ).plan(SOURCE, "train", {"ROCm/TheRock": tmp_path / "repo"})
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(json.dumps(expected.as_dict()))
    stdout = io.StringIO()
    stderr = io.StringIO()
    FakeWriter.calls.clear()
    FakePlanner.revisions.clear()

    code = main(
        [
            "--config",
            str(config(tmp_path, mode="create-draft")),
            "--config-revision",
            "a" * 40,
            "action-create-draft",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--expected-result-file",
            str(expected_file),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "draft_created"
    assert stderr.getvalue() == ""
    assert FakePlanner.revisions[-1] == "a" * 40
    assert is_valid_authority(FakeWriter.calls[-1][-1], "f" * 64)


def test_action_create_draft_blocks_drift_before_constructing_writer(tmp_path):
    expected = FakePlanner(
        load_config(config(tmp_path, mode="create-draft")), FakeGitHub("token")
    ).plan(SOURCE, "train", {"ROCm/TheRock": tmp_path / "repo"})
    expected.evidence["plan_fingerprint"] = "a" * 64
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(json.dumps(expected.as_dict()))
    stdout = io.StringIO()
    stderr = io.StringIO()
    FakeWriter.calls.clear()

    code = main(
        [
            "--config",
            str(config(tmp_path, mode="create-draft")),
            "action-create-draft",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--expected-result-file",
            str(expected_file),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert "revalidation" in stderr.getvalue()
    assert FakeWriter.calls == []


def test_action_reconcile_revalidates_each_expected_plan_before_draft(tmp_path):
    catalog_path = config(tmp_path, mode="create-draft")
    expected = FakePlanner(load_config(catalog_path), FakeGitHub("token")).plan(
        SOURCE, "train", {"ROCm/TheRock": tmp_path / "repo"}
    )
    artifact = tmp_path / "reconcile.json"
    artifact.write_text(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "plan",
                "train_id": "train",
                "results": [expected.as_dict()],
            }
        )
    )
    FakeWriter.calls.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(catalog_path),
            "action-reconcile",
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={tmp_path / 'repo'}",
            "--expected-results-file",
            str(artifact),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
    )

    payload = json.loads(stdout.getvalue())
    assert code == 0
    assert payload["mode"] == "action-create-draft"
    assert payload["results"][0]["status"] == "draft_created"
    assert is_valid_authority(FakeWriter.calls[-1][-1], "f" * 64)
    assert stderr.getvalue() == ""


def test_action_reconcile_drift_is_non_writing_and_visible(tmp_path):
    catalog_path = config(tmp_path, mode="create-draft")
    expected = FakePlanner(load_config(catalog_path), FakeGitHub("token")).plan(
        SOURCE, "train", {"ROCm/TheRock": tmp_path / "repo"}
    )
    expected.evidence["plan_fingerprint"] = "a" * 64
    artifact = tmp_path / "reconcile.json"
    artifact.write_text(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "plan",
                "train_id": "train",
                "results": [expected.as_dict()],
            }
        )
    )
    FakeWriter.calls.clear()
    stdout = io.StringIO()

    code = main(
        [
            "--config",
            str(catalog_path),
            "action-reconcile",
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={tmp_path / 'repo'}",
            "--expected-results-file",
            str(artifact),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "write-token"},
        stdout=stdout,
        stderr=io.StringIO(),
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
        writer_factory=FakeWriter,
    )

    payload = json.loads(stdout.getvalue())
    assert code == 0
    assert payload["results"][0]["status"] == "blocked_evidence"
    assert payload["results"][0]["reason_code"] == "reconciliation_plan_drift"
    assert FakeWriter.calls == []


def test_discover_accepts_unlabeled_event_and_emits_only_automated_train(tmp_path):
    code, stdout, stderr = invoke(
        tmp_path,
        [
            "discover",
            "--labels-json",
            "[]",
            "--event-action",
            "unlabeled",
            "--event-label",
            "cherry-pick:train",
        ],
    )
    assert code == 0
    assert json.loads(stdout) == {"trains": []}  # validate is manual-only
    assert stderr == ""


@pytest.mark.parametrize("event_action", ["edited", "synchronize"])
def test_discover_and_plan_accept_every_declared_pull_request_event(
    tmp_path, event_action
):
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "discover",
            "--labels-json",
            "[]",
            "--event-action",
            event_action,
        ],
    )
    assert code == 0
    assert stderr == ""

    FakePlanner.calls.clear()
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--event-action",
            event_action,
        ],
    )
    assert code == 0
    assert stderr == ""
    assert FakePlanner.calls[-1][-1] == event_action


@pytest.mark.parametrize("labels", ["not-json", "{}", "[1]"])
def test_discover_rejects_invalid_label_json(tmp_path, labels):
    code, _stdout, stderr = invoke(
        tmp_path,
        ["discover", "--labels-json", labels, "--event-action", "labeled"],
    )
    assert code == 2
    assert "labels JSON" in stderr


def test_reconcile_defaults_read_only_and_uses_explicit_repository_map(tmp_path):
    FakePlanner.calls.clear()
    code, stdout, stderr = invoke(
        tmp_path,
        ["reconcile", "--train", "train", "--repo-dir", f"ROCm/TheRock={tmp_path}"],
    )
    payload = json.loads(stdout)
    assert code == 0
    assert payload["mode"] == "plan"
    assert len(payload["results"]) == 1
    assert FakePlanner.calls[-1][2] == {"ROCm/TheRock": tmp_path}
    assert stderr == ""


def test_authorized_sync_and_reconcile_write_paths_use_only_injected_fakes(tmp_path):
    FakeGitHub.instances.clear()
    code, stdout, stderr = invoke(
        tmp_path,
        ["sync-labels", "--train", "train"],
        authority=object(),
    )
    assert code == 0
    assert json.loads(stdout)["status"] == "labels_synchronized"
    assert len(FakeGitHub.instances[-1].labels) == 1
    assert stderr == ""


def test_action_sync_labels_requires_actions_and_uses_only_configured_repositories(
    tmp_path,
):
    FakeGitHub.instances.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "action-sync-labels",
            "--train",
            "train",
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "label-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "labels_synchronized"
    assert FakeGitHub.instances[-1].labels == [
        (
            "ROCm",
            "TheRock",
            "cherry-pick:train",
            "Request a draft cherry-pick for train train",
        )
    ]
    assert stderr.getvalue() == ""

    FakeWriter.calls.clear()
    code, stdout, stderr = invoke(
        tmp_path,
        [
            "reconcile",
            "--train",
            "train",
            "--repo-dir",
            f"ROCm/TheRock={tmp_path}",
            "--create-drafts",
            "--publish-status",
        ],
        authority=object(),
    )
    payload = json.loads(stdout)
    assert code == 0
    assert payload["mode"] == "create-draft"
    assert FakeWriter.calls
    assert len(FakeGitHub.instances[-1].comments) == 1
    assert stderr == ""


def test_plan_publish_status_uses_source_identity(tmp_path):
    FakeGitHub.instances.clear()
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path),
            "--publish-status",
        ],
        authority=object(),
    )
    assert code == 0
    assert len(FakeGitHub.instances[-1].comments) == 1
    assert stderr == ""


def test_reconcile_rejects_missing_or_malformed_repository_maps(tmp_path):
    bad_mappings = (
        ["bad"],
        ["ROCm/rocm-systems=/tmp/not-configured"],
        ["=missing-repository"],
        ["ROCm/TheRock="],
        [f"ROCm/TheRock={tmp_path}", f"ROCm/TheRock={tmp_path}"],
        [f"ROCm/TheRock={tmp_path}", "ROCm/rocm-systems=/unused"],
    )
    for assignments in bad_mappings:
        arguments = ["reconcile", "--train", "train"]
        for assignment in assignments:
            arguments.extend(("--repo-dir", assignment))
        code, _stdout, stderr = invoke(
            tmp_path,
            arguments,
        )
        assert code == 2
        assert "repo-dir" in stderr or "missing" in stderr


def test_publish_result_validates_artifact_and_upserts_status(tmp_path):
    result_file = tmp_path / "result.json"
    result_file.write_text(
        json.dumps(
            Result(
                status=Status.ALREADY_CONTAINED,
                reason_code="ancestor",
                message="contained",
                source_pr=SOURCE,
                source_repository="ROCm/TheRock",
                train_id="train",
                destination_branch="release/test",
            ).as_dict()
        )
    )
    FakeGitHub.instances.clear()
    code, stdout, stderr = invoke(
        tmp_path,
        ["publish-result", "--result-file", str(result_file)],
        authority=object(),
    )
    assert code == 0
    assert json.loads(stdout)["status"] == "already_contained"
    assert len(FakeGitHub.instances[-1].comments) == 1
    assert stderr == ""


def test_action_publish_result_requires_actions_but_not_draft_write_authority(tmp_path):
    result_file = tmp_path / "result.json"
    result_file.write_text(
        json.dumps(
            Result(
                status=Status.ALREADY_CONTAINED,
                reason_code="ancestor",
                message="contained",
                source_pr=SOURCE,
                source_repository="ROCm/TheRock",
                train_id="train",
                destination_branch="release/test",
            ).as_dict()
        )
    )
    FakeGitHub.instances.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "action-publish-result",
            "--result-file",
            str(result_file),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "feedback-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
    )
    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "already_contained"
    assert len(FakeGitHub.instances[-1].comments) == 1
    assert stderr.getvalue() == ""

    stderr = io.StringIO()
    assert (
        main(
            [
                "--config",
                str(config(tmp_path)),
                "action-publish-result",
                "--result-file",
                str(result_file),
            ],
            environ={"GITHUB_TOKEN": "local-token"},
            stdout=io.StringIO(),
            stderr=stderr,
            github_factory=FakeGitHub,
        )
        == 2
    )
    assert "GitHub Actions" in stderr.getvalue()


def test_action_publish_reconciliation_validates_and_publishes_each_result(tmp_path):
    result = Result(
        status=Status.ALREADY_CONTAINED,
        reason_code="ancestor",
        message="contained",
        source_pr=SOURCE,
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
    )
    artifact = tmp_path / "reconcile-result.json"
    artifact.write_text(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "action-create-draft",
                "train_id": "train",
                "results": [result.as_dict()],
            }
        )
    )
    FakeGitHub.instances.clear()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "action-publish-reconciliation",
            "--result-file",
            str(artifact),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "feedback-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "reconciled"
    assert len(FakeGitHub.instances[-1].comments) == 1
    assert stderr.getvalue() == ""


def test_action_publish_reconciliation_rejects_duplicate_results(tmp_path):
    result = Result(
        status=Status.ALREADY_CONTAINED,
        reason_code="ancestor",
        message="contained",
        source_pr=SOURCE,
        source_repository="ROCm/TheRock",
        train_id="train",
        destination_branch="release/test",
    )
    artifact = tmp_path / "duplicate-reconcile-result.json"
    artifact.write_text(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "action-create-draft",
                "train_id": "train",
                "results": [result.as_dict(), result.as_dict()],
            }
        )
    )
    FakeGitHub.instances.clear()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "action-publish-reconciliation",
            "--result-file",
            str(artifact),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "feedback-token"},
        stdout=io.StringIO(),
        stderr=stderr,
        github_factory=FakeGitHub,
    )

    assert code == 2
    assert "duplicates" in stderr.getvalue()
    assert FakeGitHub.instances[-1].comments == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"status": "reconciled", "mode": "plan", "train_id": "train"},
        {"status": "wrong", "mode": "plan", "train_id": "train", "results": []},
        {
            "status": "reconciled",
            "mode": "plan",
            "train_id": "train",
            "results": [
                Result(
                    status=Status.ALREADY_CONTAINED,
                    reason_code="ancestor",
                    message="contained",
                    source_pr="https://github.com/ROCm/rocm-systems/pull/1",
                    source_repository="ROCm/rocm-systems",
                    train_id="train",
                    destination_branch="release/test",
                ).as_dict()
            ],
        },
    ],
)
def test_action_publish_reconciliation_rejects_malformed_boundaries(tmp_path, payload):
    artifact = tmp_path / "malformed-reconcile-result.json"
    artifact.write_text(json.dumps(payload))
    FakeGitHub.instances.clear()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(config(tmp_path)),
            "action-publish-reconciliation",
            "--result-file",
            str(artifact),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "feedback-token"},
        stdout=io.StringIO(),
        stderr=stderr,
        github_factory=FakeGitHub,
    )

    assert code == 2
    assert "invalid reconciliation artifact" in stderr.getvalue()
    assert FakeGitHub.instances[-1].comments == []


def test_unknown_train_is_a_structured_error(tmp_path):
    code, _stdout, stderr = invoke(
        tmp_path,
        [
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "not-configured",
            "--repo-dir",
            str(tmp_path),
        ],
    )

    assert code == 2
    assert "unknown train id" in stderr


def test_malformed_configuration_and_result_are_structured_errors(tmp_path):
    invalid_config = tmp_path / "invalid-config.json"
    invalid_config.write_text("not-json")
    stderr = io.StringIO()
    assert (
        main(
            [
                "--config",
                str(invalid_config),
                "discover",
                "--labels-json",
                "[]",
                "--event-action",
                "labeled",
            ],
            stderr=stderr,
        )
        == 2
    )
    assert "invalid configuration" in stderr.getvalue()

    missing = tmp_path / "missing-result.json"
    code, _stdout, stderr_text = invoke(
        tmp_path,
        ["publish-result", "--result-file", str(missing)],
        authority=object(),
    )
    assert code == 2
    assert "invalid result file" in stderr_text


def test_local_create_draft_rejects_action_auth_before_loading_review_artifacts(
    tmp_path,
):
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(config(tmp_path, mode="create-draft")),
            "local-create-draft",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
            "--expected-result-file",
            str(tmp_path / "expected.json"),
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        environ={},
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 2
    assert "local-create-draft requires --auth gh" in stderr.getvalue()
