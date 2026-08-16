# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Filesystem-only production-pipeline simulator for deterministic tests."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .clients import BranchInfo, DestinationPolicy, JiraIssueEvidence
from .config import TrainCatalog
from .git import evaluate_changeset
from .models import Result, Status
from .orchestrator import Planner
from .writer import DraftWriter, test_write_capability


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@dataclass(frozen=True)
class FrozenPullRequest:
    repository: str
    number: int
    title: str
    body: str
    base_branch: str
    head_sha: str
    merge_commit_sha: str
    commits: tuple[str, ...]
    labels: tuple[str, ...]
    label_actor: str
    label_actor_permission: str
    jira_fix_versions: tuple[str, ...]
    jira_dependencies: tuple[str, ...] = ()
    jira_ordering_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is None:
            raise ValueError("frozen pull repository must be OWNER/REPO")
        if self.number < 1 or not self.commits:
            raise ValueError("frozen pull requires a positive number and commits")
        for value in (self.head_sha, self.merge_commit_sha, *self.commits):
            if re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise ValueError("frozen pull commit evidence must use full SHAs")

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}/pull/{self.number}"


@dataclass(frozen=True)
class SimulationResult:
    result: Result
    drafts: tuple[dict[str, object], ...]


class _FrozenJira:
    def __init__(self, pull: FrozenPullRequest) -> None:
        self.pull = pull

    def issue_evidence(self, _key: str) -> JiraIssueEvidence:
        return JiraIssueEvidence(
            fix_versions=frozenset(self.pull.jira_fix_versions),
            dependencies=self.pull.jira_dependencies,
            ordering_notes=self.pull.jira_ordering_notes,
        )


class _FilesystemGitHub:
    def __init__(self, repo: Path, pull: FrozenPullRequest) -> None:
        self.repo = repo
        self.source = pull
        self.drafts: list[dict[str, object]] = []
        self.created_payloads: list[dict[str, object]] = []

    def _remote_head(self, branch: str) -> str | None:
        result = _git(
            self.repo,
            "ls-remote",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        )
        line = result.stdout.strip()
        return line.split()[0] if result.returncode == 0 and line else None

    def pull(self, owner: str, repo: str, number: int) -> dict[str, object]:
        if f"{owner}/{repo}" != self.source.repository or number != self.source.number:
            raise ValueError("frozen pull request identity mismatch")
        return {
            "number": number,
            "html_url": self.source.url,
            "title": self.source.title,
            "body": self.source.body,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": self.source.merge_commit_sha,
            "head": {"sha": self.source.head_sha},
            "base": {"ref": self.source.base_branch},
            "labels": [{"name": label} for label in self.source.labels],
        }

    def pull_commits(self, _owner: str, _repo: str, _number: int) -> tuple[str, ...]:
        return self.source.commits

    def label_actor(self, _owner: str, _repo: str, _number: int, _label: str) -> str:
        return self.source.label_actor

    def permission(self, _owner: str, _repo: str, _login: str) -> str:
        return self.source.label_actor_permission

    def branch(self, _owner: str, _repo: str, branch: str) -> BranchInfo:
        sha = self._remote_head(branch)
        return BranchInfo(exists=sha is not None, sha=sha)

    def destination_policy(
        self, _owner: str, _repo: str, _branch: str
    ) -> DestinationPolicy:
        return DestinationPolicy(
            pull_request_required=True,
            rule_ids=(1,),
            required_approvals=1,
            require_last_push_approval=True,
            allowed_merge_methods=("squash",),
        )

    def pulls(
        self, _owner: str, _repo: str, *, base: str, state: str
    ) -> list[dict[str, object]]:
        del state
        return [item for item in self.drafts if item.get("base", {}).get("ref") == base]

    def pull_for_head(
        self, owner: str, repo: str, *, head: str, base: str
    ) -> dict[str, object] | None:
        return next(
            (
                item
                for item in self.pulls(owner, repo, base=base, state="all")
                if item.get("head", {}).get("ref") == head
            ),
            None,
        )

    def create_pull(
        self,
        _owner: str,
        _repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool,
    ) -> str:
        if not draft:
            raise ValueError("local simulation creates drafts only")
        sha = self._remote_head(head)
        if sha is None:
            raise ValueError("local simulation draft head is unavailable")
        url = f"local://draft/{len(self.drafts) + 1}"
        self.created_payloads.append(
            {
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": True,
            }
        )
        self.drafts.append(
            {
                "number": len(self.drafts) + 1,
                "html_url": url,
                "title": title,
                "body": body,
                "state": "open",
                "draft": True,
                "merged_at": None,
                "head": {"ref": head, "sha": sha},
                "base": {"ref": base},
            }
        )
        return url


class LocalPipelineSimulator:
    """Run real planner/writer logic against a local bare Git remote only."""

    def __init__(
        self,
        *,
        repo: str | Path,
        catalog: TrainCatalog,
        pull: FrozenPullRequest,
        scratch_root: str | Path,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.catalog = catalog
        self.pull = pull
        self.scratch_root = Path(scratch_root).resolve()
        remote_result = _git(self.repo, "remote", "get-url", "origin")
        remote = remote_result.stdout.strip()
        if (
            remote_result.returncode != 0
            or not remote
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", remote)
            or re.match(r"^[^/]+@[^:]+:", remote)
        ):
            raise PermissionError(
                "local pipeline simulation requires a filesystem remote"
            )
        remote_path = Path(remote)
        if not remote_path.is_absolute():
            remote_path = (self.repo / remote_path).resolve()
        if not remote_path.is_dir():
            raise PermissionError(
                "local pipeline simulation filesystem remote is missing"
            )
        self.github = _FilesystemGitHub(self.repo, pull)
        self.jira = _FrozenJira(pull)

    def run(self, train_id: str) -> SimulationResult:
        planner_worktree = self.scratch_root / "planner-worktree"

        def evaluator(repo, changeset, target, *, source_identity=None):
            return evaluate_changeset(
                repo,
                changeset,
                target,
                worktree_path=planner_worktree,
                source_identity=source_identity,
            )

        planner = Planner(
            self.catalog,
            self.github,
            self.jira,
            evaluator=evaluator,
        )
        planned = planner.plan(
            self.pull.url,
            train_id,
            self.repo,
            event_action="labeled",
        )
        if planned.status is not Status.DRAFT_PLANNED:
            return SimulationResult(planned, tuple(self.github.created_payloads))
        writer = DraftWriter(
            self.github,
            capability=test_write_capability(),
            scratch_root=self.scratch_root / "writer",
        )
        result = writer.create(self.repo, self.catalog.train(train_id), planned)
        return SimulationResult(result, tuple(self.github.created_payloads))
