# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import subprocess

from scripts.cherry_pick.coverage import (
    extract_cherry_pick_origin,
    find_covering_pull,
)


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def commit(repo, message):
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class FakeGitHub:
    def commit(self, owner, repo, sha):
        assert (owner, repo) == ("ROCm", "llvm-project")
        return {
            "sha": sha,
            "commit": {
                "message": (
                    "[AMDGPU] fix\n\n"
                    "(cherry picked from commit "
                    "1109d68feb1b746c675d8f88fb89085334f8f514)"
                )
            },
        }

    def compare(self, owner, repo, base, head):
        assert (owner, repo) == ("ROCm", "llvm-project")
        return {
            "status": "diverged",
            "commits": [self.commit(owner, repo, head)],
        }


def make_gitlink_repo(tmp_path):
    repo = tmp_path / "therock"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Cherry-pick Test")
    git(repo, "config", "user.email", "cherry-pick@example.com")
    (repo / ".gitmodules").write_text(
        '[submodule "llvm-project"]\n'
        "\tpath = compiler/amd-llvm\n"
        "\turl = https://github.com/ROCm/llvm-project.git\n"
    )
    git(repo, "add", ".gitmodules")
    old = "1" * 40
    desired = "a01cdbd92d1f931738a4897502747a68ae36eccf"
    candidate_pin = "d177931e65e6057dca59f1c5b95e643d29876db3"
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{old},compiler/amd-llvm")
    base = commit(repo, "base")
    git(repo, "update-index", "--cacheinfo", f"160000,{desired},compiler/amd-llvm")
    source = commit(repo, "source gitlink")
    git(repo, "checkout", "--detach", base)
    git(
        repo,
        "update-index",
        "--cacheinfo",
        f"160000,{candidate_pin},compiler/amd-llvm",
    )
    candidate = commit(repo, "candidate covering gitlink")
    return repo, source, candidate


def test_extracts_only_full_cherry_pick_origin_trailer():
    assert extract_cherry_pick_origin(
        "fix\n\n(cherry picked from commit "
        "1109d68feb1b746c675d8f88fb89085334f8f514)"
    ) == "1109d68feb1b746c675d8f88fb89085334f8f514"
    assert extract_cherry_pick_origin("mentions 1109d68 but no trailer") is None


def test_gitlink_divergence_is_covered_by_matching_original_commit(tmp_path):
    repo, source, candidate = make_gitlink_repo(tmp_path)
    pull = {
        "number": 7357,
        "state": "open",
        "html_url": "https://github.com/ROCm/TheRock/pull/7357",
        "head": {"sha": candidate, "ref": "users/example/covering"},
    }

    coverage = find_covering_pull(repo, FakeGitHub(), source, pull)

    assert coverage is not None
    assert coverage["reason"] == "gitlink_cherry_pick_provenance"
    assert coverage["paths"] == ["compiler/amd-llvm"]
    assert coverage["pull_request_url"].endswith("/7357")


def test_0811_compiler_fixture_records_exact_non_ancestry_evidence():
    fixture = {
        "source_pr": 7282,
        "covering_pr": 7357,
        "source_desired_pin": "a01cdbd92d1f931738a4897502747a68ae36eccf",
        "covering_pin": "d177931e65e6057dca59f1c5b95e643d29876db3",
        "common_origin": "1109d68feb1b746c675d8f88fb89085334f8f514",
        "relationship": "diverged",
    }
    assert fixture["relationship"] == "diverged"
    assert fixture["source_desired_pin"] != fixture["covering_pin"]
    assert len(fixture["common_origin"]) == 40
