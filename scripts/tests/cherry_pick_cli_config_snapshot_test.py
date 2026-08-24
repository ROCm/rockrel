# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import io
import json

import pytest

from scripts.cherry_pick.__main__ import main
from scripts.cherry_pick.config import parse_config
from scripts.cherry_pick.control_plane import write_config_snapshot
from scripts.cherry_pick.release_hub import ReleaseHubConfigSnapshot
from scripts.tests.cherry_pick_cli_test import (
    SOURCE,
    FakeGitHub,
    FakePlanner,
    config,
)


def test_plan_loads_a_digest_bound_complete_config_snapshot(tmp_path):
    payload = json.loads(config(tmp_path).read_text())
    snapshot = ReleaseHubConfigSnapshot(
        request_id="request-config",
        generated_at="2026-08-21T12:00:00Z",
        configuration_schema="release-trains.v5",
        configuration_sha256="a" * 64,
        configuration_loaded_at="2026-08-21T11:59:00Z",
        catalog=parse_config(payload),
        catalog_payload=payload,
    )
    snapshot_path = tmp_path / "config-snapshot.json"
    write_config_snapshot(snapshot_path, snapshot)
    FakePlanner.revisions.clear()
    FakePlanner.control_plane_snapshots.clear()

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config-snapshot",
            str(snapshot_path),
            "--expected-config-sha256",
            "a" * 64,
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
        ],
        environ={"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "read-token"},
        stdout=stdout,
        stderr=stderr,
        github_factory=FakeGitHub,
        planner_factory=FakePlanner,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["status"] == "draft_planned"
    assert FakePlanner.revisions[-1] == "a" * 64
    assert FakePlanner.control_plane_snapshots[-1] == snapshot.as_dict()


def test_config_snapshot_digest_drift_fails_before_github_authentication(tmp_path):
    payload = json.loads(config(tmp_path).read_text())
    snapshot = ReleaseHubConfigSnapshot(
        request_id="request-config",
        generated_at="2026-08-21T12:00:00Z",
        configuration_schema="release-trains.v5",
        configuration_sha256="a" * 64,
        configuration_loaded_at="2026-08-21T11:59:00Z",
        catalog=parse_config(payload),
        catalog_payload=payload,
    )
    snapshot_path = tmp_path / "config-snapshot.json"
    write_config_snapshot(snapshot_path, snapshot)
    called = []
    stderr = io.StringIO()

    code = main(
        [
            "--config-snapshot",
            str(snapshot_path),
            "--expected-config-sha256",
            "b" * 64,
            "discover",
            "--labels-json",
            "[]",
            "--event-action",
            "edited",
        ],
        stderr=stderr,
        github_factory=lambda *_args: called.append(True),
    )

    assert code == 2
    assert "digest" in stderr.getvalue()
    assert called == []


@pytest.mark.parametrize(
    "arguments,reason",
    [
        (
            ["--config-snapshot", "snapshot.json"],
            "--expected-config-sha256 is required",
        ),
        (
            [
                "--config-snapshot",
                "snapshot.json",
                "--expected-config-sha256",
                "a" * 64,
                "--config-revision",
                "b" * 40,
            ],
            "cannot be combined",
        ),
    ],
)
def test_config_snapshot_requires_one_digest_binding_source(
    tmp_path, arguments, reason
):
    payload = json.loads(config(tmp_path).read_text())
    snapshot = ReleaseHubConfigSnapshot(
        request_id="request-config",
        generated_at="2026-08-21T12:00:00Z",
        configuration_schema="release-trains.v5",
        configuration_sha256="a" * 64,
        configuration_loaded_at="2026-08-21T11:59:00Z",
        catalog=parse_config(payload),
        catalog_payload=payload,
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_config_snapshot(snapshot_path, snapshot)
    arguments = [
        str(snapshot_path) if item == "snapshot.json" else item for item in arguments
    ]
    called = []
    stderr = io.StringIO()

    code = main(
        [
            *arguments,
            "plan",
            "--source-pr",
            SOURCE,
            "--train",
            "train",
            "--repo-dir",
            str(tmp_path / "repo"),
        ],
        stderr=stderr,
        github_factory=lambda *_args: called.append(True),
    )

    assert code == 2
    assert reason in stderr.getvalue()
    assert called == []


def test_local_config_rejects_snapshot_digest_and_malformed_revision(tmp_path):
    config_path = config(tmp_path)
    called = []

    for extra, reason in (
        (["--expected-config-sha256", "a" * 64], "requires --config-snapshot"),
        (["--config-revision", "NOT-A-REVISION"], "lowercase Git SHA"),
    ):
        stderr = io.StringIO()
        code = main(
            [
                "--config",
                str(config_path),
                *extra,
                "plan",
                "--source-pr",
                SOURCE,
                "--train",
                "train",
                "--repo-dir",
                str(tmp_path / "repo"),
            ],
            stderr=stderr,
            github_factory=lambda *_args: called.append(True),
        )

        assert code == 2
