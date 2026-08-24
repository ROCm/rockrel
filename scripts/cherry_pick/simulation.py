# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Filesystem-only production-pipeline simulator for deterministic tests."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .authorization import LabelTransition
from .clients import BranchInfo, DestinationPolicy
from .config import TrainCatalog
from .core import CorePlanner
from .git import evaluate_changeset
from .models import Result, Status
from .orchestrator import Planner
from .writer import DraftWriter, test_draft_write_authority


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run local Git with hooks and stdin disabled while capturing all output."""

    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=False,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@dataclass(frozen=True)
class FrozenPullRequest:
    """Represent frozen pull request in the simulation contract."""

    repository: str
    number: int
    title: str
    body: str
    base_branch: str
    head_sha: str
    merge_commit_sha: str
    commits: tuple[str, ...]
    labels: tuple[str, ...]
    label_event_id: int
    label_actor_id: int
    label_actor: str
    label_actor_permission: str
    label_app_id: int | None = None

    def __post_init__(self) -> None:
        """Validate frozen pull request invariants after dataclass initialization."""

        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is None:
            raise ValueError("frozen pull repository must be OWNER/REPO")
        if self.number < 1 or not self.commits:
            raise ValueError("frozen pull requires a positive number and commits")
        if self.label_event_id < 1 or self.label_actor_id < 1:
            raise ValueError("frozen pull requires positive event and actor identities")
        if not self.label_actor:
            raise ValueError("frozen pull requires a label actor")
        for value in (self.head_sha, self.merge_commit_sha, *self.commits):
            if re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise ValueError("frozen pull commit evidence must use full SHAs")

    @property
    def url(self) -> str:
        """Return the canonical URL represented by this value."""

        return f"https://github.com/{self.repository}/pull/{self.number}"


@dataclass(frozen=True)
class SimulationResult:
    """Represent simulation result in the simulation contract."""

    result: Result
    drafts: tuple[dict[str, object], ...]


class _FilesystemGitHub:
    """Provide a filesystem-backed GitHub test double for local simulation."""

    def __init__(self, repo: Path, pull: FrozenPullRequest) -> None:
        """Initialize the filesystem-backed GitHub simulator over frozen pull requests."""

        self.repo = repo
        self.source = pull
        self.drafts: list[dict[str, object]] = []
        self.created_payloads: list[dict[str, object]] = []

    def _remote_head(self, branch: str) -> str | None:
        """Resolve the exact remote head for an automation branch."""

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
        """Fetch and validate one GitHub pull request response."""

        if f"{owner}/{repo}" != self.source.repository or number != self.source.number:
            raise ValueError("frozen pull request identity mismatch")
        return {
            "number": number,
            "html_url": self.source.url,
            "title": self.source.title,
            "body": self.source.body,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-16T10:00:00Z",
            "merge_commit_sha": self.source.merge_commit_sha,
            "commits": len(self.source.commits),
            "head": {
                "sha": self.source.head_sha,
                "ref": f"source/{self.source.number}",
                "repo": {"full_name": self.source.repository},
            },
            "base": {
                "ref": self.source.base_branch,
                "sha": self._remote_head(self.source.base_branch),
            },
            "labels": [{"name": label} for label in self.source.labels],
        }

    def pull_commits(self, owner: str, repo: str, number: int) -> tuple[str, ...]:
        """Fetch the complete ordered commit list for one pull request."""

        if f"{owner}/{repo}" != self.source.repository or number != self.source.number:
            raise ValueError("frozen pull request identity mismatch")
        return self.source.commits

    def label_transitions(
        self, owner: str, repo: str, number: int, label: str
    ) -> tuple[LabelTransition, ...]:
        """Return the frozen label-transition evidence for a simulated pull request."""

        if f"{owner}/{repo}" != self.source.repository or number != self.source.number:
            raise ValueError("frozen pull request identity mismatch")
        return (
            LabelTransition(
                event_id=self.source.label_event_id,
                node_id=f"LOCAL_LABEL_EVENT_{self.source.label_event_id}",
                label=label,
                action="labeled",
                created_at="2026-08-16T09:00:00Z",
                actor_id=self.source.label_actor_id,
                actor_login=self.source.label_actor,
                performed_via_app_id=self.source.label_app_id,
            ),
        )

    def permission(self, owner: str, repo: str, login: str) -> str:
        """Fetch and validate the actor permission for one repository."""

        if (
            f"{owner}/{repo}" != self.source.repository
            or login != self.source.label_actor
        ):
            return "none"
        return self.source.label_actor_permission

    def branch(self, _owner: str, _repo: str, branch: str) -> BranchInfo:
        """Fetch and validate one repository branch response."""

        sha = self._remote_head(branch)
        return BranchInfo(exists=sha is not None, sha=sha)

    def destination_policy(
        self, _owner: str, _repo: str, _branch: str
    ) -> DestinationPolicy:
        """Fetch the destination branch protection and write policy."""

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
        """List destination pull requests relevant to idempotency checks."""

        return [
            item
            for item in self.drafts
            if item.get("base", {}).get("ref") == base
            and (state == "all" or item.get("state") == state)
        ]

    def add_open_pull(
        self,
        *,
        number: int,
        head_sha: str,
        head_branch: str,
        base_branch: str,
        draft: bool,
    ) -> str:
        """Install one canonical local open-PR record for coverage tests."""

        if self._remote_head(base_branch) is None:
            raise ValueError("local simulation pull base is unavailable")
        if _git(self.repo, "cat-file", "-e", f"{head_sha}^{{commit}}").returncode != 0:
            raise ValueError("local simulation pull head is unavailable")
        url = f"https://github.com/{self.source.repository}/pull/{number}"
        self.drafts.append(
            {
                "number": number,
                "html_url": url,
                "title": "Local manual cherry-pick",
                "body": "Local manual coverage fixture",
                "state": "open",
                "merged_at": None,
                "draft": draft,
                "head": {
                    "ref": head_branch,
                    "sha": head_sha,
                    "repo": {"full_name": self.source.repository},
                },
                "base": {
                    "ref": base_branch,
                    "sha": self._remote_head(base_branch),
                },
            }
        )
        return url

    def pull_for_head(
        self, owner: str, repo: str, *, head: str, base: str
    ) -> dict[str, object] | None:
        """Find an existing pull request for the exact automation head branch."""

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
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool,
    ) -> str:
        """Create one pull request with the caller-provided draft state."""

        if not draft:
            raise ValueError("local simulation creates drafts only")
        sha = self._remote_head(head)
        if sha is None:
            raise ValueError("local simulation draft head is unavailable")
        number = (
            max((int(item.get("number", 0)) for item in self.drafts), default=0) + 1
        )
        url = f"https://github.com/{owner}/{repo}/pull/{number}"
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": True,
        }
        self.created_payloads.append(payload)
        self.drafts.append(
            {
                "number": number,
                "html_url": url,
                **payload,
                "state": "open",
                "merged_at": None,
                "head": {
                    "ref": head,
                    "sha": sha,
                    "repo": {"full_name": f"{owner}/{repo}"},
                },
                "base": {"ref": base, "sha": self._remote_head(base)},
            }
        )
        pull_ref = _git(
            self.repo,
            "push",
            "origin",
            f"{sha}:refs/pull/{number}/head",
        )
        if pull_ref.returncode != 0:
            raise ValueError("local simulation pull ref could not be installed")
        return url


class LocalPipelineSimulator:
    """Run the real planner/core/writer against one local bare Git remote."""

    def __init__(
        self,
        *,
        repo: str | Path,
        catalog: TrainCatalog,
        pull: FrozenPullRequest,
        scratch_root: str | Path,
    ) -> None:
        """Initialize local pipeline simulation with its catalog and frozen pull requests."""

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
        pull_ref = f"refs/pull/{pull.number}/head"
        push = _git(self.repo, "push", "origin", f"{pull.head_sha}:{pull_ref}")
        if push.returncode != 0:
            raise ValueError("frozen pull head could not be installed in local remote")
        self.github = _FilesystemGitHub(self.repo, pull)

    def run(self, train_id: str) -> SimulationResult:
        """Run the local simulation and return its deterministic evidence."""

        planner_worktree = self.scratch_root / "planner-worktree"

        def evaluator(repo, changeset, target, source_identity, _scratch_root):
            """Evaluate the proven changeset inside the simulator's isolated worktree."""

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
            core_planner=CorePlanner(evaluator=evaluator),
        )
        planned = planner.plan(
            self.pull.url,
            train_id,
            {self.pull.repository: self.repo},
            event_action="labeled",
        )
        if planned.status is not Status.DRAFT_PLANNED:
            return SimulationResult(planned, tuple(self.github.created_payloads))
        fingerprint = planned.evidence.get("plan_fingerprint")
        if not isinstance(fingerprint, str):
            raise ValueError("planner omitted the plan fingerprint")
        writer = DraftWriter(
            self.github,
            capability=test_draft_write_authority(fingerprint),
            scratch_root=self.scratch_root / "writer",
        )
        result = writer.create(self.repo, self.catalog.train(train_id), planned)
        return SimulationResult(result, tuple(self.github.created_payloads))
