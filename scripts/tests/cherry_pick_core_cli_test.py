# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json
import subprocess
from pathlib import Path

import pytest

from scripts.cherry_pick import core_cli
from scripts.cherry_pick.core import CoreRequest
from scripts.cherry_pick.models import Result, Status
from scripts.tests.cherry_pick_core_test import manifest


REAL_CASE_MANIFEST = (
    Path(__file__).parent / "fixtures/cherry_pick_10153_core_request.json"
)


class FakePlanner:
    calls = []

    def plan(self, request, repositories, *, scratch_root=None):
        self.calls.append((request, repositories, scratch_root))
        return Result(
            status=Status.ALREADY_CONTAINED,
            reason_code="direct_commit_ancestor",
            message="contained",
            evidence={"plan_fingerprint": request.fingerprint()},
            source_pr=request.source.url,
            source_repository=request.source.repository,
            train_id=request.train_id,
            destination_branch=request.source.destination.branch,
        )


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def materialization_fixture(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.com")
    base_file = repo / "base.txt"
    base_file.write_text("base\n")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    source_file = repo / "source.txt"
    source_file.write_text("source\n")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "source")
    source = git(repo, "rev-parse", "HEAD")
    payload = manifest()
    payload["source"].update(
        head_sha=source,
        merge_sha=source,
        ordered_commits=[source],
    )
    payload["source"]["destination"].update(
        branch="release/test",
        head_sha=base,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload))
    return repo, manifest_path, base, source


def test_offline_cli_plans_manifest_without_config_token_or_network(tmp_path):
    manifest_path = tmp_path / "request.json"
    manifest_path.write_text(json.dumps(manifest()))
    stdout = io.StringIO()
    stderr = io.StringIO()
    FakePlanner.calls.clear()

    exit_code = core_cli.main(
        [
            "plan",
            "--manifest",
            str(manifest_path),
            "--repo",
            f"ROCm/TheRock={tmp_path / 'repo'}",
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        stdout=stdout,
        stderr=stderr,
        planner_factory=FakePlanner,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "already_contained"
    assert stderr.getvalue() == ""
    assert FakePlanner.calls[0][1] == {"ROCm/TheRock": tmp_path / "repo"}


def test_offline_cli_accepts_the_frozen_10153_core_request(tmp_path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    FakePlanner.calls.clear()

    exit_code = core_cli.main(
        [
            "plan",
            "--manifest",
            str(REAL_CASE_MANIFEST),
            "--repo",
            f"ROCm/rocm-systems={tmp_path / 'rocm-systems'}",
            "--scratch-root",
            str(tmp_path / "scratch"),
        ],
        stdout=stdout,
        stderr=stderr,
        planner_factory=FakePlanner,
    )

    request = FakePlanner.calls[0][0]
    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert request.source.number == 9716
    prerequisite_numbers = [
        item.number for item in request.prerequisites if hasattr(item, "number")
    ]
    assert prerequisite_numbers == [8221, 9480]
    assert request.coverage_candidates[0].number == 10153


def test_offline_cli_rejects_invalid_manifest_and_repository_mapping(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json")
    for argv, message in (
        (["plan", "--manifest", str(invalid), "--repo", "bad"], "--repo"),
        (
            [
                "plan",
                "--manifest",
                str(invalid),
                "--repo",
                f"ROCm/TheRock={tmp_path}",
            ],
            "manifest",
        ),
    ):
        stderr = io.StringIO()
        assert core_cli.main(argv, stderr=stderr) == 2
        assert message in stderr.getvalue()


def test_offline_cli_rejects_unsupported_empty_and_duplicate_repository_maps(tmp_path):
    manifest_path = tmp_path / "request.json"
    manifest_path.write_text(json.dumps(manifest()))
    for mappings, message in (
        (["someone/fork=/tmp/repo"], "invalid"),
        (["ROCm/TheRock="], "invalid"),
        (
            ["ROCm/TheRock=/tmp/one", "ROCm/TheRock=/tmp/two"],
            "duplicate",
        ),
    ):
        stderr = io.StringIO()
        argv = ["plan", "--manifest", str(manifest_path)]
        for mapping in mappings:
            argv.extend(["--repo", mapping])
        assert core_cli.main(argv, stderr=stderr) == 2
        assert message in stderr.getvalue()


def test_offline_cli_module_has_no_control_plane_import_or_credentials():
    source = Path("scripts/cherry_pick/core_cli.py").read_text()
    for forbidden in (
        "GitHubClient",
        "UrlLibTransport",
        "GITHUB_TOKEN",
        "JIRA",
        "os.environ",
    ):
        assert forbidden not in source


def test_materialize_creates_independent_local_branch_with_exact_tree(tmp_path):
    repo, manifest_path, base, source = materialization_fixture(tmp_path)
    output = tmp_path / "materialized"
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = core_cli.main(
        [
            "materialize",
            "--manifest",
            str(manifest_path),
            "--repo",
            f"ROCm/TheRock={repo}",
            "--scratch-root",
            str(tmp_path / "scratch"),
            "--output-repo",
            str(output),
            "--branch",
            "local/cherry-pick/train/10",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    result = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert result["status"] == "local_materialized"
    assert result["local_path"] == str(output)
    assert result["local_branch"] == "local/cherry-pick/train/10"
    assert git(output, "rev-parse", "HEAD^") == base
    assert git(output, "rev-parse", "HEAD^{tree}") == result["planned_tree"]
    assert source in git(output, "show", "-s", "--format=%B")
    assert result["commands"] == [
        f"git -c core.hooksPath=/dev/null cherry-pick -x {source}"
    ]
    assert git(output, "remote", "get-url", "--push", "origin") == (
        "disabled://local-only"
    )


def test_materialize_refuses_non_planned_result_without_creating_output(tmp_path):
    manifest_path = tmp_path / "request.json"
    manifest_path.write_text(json.dumps(manifest()))
    output = tmp_path / "materialized"
    stdout = io.StringIO()

    exit_code = core_cli.main(
        [
            "materialize",
            "--manifest",
            str(manifest_path),
            "--repo",
            f"ROCm/TheRock={tmp_path / 'repo'}",
            "--output-repo",
            str(output),
            "--branch",
            "local/cherry-pick/train/10",
        ],
        stdout=stdout,
        stderr=io.StringIO(),
        planner_factory=FakePlanner,
    )

    assert exit_code == 1
    assert json.loads(stdout.getvalue())["status"] == "already_contained"
    assert not output.exists()


def test_materialize_rejects_existing_output_or_invalid_branch(tmp_path):
    repo, manifest_path, _base, _source = materialization_fixture(tmp_path)
    output = tmp_path / "materialized"
    output.mkdir()
    for branch, message in (("local/good", "already exists"), ("bad..ref", "branch")):
        stderr = io.StringIO()
        assert (
            core_cli.main(
                [
                    "materialize",
                    "--manifest",
                    str(manifest_path),
                    "--repo",
                    f"ROCm/TheRock={repo}",
                    "--output-repo",
                    str(output),
                    "--branch",
                    branch,
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )
            == 2
        )
        assert message in stderr.getvalue()


def planned_materialization_result(request, **evidence_overrides):
    evidence = {
        "ordered_commits": ["a" * 40],
        "planned_tree": "b" * 40,
        "mainline": None,
        **evidence_overrides,
    }
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        evidence=evidence,
        source_pr=request.source.url,
        source_repository=request.source.repository,
        train_id=request.train_id,
        destination_branch=request.source.destination.branch,
    )


def test_local_materializer_rejects_paths_and_missing_source(tmp_path):
    request = CoreRequest.from_dict(manifest())
    source = tmp_path / "source"
    source.mkdir()
    result = planned_materialization_result(request)
    cases = (
        (Path("relative-output"), {request.source.repository: source}, "absolute"),
        (
            tmp_path / "missing-parent" / "output",
            {request.source.repository: source},
            "parent directory",
        ),
        (tmp_path / "output", {}, "source repository"),
    )
    for output, repositories, message in cases:
        stderr = io.StringIO()
        assert (
            core_cli.materialize_local_checkout(
                request=request,
                result=result,
                repositories=repositories,
                output_repo=output,
                branch="local/cherry-pick/train/10",
                stderr=stderr,
            )
            is None
        )
        assert message in stderr.getvalue()


@pytest.mark.parametrize(
    "overrides",
    (
        {"ordered_commits": "not-a-list"},
        {"ordered_commits": []},
        {"ordered_commits": [None]},
        {"planned_tree": None},
    ),
)
def test_local_materializer_rejects_incomplete_core_evidence(tmp_path, overrides):
    request = CoreRequest.from_dict(manifest())
    source = tmp_path / "source"
    source.mkdir()
    stderr = io.StringIO()

    assert (
        core_cli.materialize_local_checkout(
            request=request,
            result=planned_materialization_result(request, **overrides),
            repositories={request.source.repository: source},
            output_repo=tmp_path / "output",
            branch="local/cherry-pick/train/10",
            stderr=stderr,
        )
        is None
    )
    assert "omitted materialization evidence" in stderr.getvalue()


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        ("setup", "local Git setup failed"),
        ("cherry-pick", "local cherry-pick failed"),
        ("tree-read", "does not match"),
        ("head-read", "does not match"),
        ("tree-mismatch", "does not match"),
    ),
)
def test_local_materializer_fails_closed_on_git_or_tree_error(
    tmp_path, monkeypatch, failure, message
):
    request = CoreRequest.from_dict(manifest())
    source = tmp_path / "source"
    source.mkdir()
    calls = []

    def fake_git(_repo, *args):
        index = len(calls)
        calls.append(args)
        returncode = 0
        stdout = ""
        if failure == "setup" and index == 0:
            returncode = 1
        elif failure == "cherry-pick" and index == 5:
            returncode = 1
        elif failure == "tree-read" and index == 6:
            returncode = 1
        elif failure == "head-read" and index == 7:
            returncode = 1
        elif index == 6:
            stdout = "c" * 40 + "\n"
        elif index == 7:
            stdout = "d" * 40 + "\n"
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(core_cli, "_git", fake_git)
    stderr = io.StringIO()
    assert (
        core_cli.materialize_local_checkout(
            request=request,
            result=planned_materialization_result(request),
            repositories={request.source.repository: source},
            output_repo=tmp_path / "output",
            branch="local/cherry-pick/train/10",
            stderr=stderr,
        )
        is None
    )
    assert message in stderr.getvalue()
    assert not (tmp_path / "output").exists()
