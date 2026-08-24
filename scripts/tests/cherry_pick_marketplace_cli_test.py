# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json
from pathlib import Path

import pytest

from scripts.cherry_pick.config import parse_config
from scripts.cherry_pick.marketplace_cli import (
    _repository_map,
    _scratch_root,
    build_parser,
    main,
)
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.release_hub import (
    ReleaseHubConfigSnapshot,
    ReleaseHubSession,
    ReleaseHubError,
)
from scripts.cherry_pick.release_hub_auth import (
    DEVELOPER_CENTRAL_TOKEN_URL,
    Credential,
    CredentialError,
    load_credential,
)

ROOT = Path(__file__).parents[2]
SOURCE = "https://github.com/ROCm/rocm-systems/pull/10031"
TOKEN = "rrh1.abcdefghijkl." + "A" * 43


def config_snapshot():
    payload = json.loads(
        (ROOT / "config/cherry-pick-trains.json").read_text(encoding="utf-8")
    )
    return ReleaseHubConfigSnapshot(
        request_id="request-config",
        generated_at="2026-08-21T12:00:00Z",
        configuration_schema="release-trains.v5",
        configuration_sha256="a" * 64,
        configuration_loaded_at="2026-08-19T12:00:00Z",
        catalog=parse_config(payload),
        catalog_payload=payload,
    )


def test_complete_config_snapshot_contains_the_reviewed_train_catalog():
    current = config_snapshot()
    train = current.catalog.train("10.1-20260811")

    assert train.mode == "validate"
    assert train.state == "active"
    assert train.repositories["ROCm/TheRock"].source_branches == ("main",)
    assert train.repositories["ROCm/rocm-systems"].source_branches == ("develop",)
    assert train.repositories["ROCm/rocm-systems"].destination_branch == (
        "release/bkc/therock-10.1-20260811"
    )
    assert current.configuration_sha256 == "a" * 64


def test_materializer_exceptions_fail_closed_without_leaking_details(tmp_path):
    def fail(**_kwargs):
        raise OSError(f"sensitive path containing {TOKEN}")

    code, stdout, stderr = invoke_operation(
        tmp_path,
        planned_result(),
        "materialize",
        request_factory=lambda _value: object(),
        materializer=fail,
    )

    assert code == 2
    assert stdout == ""
    assert "local materialization failed" in stderr
    assert TOKEN not in stderr


def test_parser_has_no_token_argument_and_exposes_only_local_operations():
    parser = build_parser()
    help_text = parser.format_help()
    assert "--token" not in help_text
    assert "plan" in help_text
    assert "materialize" in help_text
    assert "create-draft" not in help_text
    with pytest.raises(SystemExit):
        parser.parse_args(["--token", TOKEN, "auth", "status"])


def test_missing_credential_stops_before_network_or_github_and_prints_setup_url(
    tmp_path,
):
    called = []

    def missing(**_kwargs):
        raise CredentialError(
            "Create the ROCm Cherry-Pick CLI token at " + DEVELOPER_CENTRAL_TOKEN_URL
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--credential-file",
            str(tmp_path / "missing.json"),
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            f"ROCm/rocm-systems={tmp_path}",
            "--scratch-root",
            str(tmp_path),
        ],
        environ={},
        stdout=stdout,
        stderr=stderr,
        credential_loader=missing,
        release_hub_factory=lambda *_args, **_kwargs: called.append("network"),
        github_factory=lambda *_args, **_kwargs: called.append("github"),
    )
    assert code == 2
    assert stdout.getvalue() == ""
    assert DEVELOPER_CENTRAL_TOKEN_URL in stderr.getvalue()
    assert called == []


def test_auth_login_reads_token_from_stdin_validates_then_saves_privately(tmp_path):
    credential_file = tmp_path / "auth.json"
    seen = []

    class Hub:
        def session(self):
            seen.append("validated")
            return ReleaseHubSession(
                display_name="ROCm Cherry-Pick CLI",
                scopes=("read:evidence",),
                expires_at="2026-09-18T00:00:00Z",
                expires_within_days=None,
            )

    stdout = io.StringIO()
    code = main(
        [
            "--credential-file",
            str(credential_file),
            "auth",
            "login",
            "--stdin",
        ],
        environ={},
        stdin=io.StringIO(TOKEN + "\n"),
        stdout=stdout,
        stderr=io.StringIO(),
        release_hub_factory=lambda _api, token: Hub() if token == TOKEN else None,
    )
    assert code == 0
    assert seen == ["validated"]
    assert (
        load_credential(
            api_origin="https://developer-central.amd.com",
            path=credential_file,
            environ={},
        ).token
        == TOKEN
    )
    assert TOKEN not in stdout.getvalue()
    assert json.loads(stdout.getvalue())["status"] == "authenticated"


def test_auth_hidden_prompt_token_file_status_and_logout_paths(tmp_path):
    session = ReleaseHubSession(
        display_name="ROCm Cherry-Pick CLI",
        scopes=("read:evidence",),
        expires_at="2026-09-18T00:00:00Z",
        expires_within_days=None,
    )

    class Hub:
        def session(self):
            return session

    saved = []
    prompt = []
    for extra, stdin, getpass_func in (
        ([], io.StringIO(), lambda value: prompt.append(value) or TOKEN),
        (
            ["--token-file", str(_private_token_file(tmp_path, TOKEN))],
            io.StringIO(),
            lambda _value: pytest.fail("prompt must not run"),
        ),
    ):
        output = io.StringIO()
        assert (
            main(
                [
                    "--credential-file",
                    str(tmp_path / "auth.json"),
                    "auth",
                    "login",
                    *extra,
                ],
                environ={},
                stdin=stdin,
                stdout=output,
                stderr=io.StringIO(),
                release_hub_factory=lambda _api, _token: Hub(),
                credential_saver=lambda *args: saved.append(args),
                getpass_func=getpass_func,
            )
            == 0
        )
        assert json.loads(output.getvalue())["status"] == "authenticated"
    assert prompt == ["Release Hub API token: "]
    assert len(saved) == 2

    status = io.StringIO()
    assert (
        main(
            ["--credential-file", str(tmp_path / "auth.json"), "auth", "status"],
            environ={},
            stdout=status,
            stderr=io.StringIO(),
            credential_loader=lambda **_kwargs: Credential(TOKEN, "test"),
            release_hub_factory=lambda _api, _token: Hub(),
        )
        == 0
    )
    assert json.loads(status.getvalue())["status"] == "valid"

    for removed, expected in ((True, "removed"), (False, "absent")):
        output = io.StringIO()
        assert (
            main(
                ["--credential-file", str(tmp_path / "auth.json"), "auth", "logout"],
                environ={},
                stdout=output,
                stderr=io.StringIO(),
                credential_remover=lambda *_args, value=removed: value,
            )
            == 0
        )
        assert json.loads(output.getvalue())["status"] == expected


def test_auth_and_api_errors_fail_closed(tmp_path):
    stderr = io.StringIO()
    assert (
        main(
            ["--api", "http://example.com", "auth", "status"],
            environ={},
            stdout=io.StringIO(),
            stderr=stderr,
        )
        == 2
    )
    assert "HTTPS" in stderr.getvalue()

    class BrokenHub:
        def session(self):
            raise ReleaseHubError("authentication failed")

    stderr = io.StringIO()
    assert (
        main(
            ["--credential-file", str(tmp_path / "auth.json"), "auth", "status"],
            environ={},
            stdout=io.StringIO(),
            stderr=stderr,
            credential_loader=lambda **_kwargs: Credential(TOKEN, "test"),
            release_hub_factory=lambda *_args: BrokenHub(),
        )
        == 2
    )
    assert "authentication failed" in stderr.getvalue()


def test_plan_uses_release_hub_snapshot_and_local_only_planner(tmp_path):
    repository = tmp_path / "rocm-systems"
    repository.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    observed = {}

    class Hub:
        def session(self):
            return ReleaseHubSession(
                display_name="ROCm Cherry-Pick CLI",
                scopes=("read:evidence",),
                expires_at="2026-08-22T00:00:00Z",
                expires_within_days=3,
            )

        def cherry_pick_config(self):
            observed["config_endpoint"] = True
            return config_snapshot()

    class Planner:
        def __init__(self, catalog, github, **kwargs):
            observed["train"] = catalog.train("10.1-20260811")
            observed["github"] = github
            observed["planner"] = kwargs

        def plan(self, source_pr, train_id, repositories, **kwargs):
            observed["plan"] = (source_pr, train_id, repositories, kwargs)
            return Result(
                status=Status.DRAFT_PLANNED,
                reason_code="clean_trial_application",
                message="clean",
                evidence={"commands": [["git", "cherry-pick", "-x", "d" * 40]]},
                source_pr=source_pr,
                source_repository="ROCm/rocm-systems",
                train_id=train_id,
                destination_branch="release/bkc/therock-10.1-20260811",
            )

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--credential-file",
            str(tmp_path / "unused.json"),
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "10.1-20260811",
            "--repo-dir",
            f"ROCm/rocm-systems={repository}",
            "--scratch-root",
            str(scratch),
        ],
        environ={},
        stdout=stdout,
        stderr=stderr,
        credential_loader=lambda **_kwargs: Credential(TOKEN, "test"),
        release_hub_factory=lambda *_args: Hub(),
        github_factory=lambda _environ: "github-read-client",
        planner_factory=Planner,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "draft_planned"
    assert "expires in 3 day" in stderr.getvalue()
    assert observed["config_endpoint"] is True
    assert observed["planner"]["execution_context"] == "local-materialize"
    assert observed["planner"]["config_revision"] == "a" * 64
    assert observed["planner"]["control_plane_snapshot"] == config_snapshot().as_dict()
    assert observed["plan"][3]["scratch_root"] == scratch
    assert TOKEN not in stdout.getvalue() + stderr.getvalue()


def test_materialize_success_uses_planned_manifest_and_preserves_commands(tmp_path):
    result = planned_result()
    observed = {}

    def materializer(**kwargs):
        observed.update(kwargs)
        return {
            "status": "local_materialized",
            "commands": [["git", "cherry-pick", "-x", "d" * 40]],
        }

    code, stdout, stderr = invoke_operation(
        tmp_path,
        result,
        "materialize",
        materializer=materializer,
        request_factory=lambda value: ("request", value),
    )
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)["commands"][0][:3] == ["git", "cherry-pick", "-x"]
    assert observed["request"] == ("request", {"schema_version": 3})
    assert observed["output_repo"] == tmp_path / "output"
    assert observed["branch"] == "local/cherry-pick/test"


def test_materialize_stops_for_blocked_invalid_or_failed_materialization(tmp_path):
    blocked = Result(**{**planned_result().__dict__, "status": Status.BLOCKED_CONFLICT})
    called = []
    code, stdout, _stderr = invoke_operation(
        tmp_path,
        blocked,
        "materialize",
        materializer=lambda **_kwargs: called.append(True),
    )
    assert code == 1
    assert json.loads(stdout)["status"] == "blocked_conflict"
    assert called == []

    code, _stdout, stderr = invoke_operation(
        tmp_path,
        planned_result(),
        "materialize",
        request_factory=lambda _value: (_ for _ in ()).throw(
            ValueError("bad manifest")
        ),
    )
    assert code == 2
    assert "invalid core manifest" in stderr

    code, _stdout, _stderr = invoke_operation(
        tmp_path,
        planned_result(),
        "materialize",
        request_factory=lambda _value: object(),
        materializer=lambda **_kwargs: None,
    )
    assert code == 2


@pytest.mark.parametrize(
    "values,source,reason",
    [
        (["missing-separator"], "ROCm/rocm-systems", "OWNER/REPO"),
        (["ROCm/unsupported=/tmp"], "ROCm/rocm-systems", "invalid"),
        (["ROCm/rocm-systems=/missing"], "ROCm/rocm-systems", "unavailable"),
        ([], "ROCm/rocm-systems", "missing"),
    ],
)
def test_repository_map_rejects_invalid_or_missing_paths(values, source, reason):
    with pytest.raises(ValueError, match=reason):
        _repository_map(values, source)


def test_scratch_root_validation(tmp_path):
    relative = Path("relative")
    with pytest.raises(ValueError, match="disk-backed"):
        _scratch_root(relative)
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="disk-backed"):
        _scratch_root(missing)
    assert _scratch_root(tmp_path) == tmp_path


def _private_token_file(tmp_path, token):
    path = tmp_path / f"token-{len(list(tmp_path.glob('token-*')))}"
    path.write_text(token + "\n")
    path.chmod(0o600)
    return path


def planned_result():
    return Result(
        status=Status.DRAFT_PLANNED,
        reason_code="clean_trial_application",
        message="clean",
        evidence={"request_manifest": {"schema_version": 3}},
        source_pr=SOURCE,
        source_repository="ROCm/rocm-systems",
        train_id="10.1-20260811",
        destination_branch="release/bkc/therock-10.1-20260811",
    )


def invoke_operation(
    tmp_path,
    result,
    command,
    *,
    materializer=lambda **_kwargs: pytest.fail("materializer must not run"),
    request_factory=lambda _value: object(),
):
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)

    class Hub:
        def session(self):
            return ReleaseHubSession(
                display_name="ROCm Cherry-Pick CLI",
                scopes=("read:evidence",),
                expires_at="2026-09-18T00:00:00Z",
                expires_within_days=None,
            )

        def cherry_pick_config(self):
            return config_snapshot()

    class Planner:
        def __init__(self, *_args, **_kwargs):
            pass

        def plan(self, *_args, **_kwargs):
            return result

    args = [
        command,
        "--source-pr",
        SOURCE,
        "--train",
        "10.1-20260811",
        "--repo-dir",
        f"ROCm/rocm-systems={repository}",
        "--scratch-root",
        str(scratch),
    ]
    if command == "materialize":
        args.extend(
            [
                "--output-repo",
                str(tmp_path / "output"),
                "--branch",
                "local/cherry-pick/test",
            ]
        )
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        args,
        environ={},
        stdout=stdout,
        stderr=stderr,
        credential_loader=lambda **_kwargs: Credential(TOKEN, "test"),
        release_hub_factory=lambda *_args: Hub(),
        github_factory=lambda _environ: object(),
        planner_factory=Planner,
        materializer=materializer,
        request_factory=request_factory,
    )
    return code, stdout.getvalue(), stderr.getvalue()
