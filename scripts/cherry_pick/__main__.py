# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Command-line entry point for label-driven cherry-pick automation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .action_runtime import (
    ActionRuntimeError,
    action_github_client,
    require_action_runtime,
    revalidate_action_frontier_authorities,
    revalidate_action_write_authority,
)
from .clients import parse_pull_request_url
from .config import ConfigError, TrainCatalog, load_config
from .control_plane import load_config_snapshot
from .feedback import publish_result_feedback
from .git_auth import gh_git_environment
from .local_runtime import (
    CONFIRMATION,
    LocalRuntimeError,
    gh_github_client,
    revalidate_local_write_authority,
)
from .models import Result, Status
from .release_hub import ReleaseHubError
from .core import CoreRequest, ManifestError
from .core_cli import materialize_local_checkout
from .orchestrator import (
    Planner,
    discover_train_ids,
    render_status_comment,
    status_marker,
)
from .writer import DraftWriter

REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def build_parser() -> argparse.ArgumentParser:
    """Build the complete parser for discovery, planning, and write commands."""
    parser = argparse.ArgumentParser(
        description="Plan draft ROCm destination-branch cherry-picks."
    )
    config_source = parser.add_mutually_exclusive_group(required=True)
    config_source.add_argument("--config", type=Path)
    config_source.add_argument("--config-snapshot", type=Path)
    parser.add_argument("--config-revision")
    parser.add_argument("--expected-config-sha256")
    parser.add_argument("--auth", choices=("action", "gh"), default="action")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--labels-json", required=True)
    discover.add_argument(
        "--event-action",
        choices=("labeled", "unlabeled", "edited", "synchronize", "closed"),
        required=True,
    )
    discover.add_argument("--event-label", default="")

    for command in (
        "plan",
        "create-draft",
        "action-create-draft",
        "local-create-draft",
        "local-materialize",
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--source-pr", required=True)
        subparser.add_argument("--train", required=True)
        subparser.add_argument(
            "--repo-dir",
            action="append",
            required=True,
            metavar="PATH|OWNER/REPO=PATH",
        )
        if command != "local-materialize":
            subparser.add_argument("--publish-status", action="store_true")
        subparser.add_argument(
            "--event-action",
            choices=(
                "labeled",
                "unlabeled",
                "edited",
                "synchronize",
                "closed",
                "manual",
            ),
            default="manual",
        )
        if command in {"action-create-draft", "local-create-draft"}:
            subparser.add_argument("--expected-result-file", type=Path, required=True)
            subparser.add_argument("--scratch-root", type=Path, required=True)
        if command == "local-create-draft":
            subparser.add_argument("--confirm-remote-write")
        if command == "local-materialize":
            subparser.add_argument("--scratch-root", type=Path, required=True)
            subparser.add_argument("--output-repo", type=Path, required=True)
            subparser.add_argument("--branch", required=True)

    for command in ("sync-labels", "action-sync-labels"):
        sync = subparsers.add_parser(command)
        sync.add_argument("--train", required=True)

    for command in ("publish-result", "action-publish-result"):
        publish = subparsers.add_parser(command)
        publish.add_argument("--result-file", type=Path, required=True)
    publish_reconciliation = subparsers.add_parser("action-publish-reconciliation")
    publish_reconciliation.add_argument("--result-file", type=Path, required=True)

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--train", required=True)
    reconcile.add_argument(
        "--repo-dir",
        action="append",
        required=True,
        metavar="OWNER/REPO=PATH",
    )
    reconcile.add_argument("--publish-status", action="store_true")
    reconcile.add_argument("--create-drafts", action="store_true")

    action_reconcile = subparsers.add_parser("action-reconcile")
    action_reconcile.add_argument("--train", required=True)
    action_reconcile.add_argument(
        "--repo-dir",
        action="append",
        required=True,
        metavar="OWNER/REPO=PATH",
    )
    action_reconcile.add_argument("--expected-results-file", type=Path, required=True)
    action_reconcile.add_argument("--scratch-root", type=Path, required=True)
    return parser


def _credential(environ: Mapping[str, str], name: str, stderr: TextIO) -> str | None:
    """Read one required credential without exposing other environment contents."""
    value = environ.get(name)
    if value:
        return value
    print(f"error: required environment variable {name} is not set", file=stderr)
    return None


def _needs_write_authority(args: argparse.Namespace) -> bool:
    """Return whether the selected local-review command can write remotely."""
    return bool(
        args.command in {"create-draft", "sync-labels", "publish-result"}
        or (args.command == "reconcile" and args.create_drafts)
        or getattr(args, "publish_status", False)
    )


def _request_repository_map(
    values: Sequence[str], source_repository: str
) -> dict[str, Path]:
    """Parse checkout assignments required by one source request."""
    if len(values) == 1 and "=" not in values[0]:
        return {source_repository: Path(values[0])}
    result: dict[str, Path] = {}
    for assignment in values:
        if "=" not in assignment:
            raise ValueError(
                "--repo-dir entries must all be OWNER/REPO=PATH when multiple are used"
            )
        repository, raw_path = assignment.split("=", 1)
        if not repository or not raw_path or repository in result:
            raise ValueError(f"invalid --repo-dir mapping: {assignment!r}")
        result[repository] = Path(raw_path)
    if source_repository not in result:
        raise ValueError(
            f"--repo-dir is missing the source repository {source_repository}"
        )
    return result


def _reconciliation_repository_map(
    values: Sequence[str], expected_repositories: set[str]
) -> dict[str, Path]:
    """Parse exact checkout assignments for every train repository."""
    result: dict[str, Path] = {}
    for assignment in values:
        if "=" not in assignment:
            raise ValueError(f"--repo-dir must be OWNER/REPO=PATH, got {assignment!r}")
        repository, raw_path = assignment.split("=", 1)
        if (
            not repository
            or not raw_path
            or repository in result
            or repository not in expected_repositories
        ):
            raise ValueError(f"invalid --repo-dir mapping: {assignment!r}")
        result[repository] = Path(raw_path)
    missing = sorted(expected_repositories - set(result))
    if missing:
        raise ValueError("missing --repo-dir mappings for " + ", ".join(missing))
    return result


def _write_action_result(
    *,
    environ: Mapping[str, str],
    train,
    expected: Result,
    current: Result,
    github,
    writer_factory,
    repository_paths: Mapping[str, Path],
    scratch_root: Path,
) -> Result:
    """Revalidate an exact Actions plan before any capability-bound write."""
    if (
        current.status is Status.AWAITING_DEPENDENCIES
        and current.reason_code == "managed_dependency_frontier"
    ):
        authorities = revalidate_action_frontier_authorities(
            environ,
            train_mode=train.mode,
            expected=expected,
            current=current,
        )
        raw_frontier = current.evidence.get("managed_frontier_results")
        if not isinstance(raw_frontier, list):
            raise ValueError("managed frontier results are unavailable")
        write_results: list[dict[str, object]] = []
        for raw_item in raw_frontier:
            frontier = Result.from_dict(raw_item)
            if (
                frontier.source_pr not in authorities
                or frontier.source_repository not in repository_paths
            ):
                raise ValueError(
                    "managed frontier repository or authority is unavailable"
                )
            writer = writer_factory(
                github,
                capability=authorities[frontier.source_pr],
                scratch_root=scratch_root,
            )
            written = writer.create(
                repository_paths[frontier.source_repository],
                train,
                frontier,
            )
            write_results.append(written.as_dict())
        return Result(
            status=current.status,
            reason_code=current.reason_code,
            message=(
                "The exact managed dependency frontier was processed; "
                "the root remains gated until dependencies are contained."
            ),
            evidence={
                **current.evidence,
                "managed_frontier_write_results": write_results,
            },
            source_pr=current.source_pr,
            source_repository=current.source_repository,
            train_id=current.train_id,
            destination_branch=current.destination_branch,
        )
    authority = revalidate_action_write_authority(
        environ,
        train_mode=train.mode,
        expected=expected,
        current=current,
    )
    if current.source_repository not in repository_paths:
        raise ValueError("source repository checkout is unavailable")
    writer = writer_factory(
        github,
        capability=authority,
        scratch_root=scratch_root,
    )
    return writer.create(
        repository_paths[current.source_repository],
        train,
        current,
    )


@dataclass(frozen=True)
class _LoadedConfiguration:
    """Configuration facts shared by every post-discovery command."""

    catalog: TrainCatalog
    revision: str
    control_plane_snapshot: dict[str, object] | None


def _load_cli_configuration(args: argparse.Namespace) -> _LoadedConfiguration:
    """Load one local catalog or digest-bound Release Hub snapshot."""

    control_plane_snapshot: dict[str, object] | None = None
    if args.config_snapshot is not None:
        if args.expected_config_sha256 is None:
            raise ConfigError(
                "--expected-config-sha256 is required with --config-snapshot"
            )
        if args.config_revision is not None:
            raise ConfigError(
                "--config-revision cannot be combined with --config-snapshot"
            )
        snapshot = load_config_snapshot(
            args.config_snapshot,
            expected_sha256=args.expected_config_sha256,
        )
        catalog = snapshot.catalog
        revision = snapshot.configuration_sha256
        control_plane_snapshot = snapshot.as_dict()
    else:
        if args.expected_config_sha256 is not None:
            raise ConfigError("--expected-config-sha256 requires --config-snapshot")
        catalog = load_config(args.config)
        revision = args.config_revision or "0" * 40
    # Some injected planners inspect the parsed namespace, so retain the
    # historical normalization in addition to returning immutable state.
    args.config_revision = revision
    return _LoadedConfiguration(catalog, revision, control_plane_snapshot)


def _run_discover(
    args: argparse.Namespace,
    catalog: TrainCatalog,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Resolve active trains from one label event without authenticating."""

    try:
        labels = json.loads(args.labels_json)
    except json.JSONDecodeError as exc:
        print(f"error: labels JSON is invalid: {exc}", file=stderr)
        return 2
    if not isinstance(labels, list) or any(
        not isinstance(item, str) for item in labels
    ):
        print("error: labels JSON must be an array of strings", file=stderr)
        return 2
    trains = discover_train_ids(
        catalog,
        current_labels=tuple(labels),
        event_action=args.event_action,
        event_label=args.event_label,
    )
    print(json.dumps({"trains": list(trains)}, sort_keys=True), file=stdout)
    return 0


def _reject_invalid_runtime(
    args: argparse.Namespace,
    revision: str,
    write_authority: object | None,
    stderr: TextIO,
) -> bool:
    """Print the first runtime-contract violation and report whether to stop."""

    if REVISION_RE.fullmatch(revision) is None:
        print(
            "error: configuration revision must be a lowercase Git SHA or SHA-256 digest",
            file=stderr,
        )
        return True
    action_only = {
        "action-create-draft",
        "action-sync-labels",
        "action-publish-result",
        "action-publish-reconciliation",
        "action-reconcile",
    }
    if args.command in action_only and args.auth != "action":
        print(
            "error: action-* commands require GitHub Actions authentication",
            file=stderr,
        )
        return True
    if args.command == "local-create-draft":
        if args.auth != "gh":
            print("error: local-create-draft requires --auth gh", file=stderr)
            return True
        if args.confirm_remote_write != CONFIRMATION:
            print(
                f"error: remote write confirmation must be the literal {CONFIRMATION}",
                file=stderr,
            )
            return True
    if args.command == "local-materialize" and args.auth != "gh":
        print("error: local-materialize requires --auth gh", file=stderr)
        return True
    if _needs_write_authority(args) and write_authority is None:
        print(
            "error: remote write authority is unavailable in local-review mode",
            file=stderr,
        )
        return True
    return False


def _build_github_client(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    stderr: TextIO,
    github_factory,
    local_github_factory,
):
    """Construct the selected GitHub client, printing sanitized auth failures."""

    if args.auth == "gh":
        try:
            return local_github_factory(environ)
        except LocalRuntimeError as exc:
            print(f"error: {exc}", file=stderr)
            return None
    github_token = _credential(environ, "GITHUB_TOKEN", stderr)
    if github_token is None:
        return None
    if github_factory is not None:
        return github_factory(github_token)
    try:
        return action_github_client(environ)
    except ActionRuntimeError as exc:
        print(f"error: {exc}", file=stderr)
        return None


def _run_publish_result(
    args: argparse.Namespace,
    catalog: TrainCatalog,
    github,
    environ: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Validate and publish one serialized planning result."""

    try:
        if args.command == "action-publish-result":
            require_action_runtime(environ)
        payload = json.loads(args.result_file.read_text())
        result = Result.from_dict(payload)
        catalog.train(result.train_id or "")
        publish_result_feedback(github, result)
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ActionRuntimeError,
    ) as exc:
        print(f"error: invalid result file: {exc}", file=stderr)
        return 2
    print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
    return 0


def _run_publish_reconciliation(
    args: argparse.Namespace,
    catalog: TrainCatalog,
    github,
    environ: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Validate and publish every unique result in a reconciliation artifact."""

    try:
        require_action_runtime(environ)
        artifact = json.loads(args.result_file.read_text())
        if not isinstance(artifact, dict):
            raise ValueError("reconciliation artifact must be an object")
        if set(artifact) != {"status", "mode", "train_id", "results"}:
            raise ValueError("reconciliation artifact fields are invalid")
        train_id = artifact.get("train_id")
        results = artifact.get("results")
        if (
            artifact.get("status") != "reconciled"
            or artifact.get("mode") not in {"plan", "action-create-draft"}
            or not isinstance(train_id, str)
            or not isinstance(results, list)
        ):
            raise ValueError("reconciliation artifact identity is invalid")
        train = catalog.train(train_id)
        parsed_results = [Result.from_dict(item) for item in results]
        identities = [(item.source_pr, item.train_id) for item in parsed_results]
        if len(set(identities)) != len(identities):
            raise ValueError("reconciliation artifact contains duplicates")
        if any(
            item.train_id != train.id
            or item.source_repository not in train.repositories
            for item in parsed_results
        ):
            raise ValueError("reconciliation result is outside the train")
        for item in parsed_results:
            publish_result_feedback(github, item)
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ActionRuntimeError,
    ) as exc:
        print(f"error: invalid reconciliation artifact: {exc}", file=stderr)
        return 2
    print(json.dumps(artifact, sort_keys=True), file=stdout)
    return 0


def _run_sync_labels(
    args: argparse.Namespace,
    train,
    github,
    environ: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Synchronize the reviewed train label across configured repositories."""

    if args.command == "action-sync-labels":
        try:
            require_action_runtime(environ)
        except ActionRuntimeError as exc:
            print(f"error: {exc}", file=stderr)
            return 2
    for repository in train.repositories:
        owner, repo = repository.split("/", 1)
        github.ensure_label(
            owner,
            repo,
            name=train.label,
            description=f"Request a draft cherry-pick for train {train.id}",
        )
    print(
        json.dumps(
            {
                "status": "labels_synchronized",
                "train_id": train.id,
                "repositories": sorted(train.repositories),
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def _build_planner(args, loaded, github, planner_factory):
    """Construct a planner with the exact reviewed configuration binding."""

    options: dict[str, object] = {
        "config_revision": loaded.revision,
        "execution_context": (
            "local-materialize"
            if args.command == "local-materialize"
            else "local-gh" if args.auth == "gh" else "github-app"
        ),
    }
    if loaded.control_plane_snapshot is not None:
        options["control_plane_snapshot"] = loaded.control_plane_snapshot
    return planner_factory(loaded.catalog, github, **options)


def _run_action_reconciliation(
    args,
    train,
    planner,
    github,
    environ,
    writer_factory,
    repo_directories,
    stdout,
    stderr,
) -> int:
    """Replan and write each exact result from a reviewed read-phase artifact."""

    try:
        require_action_runtime(environ)
        artifact = json.loads(args.expected_results_file.read_text())
        if not isinstance(artifact, dict):
            raise ValueError("reconciliation artifact must be an object")
        expected_fields = {"status", "mode", "train_id", "results"}
        if set(artifact) != expected_fields:
            raise ValueError("reconciliation artifact fields are invalid")
        if (
            artifact.get("status") != "reconciled"
            or artifact.get("mode") != "plan"
            or artifact.get("train_id") != train.id
            or not isinstance(artifact.get("results"), list)
        ):
            raise ValueError("reconciliation artifact identity is invalid")
        expected_results = [Result.from_dict(item) for item in artifact["results"]]
        identities = [(item.source_pr, item.train_id) for item in expected_results]
        if len(set(identities)) != len(identities):
            raise ValueError("reconciliation artifact contains duplicates")
        if any(
            item.train_id != train.id
            or item.source_repository not in train.repositories
            for item in expected_results
        ):
            raise ValueError("reconciliation result is outside the train")
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ActionRuntimeError,
    ) as exc:
        print(f"error: invalid reconciliation artifact: {exc}", file=stderr)
        return 2

    action_results: list[dict[str, object]] = []
    for expected in expected_results:
        assert expected.source_pr is not None
        assert expected.source_repository is not None
        current = planner.plan(
            expected.source_pr,
            train.id,
            repo_directories,
            event_action="manual",
        )
        expected_is_writable = expected.status is Status.DRAFT_PLANNED or (
            expected.status is Status.AWAITING_DEPENDENCIES
            and expected.reason_code == "managed_dependency_frontier"
        )
        if expected_is_writable:
            try:
                current = _write_action_result(
                    environ=environ,
                    train=train,
                    expected=expected,
                    current=current,
                    github=github,
                    writer_factory=writer_factory,
                    repository_paths=repo_directories,
                    scratch_root=args.scratch_root,
                )
            except (ActionRuntimeError, ValueError) as exc:
                current = Result(
                    status=Status.BLOCKED_EVIDENCE,
                    reason_code="reconciliation_plan_drift",
                    message=(
                        "Write-time reconciliation changed; no draft was "
                        "created. A later read phase must plan it again."
                    ),
                    evidence={
                        **current.evidence,
                        "revalidation_error": str(exc),
                        "expected_plan_fingerprint": expected.evidence.get(
                            "plan_fingerprint"
                        ),
                    },
                    source_pr=current.source_pr,
                    source_repository=current.source_repository,
                    train_id=current.train_id,
                    destination_branch=current.destination_branch,
                )
        action_results.append(current.as_dict())
    print(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "action-create-draft",
                "train_id": train.id,
                "results": action_results,
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def _run_local_reconciliation(
    args,
    train,
    planner,
    github,
    writer_factory,
    write_authority,
    repo_directories,
    stdout,
) -> int:
    """Plan a full train locally and optionally create injected fake drafts."""

    results: list[dict[str, object]] = []
    writer = (
        writer_factory(github, capability=write_authority)
        if args.create_drafts
        else None
    )
    for repository in train.repositories:
        owner, repo = repository.split("/", 1)
        source_urls = github.search_merged_labeled_pull_requests(
            owner, repo, train.label
        )
        for source_url in source_urls:
            result = planner.plan(source_url, train.id, repo_directories)
            if writer is not None:
                result = writer.create(repo_directories[repository], train, result)
            if args.publish_status:
                source_owner, source_repo, number = parse_pull_request_url(source_url)
                github.upsert_comment(
                    source_owner,
                    source_repo,
                    number,
                    marker=status_marker(train.id),
                    body=render_status_comment(result),
                )
            results.append(result.as_dict())
    print(
        json.dumps(
            {
                "status": "reconciled",
                "mode": "create-draft" if args.create_drafts else "plan",
                "train_id": train.id,
                "results": results,
            },
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def _run_reconciliation(
    args,
    train,
    planner,
    github,
    environ,
    writer_factory,
    write_authority,
    stdout,
    stderr,
) -> int:
    """Validate checkout mappings and dispatch one reconciliation mode."""

    try:
        repo_directories = _reconciliation_repository_map(
            args.repo_dir, set(train.repositories)
        )
    except ValueError as exc:
        print(f"error: invalid --repo-dir: {exc}", file=stderr)
        return 2
    if args.command == "action-reconcile":
        return _run_action_reconciliation(
            args,
            train,
            planner,
            github,
            environ,
            writer_factory,
            repo_directories,
            stdout,
            stderr,
        )
    return _run_local_reconciliation(
        args,
        train,
        planner,
        github,
        writer_factory,
        write_authority,
        repo_directories,
        stdout,
    )


def _run_local_materialize(
    args,
    result: Result,
    repository_paths,
    local_materializer,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Materialize a clean plan into a local checkout without remote writes."""

    if result.status is not Status.DRAFT_PLANNED:
        print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
        return 1
    try:
        request = CoreRequest.from_dict(result.evidence.get("request_manifest"))
    except ManifestError as exc:
        print(f"error: planner produced an invalid core manifest: {exc}", file=stderr)
        return 2
    materialized = local_materializer(
        request=request,
        result=result,
        repositories=repository_paths,
        output_repo=args.output_repo,
        branch=args.branch,
        stderr=stderr,
    )
    if materialized is None:
        return 2
    print(json.dumps(materialized, sort_keys=True), file=stdout)
    return 0


def _revalidate_action_draft(
    args,
    result: Result,
    train,
    github,
    environ,
    writer_factory,
    repository_paths,
    stderr: TextIO,
) -> Result | None:
    """Revalidate an Actions plan and perform only its capability-bound writes."""

    try:
        expected = Result.from_dict(json.loads(args.expected_result_file.read_text()))
        return _write_action_result(
            environ=environ,
            train=train,
            expected=expected,
            current=result,
            github=github,
            writer_factory=writer_factory,
            repository_paths=repository_paths,
            scratch_root=args.scratch_root,
        )
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ActionRuntimeError,
    ) as exc:
        print(f"error: write-time revalidation failed: {exc}", file=stderr)
        return None


def _revalidate_local_draft(
    args,
    result: Result,
    train,
    github,
    environ,
    writer_factory,
    repository_paths,
    source_repository: str,
    stderr: TextIO,
) -> Result | None:
    """Revalidate a confirmed local plan and execute it with process-scoped auth."""

    try:
        expected = Result.from_dict(json.loads(args.expected_result_file.read_text()))
        authority = revalidate_local_write_authority(
            train_mode=train.mode,
            expected=expected,
            current=result,
            confirmation=args.confirm_remote_write,
        )
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        LocalRuntimeError,
    ) as exc:
        print(f"error: local write-time revalidation failed: {exc}", file=stderr)
        return None
    writer = writer_factory(
        github,
        capability=authority,
        scratch_root=args.scratch_root,
        git_environment=gh_git_environment(environ),
    )
    return writer.create(repository_paths[source_repository], train, result)


def _run_single_request(
    args,
    train,
    planner,
    github,
    environ,
    writer_factory,
    local_materializer,
    write_authority,
    stdout,
    stderr,
) -> int:
    """Plan and optionally materialize or write one source pull request."""

    try:
        source_owner, source_repo, _ = parse_pull_request_url(args.source_pr)
        source_repository = f"{source_owner}/{source_repo}"
        repository_paths = _request_repository_map(args.repo_dir, source_repository)
    except ValueError as exc:
        print(f"error: invalid --repo-dir: {exc}", file=stderr)
        return 2
    result = planner.plan(
        args.source_pr,
        args.train,
        repository_paths,
        event_action=args.event_action,
        scratch_root=args.scratch_root if args.command == "local-materialize" else None,
    )
    if args.command == "local-materialize":
        return _run_local_materialize(
            args,
            result,
            repository_paths,
            local_materializer,
            stdout,
            stderr,
        )
    if args.command == "action-create-draft":
        validated = _revalidate_action_draft(
            args,
            result,
            train,
            github,
            environ,
            writer_factory,
            repository_paths,
            stderr,
        )
        if validated is None:
            return 2
        result = validated
    if args.command == "local-create-draft":
        validated = _revalidate_local_draft(
            args,
            result,
            train,
            github,
            environ,
            writer_factory,
            repository_paths,
            source_repository,
            stderr,
        )
        if validated is None:
            return 2
        result = validated
    if args.command == "create-draft":
        writer = writer_factory(github, capability=write_authority)
        result = writer.create(repository_paths[source_repository], train, result)
    if args.publish_status:
        owner, repo, number = parse_pull_request_url(args.source_pr)
        github.upsert_comment(
            owner,
            repo,
            number,
            marker=status_marker(args.train),
            body=render_status_comment(result),
        )
    print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
    return 0


def _dispatch_authenticated(
    args,
    loaded,
    github,
    environ,
    planner_factory,
    writer_factory,
    local_materializer,
    write_authority,
    stdout,
    stderr,
) -> int:
    """Dispatch a validated, authenticated command without reordering effects."""

    if args.command in {"publish-result", "action-publish-result"}:
        return _run_publish_result(
            args, loaded.catalog, github, environ, stdout, stderr
        )
    if args.command == "action-publish-reconciliation":
        return _run_publish_reconciliation(
            args, loaded.catalog, github, environ, stdout, stderr
        )
    try:
        train = loaded.catalog.train(args.train)
    except ConfigError as exc:
        print(f"error: invalid train: {exc}", file=stderr)
        return 2
    if args.command in {"sync-labels", "action-sync-labels"}:
        return _run_sync_labels(args, train, github, environ, stdout, stderr)
    planner = _build_planner(args, loaded, github, planner_factory)
    if args.command in {"reconcile", "action-reconcile"}:
        return _run_reconciliation(
            args,
            train,
            planner,
            github,
            environ,
            writer_factory,
            write_authority,
            stdout,
            stderr,
        )
    return _run_single_request(
        args,
        train,
        planner,
        github,
        environ,
        writer_factory,
        local_materializer,
        write_authority,
        stdout,
        stderr,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    github_factory=None,
    local_github_factory=gh_github_client,
    planner_factory=Planner,
    writer_factory=DraftWriter,
    local_materializer=materialize_local_checkout,
    write_authority: object | None = None,
) -> int:
    """Run one cherry-pick automation command with injected side-effect services.

    Args:
        argv: Command-line arguments, excluding the executable name.
        environ: Environment used for authentication and runtime validation.
        stdout: Destination for machine-readable success output.
        stderr: Destination for sanitized validation errors.

    Returns:
        Zero on successful dispatch, one for a safe non-materializable plan, or
        two for invalid input, evidence, authentication, or configuration.
    """

    args = build_parser().parse_args(argv)
    try:
        loaded = _load_cli_configuration(args)
    except (ConfigError, ReleaseHubError, ValueError) as exc:
        print(f"error: invalid configuration: {exc}", file=stderr)
        return 2
    if args.command == "discover":
        return _run_discover(args, loaded.catalog, stdout, stderr)
    if _reject_invalid_runtime(args, loaded.revision, write_authority, stderr):
        return 2
    github = _build_github_client(
        args,
        environ,
        stderr,
        github_factory,
        local_github_factory,
    )
    if github is None:
        return 2
    return _dispatch_authenticated(
        args,
        loaded,
        github,
        environ,
        planner_factory,
        writer_factory,
        local_materializer,
        write_authority,
        stdout,
        stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
