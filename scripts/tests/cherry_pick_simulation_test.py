# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import importlib
import subprocess

import pytest

from scripts.cherry_pick.config import (
    RepositoryConfig,
    TrainCatalog,
    TrainConfig,
    TrainRequirements,
)
from scripts.cherry_pick.models import Status


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def commit_file(repo, path, content, message):
    (repo / path).write_text(content)
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def simulation_module():
    return importlib.import_module("scripts.cherry_pick.simulation")


def train():
    return TrainCatalog(
        trains={
            "candidate-1": TrainConfig(
                id="candidate-1",
                label="cherry-pick:candidate-1",
                state="active",
                mode="create-draft",
                requirements=TrainRequirements(
                    jira_fix_version="10.1.0a20260811",
                    block_on_dependencies=True,
                ),
                repositories={
                    "ROCm/TheRock": RepositoryConfig(
                        source_branches=("main",),
                        destination_branch="staging/candidate-1",
                    )
                },
            )
        }
    )


def repository_fixture(tmp_path, *, conflict=False):
    remote = tmp_path / "local-remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    repo = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(repo))
    git(repo, "config", "user.name", "Simulation Fixture")
    git(repo, "config", "user.email", "simulation@example.com")
    git(repo, "checkout", "-b", "main")
    base = commit_file(repo, "value.txt", "base\n", "base")
    git(repo, "push", "origin", "main")
    git(repo, "branch", "staging/candidate-1", base)
    git(repo, "push", "origin", "staging/candidate-1")

    git(repo, "checkout", "-b", "topic", base)
    source_head = commit_file(repo, "value.txt", "source\n", "ROCM-100 source")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", source_head)
    git(repo, "commit", "--amend", "-m", "ROCM-100 source (#100)")
    source_merge = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "main")

    destination = base
    if conflict:
        git(repo, "checkout", "staging/candidate-1")
        destination = commit_file(repo, "value.txt", "target\n", "target conflict")
        git(repo, "push", "origin", "staging/candidate-1")
    return repo, remote, source_head, source_merge, destination


def frozen_pull(module, source_head, source_merge):
    return module.FrozenPullRequest(
        repository="ROCm/TheRock",
        number=100,
        title="ROCM-100 source",
        body="Take ROCM-100 for the candidate train.",
        base_branch="main",
        head_sha=source_head,
        merge_commit_sha=source_merge,
        commits=(source_head,),
        labels=("cherry-pick:candidate-1",),
        label_actor="operator",
        label_actor_permission="write",
        jira_fix_versions=("10.1.0a20260811",),
    )


def test_local_pipeline_creates_only_a_local_draft_and_is_idempotent(tmp_path):
    module = simulation_module()
    repo, remote, source_head, source_merge, destination = repository_fixture(tmp_path)
    simulator = module.LocalPipelineSimulator(
        repo=repo,
        catalog=train(),
        pull=frozen_pull(module, source_head, source_merge),
        scratch_root=tmp_path / "disk-scratch",
    )

    first = simulator.run("candidate-1")

    assert first.result.status is Status.DRAFT_CREATED
    assert len(first.drafts) == 1
    draft = first.drafts[0]
    assert draft["draft"] is True
    assert draft["base"] == "staging/candidate-1"
    assert "Operator review required" in draft["body"]
    branch = "shared/cherry-pick/candidate-1/100"
    branch_head = git(remote, "rev-parse", branch)
    assert git(remote, "rev-parse", f"{branch_head}^") == destination
    assert git(remote, "rev-parse", f"{branch_head}^{{tree}}") == git(
        repo, "rev-parse", f"{source_merge}^{{tree}}"
    )
    assert source_merge in git(remote, "show", "-s", "--format=%B", branch)

    second = simulator.run("candidate-1")

    assert second.result.status in {Status.COVERED_BY_EXISTING_PR, Status.DRAFT_EXISTS}
    assert len(second.drafts) == 1
    assert git(remote, "rev-parse", branch) == branch_head


def test_local_pipeline_conflict_creates_no_branch_or_draft(tmp_path):
    module = simulation_module()
    repo, remote, source_head, source_merge, _destination = repository_fixture(
        tmp_path, conflict=True
    )
    simulator = module.LocalPipelineSimulator(
        repo=repo,
        catalog=train(),
        pull=frozen_pull(module, source_head, source_merge),
        scratch_root=tmp_path / "disk-scratch",
    )

    result = simulator.run("candidate-1")

    assert result.result.status is Status.BLOCKED_CONFLICT
    assert result.drafts == ()
    assert (
        git(
            remote,
            "show-ref",
            "--verify",
            "refs/heads/shared/cherry-pick/candidate-1/100",
            check=False,
        )
        == ""
    )


def test_local_pipeline_refuses_a_network_origin_before_planning(tmp_path):
    module = simulation_module()
    repo, _remote, source_head, source_merge, _destination = repository_fixture(
        tmp_path
    )
    git(repo, "remote", "set-url", "origin", "https://github.com/ROCm/TheRock.git")

    with pytest.raises(PermissionError, match="filesystem remote"):
        module.LocalPipelineSimulator(
            repo=repo,
            catalog=train(),
            pull=frozen_pull(module, source_head, source_merge),
            scratch_root=tmp_path / "disk-scratch",
        )
