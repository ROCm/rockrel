# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import os
import subprocess
from pathlib import Path

import pytest

from scripts.cherry_pick.refs import (
    RefHydrationError,
    hydrate_commit_ref,
    hydrate_pull_head_ref,
    hydrate_pull_refs,
)


def git(repo, *args, env=None):
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def local_remote(tmp_path):
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    cache = tmp_path / "cache.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", str(source))
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "test@example.com")
    (source / "base.txt").write_text("base\n")
    git(source, "add", "base.txt")
    git(source, "commit", "-m", "base")
    base = git(source, "rev-parse", "HEAD")
    git(source, "branch", "-M", "main")
    git(source, "branch", "release/test", base)
    (source / "feature.txt").write_text("feature\n")
    git(source, "add", "feature.txt")
    git(source, "commit", "-m", "feature")
    head = git(source, "rev-parse", "HEAD")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "origin", "main", "release/test")
    git(source, "push", "origin", f"{head}:refs/pull/1/head")
    git(tmp_path, "init", "--bare", str(cache))
    return remote, cache, base, head


def test_hydrates_exact_pull_head_merge_commits_and_destination(local_remote):
    remote, cache, base, head = local_remote
    result = hydrate_pull_refs(
        cache,
        remote=str(remote),
        pull_number=1,
        source_branch="main",
        merge_sha=head,
        head_sha=head,
        ordered_commits=(head,),
        destination_branch="release/test",
        destination_sha=base,
    )
    assert result.head_sha == head
    assert result.merge_sha == head
    assert result.destination_sha == base
    assert result.ordered_commits == (head,)
    assert git(cache, "rev-parse", "refs/cherry-pick/pull/1/head") == head
    assert git(cache, "rev-parse", "refs/cherry-pick/destination") == base


def test_hydrates_exact_standalone_commit_and_destination(local_remote):
    remote, cache, base, head = local_remote

    result = hydrate_commit_ref(
        cache,
        remote=str(remote),
        commit_sha=head,
        destination_branch="release/test",
        destination_sha=base,
    )

    assert result.commit_sha == head
    assert result.destination_sha == base
    assert git(cache, "cat-file", "-t", head) == "commit"
    assert git(cache, "rev-parse", "refs/cherry-pick/destination") == base


def test_hydrates_exact_open_pull_head_for_coverage(local_remote):
    remote, cache, _base, head = local_remote

    result = hydrate_pull_head_ref(
        cache,
        remote=str(remote),
        pull_number=1,
        head_sha=head,
    )

    assert result.head_sha == head
    assert git(cache, "rev-parse", "refs/cherry-pick/coverage/1/head") == head


def test_fails_closed_when_claimed_object_does_not_match_hydrated_ref(local_remote):
    remote, cache, base, head = local_remote
    with pytest.raises(RefHydrationError) as error:
        hydrate_pull_refs(
            cache,
            remote=str(remote),
            pull_number=1,
            source_branch="main",
            merge_sha=head,
            head_sha="f" * 40,
            ordered_commits=(head,),
            destination_branch="release/test",
            destination_sha=base,
        )
    assert error.value.reason_code == "pull_head_mismatch"


def test_fails_closed_for_missing_original_commit_or_destination(local_remote):
    remote, cache, base, head = local_remote
    with pytest.raises(RefHydrationError) as commit_error:
        hydrate_pull_refs(
            cache,
            remote=str(remote),
            pull_number=1,
            source_branch="main",
            merge_sha=head,
            head_sha=head,
            ordered_commits=("e" * 40,),
            destination_branch="release/test",
            destination_sha=base,
        )
    assert commit_error.value.reason_code == "source_commit_missing"

    with pytest.raises(RefHydrationError) as destination_error:
        hydrate_pull_refs(
            cache,
            remote=str(remote),
            pull_number=1,
            source_branch="main",
            merge_sha=head,
            head_sha=head,
            ordered_commits=(head,),
            destination_branch="release/test",
            destination_sha="d" * 40,
        )
    assert destination_error.value.reason_code == "destination_head_mismatch"


def test_hydration_disables_prompts_lazy_fetch_and_hooks(monkeypatch, local_remote):
    remote, cache, base, head = local_remote
    calls = []
    real_run = subprocess.run

    def record(command, **kwargs):
        calls.append((command, kwargs))
        return real_run(command, **kwargs)

    monkeypatch.setattr("scripts.cherry_pick.refs.subprocess.run", record)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "short-lived-read-token")
    hydrate_pull_refs(
        cache,
        remote=str(remote),
        pull_number=1,
        source_branch="main",
        merge_sha=head,
        head_sha=head,
        ordered_commits=(head,),
        destination_branch="release/test",
        destination_sha=base,
    )
    assert calls
    for command, kwargs in calls:
        assert "core.hooksPath=/dev/null" in command
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["env"]["GIT_CONFIG_KEY_0"] == (
            "http.https://github.com/.extraheader"
        )
        assert "short-lived-read-token" not in kwargs["env"]["GIT_CONFIG_VALUE_0"]


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"pull_number": 0}, "pull_number_invalid"),
        ({"source_branch": "-invalid"}, "branch_invalid"),
        ({"destination_branch": "bad branch"}, "branch_invalid"),
        ({"head_sha": "short"}, "head_sha_invalid"),
        ({"ordered_commits": ()}, "source_commit_missing"),
    ],
)
def test_hydration_rejects_invalid_inputs_before_fetch(local_remote, overrides, reason):
    remote, cache, base, head = local_remote
    arguments = {
        "remote": str(remote),
        "pull_number": 1,
        "source_branch": "main",
        "merge_sha": head,
        "head_sha": head,
        "ordered_commits": (head,),
        "destination_branch": "release/test",
        "destination_sha": base,
    }
    arguments.update(overrides)
    with pytest.raises(RefHydrationError) as error:
        hydrate_pull_refs(cache, **arguments)
    assert error.value.reason_code == reason


def test_hydration_structures_fetch_and_missing_merge_failures(local_remote, tmp_path):
    remote, cache, base, head = local_remote
    common = {
        "pull_number": 1,
        "source_branch": "main",
        "head_sha": head,
        "ordered_commits": (head,),
        "destination_branch": "release/test",
        "destination_sha": base,
    }
    with pytest.raises(RefHydrationError) as fetch_error:
        hydrate_pull_refs(
            cache,
            remote=str(tmp_path / "missing.git"),
            merge_sha=head,
            **common,
        )
    assert fetch_error.value.reason_code == "ref_fetch_failed"

    with pytest.raises(RefHydrationError) as merge_error:
        hydrate_pull_refs(
            cache,
            remote=str(remote),
            merge_sha="d" * 40,
            **common,
        )
    assert merge_error.value.reason_code == "merge_commit_missing"


def test_commit_and_coverage_hydration_fail_closed_on_identity_mismatch(local_remote):
    remote, cache, base, _head = local_remote
    with pytest.raises(RefHydrationError) as commit_error:
        hydrate_commit_ref(
            cache,
            remote=str(remote),
            commit_sha="d" * 40,
            destination_branch="release/test",
            destination_sha=base,
        )
    assert commit_error.value.reason_code in {
        "commit_fetch_failed",
        "commit_object_missing",
    }

    with pytest.raises(RefHydrationError) as pull_error:
        hydrate_pull_head_ref(
            cache,
            remote=str(remote),
            pull_number=1,
            head_sha="e" * 40,
        )
    assert pull_error.value.reason_code == "coverage_pull_head_mismatch"


def test_commit_hydration_rejects_invalid_branch_and_destination(local_remote):
    remote, cache, _base, head = local_remote
    with pytest.raises(RefHydrationError) as branch_error:
        hydrate_commit_ref(
            cache,
            remote=str(remote),
            commit_sha=head,
            destination_branch="bad branch",
            destination_sha="d" * 40,
        )
    assert branch_error.value.reason_code == "branch_invalid"

    with pytest.raises(RefHydrationError) as destination_error:
        hydrate_commit_ref(
            cache,
            remote=str(remote),
            commit_sha=head,
            destination_branch="release/test",
            destination_sha="d" * 40,
        )
    assert destination_error.value.reason_code == "destination_head_mismatch"


def test_coverage_hydration_rejects_invalid_number_and_fetch_failure(
    local_remote, tmp_path
):
    _remote, cache, _base, head = local_remote
    with pytest.raises(RefHydrationError) as number_error:
        hydrate_pull_head_ref(
            cache,
            remote=str(tmp_path / "missing.git"),
            pull_number=0,
            head_sha=head,
        )
    assert number_error.value.reason_code == "pull_number_invalid"

    with pytest.raises(RefHydrationError) as fetch_error:
        hydrate_pull_head_ref(
            cache,
            remote=str(tmp_path / "missing.git"),
            pull_number=1,
            head_sha=head,
        )
    assert fetch_error.value.reason_code == "coverage_pull_fetch_failed"
