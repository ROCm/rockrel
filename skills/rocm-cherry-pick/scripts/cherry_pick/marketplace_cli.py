# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Self-contained, local-only CLI distributed with the SLAI skill."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .config import ConfigError, SUPPORTED_REPOSITORIES
from .core import CoreRequest, ManifestError
from .core_cli import materialize_local_checkout
from .github_read import (
    ReadOnlyGitHubError,
    gh_github_read_client,
    parse_pull_request_url,
)
from .models import Status
from .orchestrator import Planner
from .release_hub import (
    ReleaseHubClient,
    ReleaseHubError,
    validate_api_origin,
)
from .release_hub_auth import (
    CredentialError,
    default_credential_path,
    load_credential,
    read_token_file,
    remove_credential,
    save_credential,
    validate_token,
)

DEFAULT_API_ORIGIN = "https://developer-central.amd.com"
LIVE_CONFIG_ENDPOINT = "/api/v1/cherry-pick/config"
LIVE_CONFIG_SCHEMA = "release-trains.v5"


def build_parser() -> argparse.ArgumentParser:
    """Define local auth, planning, and materialization commands without remote writes."""

    parser = argparse.ArgumentParser(
        description=(
            "Plan or materialize an exact ROCm cherry-pick locally. "
            "This command cannot create a remote branch or pull request."
        )
    )
    parser.add_argument("--api", help="Release Hub API origin")
    parser.add_argument("--credential-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    auth = commands.add_parser("auth", help="Manage the private Release Hub credential")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_commands.add_parser(
        "login", help="Validate and save a Release Hub token"
    )
    token_input = login.add_mutually_exclusive_group()
    token_input.add_argument(
        "--stdin", action="store_true", help="Read the token from standard input"
    )
    token_input.add_argument(
        "--token-file", type=Path, help="Read the token from a private file"
    )
    auth_commands.add_parser("status", help="Validate the configured Release Hub token")
    auth_commands.add_parser(
        "logout", help="Remove the saved token for this API origin"
    )

    for name in ("plan", "materialize"):
        operation = commands.add_parser(name)
        operation.add_argument("--source-pr", required=True)
        operation.add_argument("--train", required=True)
        operation.add_argument(
            "--repo-dir",
            action="append",
            required=True,
            metavar="OWNER/REPO=PATH",
        )
        operation.add_argument("--scratch-root", required=True, type=Path)
        if name == "materialize":
            operation.add_argument("--output-repo", required=True, type=Path)
            operation.add_argument("--branch", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    credential_loader=load_credential,
    credential_saver=save_credential,
    credential_remover=remove_credential,
    release_hub_factory=ReleaseHubClient,
    github_factory=gh_github_read_client,
    planner_factory=Planner,
    materializer=materialize_local_checkout,
    request_factory=CoreRequest.from_dict,
    getpass_func=getpass.getpass,
) -> int:
    """Run credential management or read-only planning against reviewed live configuration."""

    args = build_parser().parse_args(argv)
    try:
        api_origin = validate_api_origin(
            args.api
            or environ.get("ROCM_RELEASE_HUB_API", "").strip()
            or DEFAULT_API_ORIGIN
        )
    except ReleaseHubError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
    credential_path = args.credential_file or default_credential_path(environ)

    if args.command == "auth":
        return _auth_command(
            args,
            api_origin=api_origin,
            credential_path=credential_path,
            environ=environ,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            credential_loader=credential_loader,
            credential_saver=credential_saver,
            credential_remover=credential_remover,
            release_hub_factory=release_hub_factory,
            getpass_func=getpass_func,
        )

    try:
        source_owner, source_name, _number = parse_pull_request_url(args.source_pr)
        source_repository = f"{source_owner}/{source_name}"
        repositories = _repository_map(args.repo_dir, source_repository)
        scratch_root = _scratch_root(args.scratch_root)
        credential = credential_loader(
            api_origin=api_origin,
            path=credential_path,
            environ=environ,
        )
        hub = release_hub_factory(api_origin, credential.token)
        session = hub.session()
        if session.expires_within_days is not None:
            print(
                "warning: Release Hub token expires in "
                f"{session.expires_within_days} day(s); rotate it in Developer Central.",
                file=stderr,
            )
        snapshot = hub.cherry_pick_config()
        if snapshot.configuration_schema != LIVE_CONFIG_SCHEMA:
            raise ReleaseHubError(
                f"Developer Central {LIVE_CONFIG_ENDPOINT} returned an unsupported source schema."
            )
        catalog = snapshot.catalog
        train = catalog.train(args.train)
        if source_repository not in train.repositories:
            raise ConfigError(
                "Developer Central configuration does not authorize the source repository."
            )
        revision = snapshot.configuration_sha256
        github = github_factory(environ)
        planner = planner_factory(
            catalog,
            github,
            config_revision=revision,
            execution_context="local-materialize",
            control_plane_snapshot=snapshot.as_dict(),
        )
        result = planner.plan(
            args.source_pr,
            args.train,
            repositories,
            event_action="manual",
            scratch_root=scratch_root,
        )
    except (
        ConfigError,
        CredentialError,
        ReadOnlyGitHubError,
        ReleaseHubError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=stderr)
        return 2

    if args.command == "plan":
        print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
        return 0

    if result.status is not Status.DRAFT_PLANNED:
        print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
        return 1
    try:
        request = request_factory(result.evidence.get("request_manifest"))
    except (ManifestError, TypeError, ValueError) as exc:
        print(f"error: planner produced an invalid core manifest: {exc}", file=stderr)
        return 2
    try:
        materialized = materializer(
            request=request,
            result=result,
            repositories=repositories,
            output_repo=args.output_repo,
            branch=args.branch,
            stderr=stderr,
        )
    except (OSError, RuntimeError):
        print("error: local materialization failed", file=stderr)
        return 2
    if materialized is None:
        return 2
    print(json.dumps(materialized, sort_keys=True), file=stdout)
    return 0


def _auth_command(
    args: argparse.Namespace,
    *,
    api_origin: str,
    credential_path: Path,
    environ: Mapping[str, str],
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    credential_loader,
    credential_saver,
    credential_remover,
    release_hub_factory,
    getpass_func,
) -> int:
    """Execute one local credential-management subcommand."""

    try:
        if args.auth_command == "logout":
            removed = credential_remover(credential_path, api_origin)
            print(
                json.dumps(
                    {"api": api_origin, "status": "removed" if removed else "absent"},
                    sort_keys=True,
                ),
                file=stdout,
            )
            return 0
        if args.auth_command == "login":
            if args.stdin:
                token = validate_token(stdin.readline(1025))
            elif args.token_file is not None:
                token = read_token_file(args.token_file)
            else:
                token = validate_token(getpass_func("Release Hub API token: "))
            session = release_hub_factory(api_origin, token).session()
            credential_saver(credential_path, api_origin, token)
            payload = _session_payload(api_origin, session, "authenticated")
        else:
            credential = credential_loader(
                api_origin=api_origin,
                path=credential_path,
                environ=environ,
            )
            session = release_hub_factory(api_origin, credential.token).session()
            payload = _session_payload(api_origin, session, "valid")
        print(json.dumps(payload, sort_keys=True), file=stdout)
        return 0
    except (CredentialError, ReleaseHubError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=stderr)
        return 2


def _session_payload(api_origin, session, status):
    """Serialize authenticated Release Hub session metadata for CLI output."""

    return {
        "api": api_origin,
        "status": status,
        "display_name": session.display_name,
        "scopes": list(session.scopes),
        "expires_at": session.expires_at,
        "expires_within_days": session.expires_within_days,
    }


def _repository_map(values: Sequence[str], source_repository: str) -> dict[str, Path]:
    """Parse and validate repository-to-checkout command-line mappings."""

    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--repo-dir must be OWNER/REPO=PATH")
        repository, raw_path = value.split("=", 1)
        path = Path(raw_path)
        if (
            repository not in SUPPORTED_REPOSITORIES
            or not raw_path
            or repository in result
        ):
            raise ValueError(f"invalid --repo-dir mapping: {value!r}")
        if not path.is_dir():
            raise ValueError(f"local repository directory is unavailable: {repository}")
        result[repository] = path
    if source_repository not in result:
        raise ValueError(f"--repo-dir is missing {source_repository}")
    return result


def _scratch_root(path: Path) -> Path:
    """Validate and return the disk-backed scratch directory."""

    if not path.is_absolute() or not path.is_dir():
        raise ValueError(
            "--scratch-root must be an existing absolute disk-backed directory"
        )
    return path
