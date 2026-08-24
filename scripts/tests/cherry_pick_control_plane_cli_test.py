# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json

from scripts.cherry_pick import control_plane_cli
from scripts.cherry_pick.release_hub import (
    ReleaseHubClient,
    ReleaseHubError,
)
from scripts.tests.cherry_pick_config_api_test import SOURCE_SHA, TOKEN, config_payload


def snapshot():
    return ReleaseHubClient(
        "https://developer-central.amd.com",
        TOKEN,
        transport=lambda *_args: config_payload(),
    ).cherry_pick_config()


def test_action_fetch_config_writes_one_snapshot_and_prints_only_safe_metadata(
    tmp_path,
):
    output = tmp_path / "config-snapshot.json"
    observed = {}

    def fetcher(api_origin, environment):
        observed["environment"] = environment
        observed["api_origin"] = api_origin
        return snapshot()

    def writer(path, value):
        observed["write"] = (path, value)
        path.write_text(json.dumps(value.as_dict()))

    stdout = io.StringIO()
    stderr = io.StringIO()
    environment = {
        "GITHUB_ACTIONS": "true",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-secret",
    }

    code = control_plane_cli.main(
        [
            "action-fetch-config",
            "--api",
            "https://developer-central.amd.com",
            "--output",
            str(output),
        ],
        environ=environment,
        stdout=stdout,
        stderr=stderr,
        fetcher=fetcher,
        snapshot_writer=writer,
    )

    assert code == 0
    assert observed["api_origin"] == "https://developer-central.amd.com"
    assert observed["environment"] is environment
    assert observed["write"] == (output, observed["write"][1])
    assert observed["write"][1].configuration_sha256 == SOURCE_SHA
    assert json.loads(stdout.getvalue()) == {
        "configuration_sha256": SOURCE_SHA,
        "request_id": "request-config",
        "schema_version": "release-hub-config-snapshot.v1",
    }
    assert stderr.getvalue() == ""
    assert "request-secret" not in stdout.getvalue()


def test_action_fetch_config_requires_an_absolute_output_path(tmp_path):
    stderr = io.StringIO()

    code = control_plane_cli.main(
        ["action-fetch-config", "--output", "relative.json"],
        environ={},
        stdout=io.StringIO(),
        stderr=stderr,
        fetcher=lambda *_args, **_kwargs: snapshot(),
    )

    assert code == 2
    assert "absolute" in stderr.getvalue()


def test_action_fetch_config_sanitizes_transport_failures(tmp_path):
    secret = "oidc-request-secret"
    stderr = io.StringIO()

    code = control_plane_cli.main(
        ["action-fetch-config", "--output", str(tmp_path / "config.json")],
        environ={},
        stdout=io.StringIO(),
        stderr=stderr,
        fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ReleaseHubError("failure " + secret)
        ),
    )

    assert code == 2
    assert "configuration fetch failed" in stderr.getvalue()
    assert secret not in stderr.getvalue()


def test_control_plane_module_entrypoint_invokes_the_cli_parser():
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.cherry_pick.control_plane_cli",
            "--help",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert "action-fetch-config" in completed.stdout
