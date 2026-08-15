# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Command-line entry point for label-driven cherry-pick automation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .clients import GitHubClient, JiraClient, parse_pull_request_url
from .config import ConfigError, load_config
from .models import Result
from .orchestrator import (
    Planner,
    discover_train_ids,
    render_status_comment,
    status_marker,
)
from .writer import DraftWriter


DEFAULT_CONFIG = Path(__file__).parents[2] / "config" / "cherry-pick-trains.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan draft ROCm destination-branch cherry-picks."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--labels-json", required=True)
    discover.add_argument(
        "--event-action",
        choices=("labeled", "unlabeled", "closed"),
        required=True,
    )
    discover.add_argument("--event-label", default="")

    for command in ("plan", "create-draft"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--source-pr", required=True)
        subparser.add_argument("--train", required=True)
        subparser.add_argument("--repo-dir", type=Path, required=True)
        subparser.add_argument("--publish-status", action="store_true")
        subparser.add_argument(
            "--event-action",
            choices=("labeled", "unlabeled", "closed", "manual"),
            default="manual",
        )

    sync = subparsers.add_parser("sync-labels")
    sync.add_argument("--train", required=True)

    publish = subparsers.add_parser("publish-result")
    publish.add_argument("--result-file", type=Path, required=True)

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
    return parser


def _credential(environ: Mapping[str, str], name: str, stderr: TextIO) -> str | None:
    value = environ.get(name)
    if value:
        return value
    print(f"error: required environment variable {name} is not set", file=stderr)
    return None


def _needs_write_capability(args: argparse.Namespace) -> bool:
    return bool(
        args.command in {"create-draft", "sync-labels", "publish-result"}
        or (args.command == "reconcile" and args.create_drafts)
        or getattr(args, "publish_status", False)
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    github_factory=GitHubClient,
    jira_factory=JiraClient,
    planner_factory=Planner,
    writer_factory=DraftWriter,
    write_capability: object | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: invalid configuration: {exc}", file=stderr)
        return 2

    if args.command == "discover":
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
            config,
            current_labels=tuple(labels),
            event_action=args.event_action,
            event_label=args.event_label,
        )
        print(json.dumps({"trains": list(trains)}, sort_keys=True), file=stdout)
        return 0

    if _needs_write_capability(args) and write_capability is None:
        print(
            "error: remote write capability is unavailable in local-review mode",
            file=stderr,
        )
        return 2

    github_token = _credential(environ, "GITHUB_TOKEN", stderr)
    if github_token is None:
        return 2
    github = github_factory(github_token)

    if args.command == "publish-result":
        try:
            payload = json.loads(args.result_file.read_text())
            result = Result.from_dict(payload)
            train = config.train(result.train_id or "")
            owner, repo, number = parse_pull_request_url(result.source_pr or "")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            print(f"error: invalid result file: {exc}", file=stderr)
            return 2
        github.upsert_comment(
            owner,
            repo,
            number,
            marker=status_marker(train.id),
            body=render_status_comment(result),
        )
        print(json.dumps(result.as_dict(), sort_keys=True), file=stdout)
        return 0

    train = config.train(args.train)
    if args.command == "sync-labels":
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

    jira = None
    if train.requirements.jira_fix_version is not None:
        jira_url = _credential(environ, "JIRA_URL", stderr)
        jira_token = _credential(environ, "JIRA_TOKEN", stderr)
        if jira_url is None or jira_token is None:
            return 2
        jira = jira_factory(jira_url, jira_token)
    planner = planner_factory(config, github, jira)

    if args.command == "reconcile":
        repo_directories: dict[str, Path] = {}
        for assignment in args.repo_dir:
            if "=" not in assignment:
                print(
                    f"error: --repo-dir must be OWNER/REPO=PATH, got {assignment!r}",
                    file=stderr,
                )
                return 2
            repository, directory = assignment.split("=", 1)
            repo_directories[repository] = Path(directory)
        missing = sorted(set(train.repositories) - set(repo_directories))
        if missing:
            print(
                "error: missing --repo-dir mappings for " + ", ".join(missing),
                file=stderr,
            )
            return 2
        results: list[dict[str, object]] = []
        writer = (
            writer_factory(github, capability=write_capability)
            if args.create_drafts
            else None
        )
        for repository in train.repositories:
            owner, repo = repository.split("/", 1)
            source_urls = github.search_merged_labeled_pull_requests(
                owner, repo, train.label
            )
            for source_url in source_urls:
                result = planner.plan(
                    source_url, train.id, repo_directories[repository]
                )
                if writer is not None:
                    result = writer.create(repo_directories[repository], train, result)
                if args.publish_status:
                    source_owner, source_repo, number = parse_pull_request_url(
                        source_url
                    )
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

    result = planner.plan(
        args.source_pr,
        args.train,
        args.repo_dir,
        event_action=args.event_action,
    )
    if args.command == "create-draft":
        writer = writer_factory(github, capability=write_capability)
        result = writer.create(args.repo_dir, train, result)
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


if __name__ == "__main__":
    raise SystemExit(main())
