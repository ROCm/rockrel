# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import importlib
import subprocess
from pathlib import Path

import pytest

try:
    replay = importlib.import_module("scripts.cherry_pick.replay")
except ModuleNotFoundError:
    replay = None


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40


def required(name):
    assert replay is not None, "scripts.cherry_pick.replay must exist"
    value = getattr(replay, name, None)
    assert value is not None, f"replay module must define {name}"
    return value


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
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content)
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Replay Test")
    git(path, "config", "user.email", "replay@example.com")
    commit_file(path, "value.txt", "base\n", "base")
    return path


def valid_case(**overrides):
    value = {
        "id": "ROCm-TheRock-7.14-deadbeef",
        "repository": "ROCm/TheRock",
        "source_branch": "main",
        "target_branch": "release/therock-7.14",
        "source_prs": [100],
        "source_merge_commit": SHA_A,
        "source_head": SHA_B,
        "source_commits": [SHA_B],
        "target_before": SHA_C,
        "target_after": SHA_D,
        "target_after_tree": "e" * 40,
        "provenance_method": "explicit_source_pr",
        "classification": "strict_exact",
        "analysis_notes": "Exact one-source historical backport.",
    }
    value.update(overrides)
    return value


def valid_manifest(cases=None):
    return {
        "schema_version": 1,
        "snapshots": {
            "ROCm/TheRock": {
                "source_branch": "main",
                "source_tip": "f" * 40,
                "targets": {"release/therock-7.14": "1" * 40},
            }
        },
        "cases": cases if cases is not None else [valid_case()],
    }


def test_manifest_round_trip_has_strict_typed_contract():
    manifest_type = required("CorpusManifest")
    manifest = manifest_type.from_dict(valid_manifest())

    assert manifest.schema_version == 1
    assert manifest.cases[0].classification.value == "strict_exact"
    assert manifest.as_dict() == valid_manifest()


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value.update(schema_version=2), "schema_version"),
        (lambda value: value["cases"][0].update(target_before="short"), "SHA"),
        (lambda value: value["cases"][0].update(source_prs=[]), "source_prs"),
        (
            lambda value: value["cases"].append(dict(value["cases"][0])),
            "duplicate",
        ),
    ],
)
def test_manifest_rejects_unsafe_or_ambiguous_shapes(mutation, match):
    manifest_type = required("CorpusManifest")
    value = valid_manifest()
    mutation(value)
    with pytest.raises(ValueError, match=match):
        manifest_type.from_dict(value)


def test_manifest_audit_blocks_unresolved_cases():
    manifest_type = required("CorpusManifest")
    audit = required("audit_manifest")
    unresolved = valid_case(
        classification="unresolved",
        source_prs=[],
        source_merge_commit=None,
        source_head=None,
        source_commits=[],
        analysis_notes="Source provenance could not be established.",
    )
    manifest = manifest_type.from_dict(valid_manifest([unresolved]))

    result = audit(manifest)
    assert result.exit_code == 2
    assert result.evidence_gap_count == 1


@pytest.mark.parametrize(
    "subject,body,expected_prs,expected_commits,method",
    [
        (
            "Fix queue idle (#8113) (#10005)",
            "Cherry-picks commit 22af9e98a3dad737d0a7b153c712622139e5a2e6.",
            (8113,),
            ("22af9e98a3dad737d0a7b153c712622139e5a2e6",),
            "explicit_commit",
        ),
        (
            "Cherry pick workflow fix (#3898)",
            "Cherry pick #3896",
            (3896,),
            (),
            "explicit_source_pr",
        ),
        (
            "Cherry-pick runner updates (#6385)",
            "Cherry-picked PRs:\n#6275 - labels\n#6269 - defaults",
            (6275, 6269),
            (),
            "explicit_source_pr",
        ),
        (
            "rocjitsu bundle (#9880)",
            "Original commits\n* 41bdeac5c11c7c8a5d86dde74ae47c013a04eb5e\n* 74324f666a320b5f0ce38043b7049bc4105a5b72",
            (),
            (
                "41bdeac5c11c7c8a5d86dde74ae47c013a04eb5e",
                "74324f666a320b5f0ce38043b7049bc4105a5b72",
            ),
            "explicit_commit",
        ),
    ],
)
def test_extracts_only_explicit_historical_provenance(
    subject, body, expected_prs, expected_commits, method
):
    extract = required("extract_provenance")
    result = extract(subject, body, repository="ROCm/TheRock")
    assert result.source_prs == expected_prs
    assert result.source_commits == expected_commits
    assert result.method == method


def test_revert_title_does_not_treat_quoted_pr_as_source_provenance():
    extract = required("extract_provenance")
    result = extract(
        'Revert "HIP patch version bump for cherry-pick (#4010)" (#4235)',
        "This reverts the release-only patch bump.",
        repository="ROCm/rocm-systems",
    )
    assert result.source_prs == ()
    assert result.source_commits == ()


def test_inventory_includes_every_first_parent_release_only_commit(repo):
    inventory = required("inventory_release_commits")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "release")
    first = commit_file(repo, "release-a.txt", "a\n", "Backport A (#1) (#10)")
    second = commit_file(repo, "release-b.txt", "b\n", "Release-only B (#11)")
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "main\n", "advance main")

    records = inventory(repo, "main", "release")
    assert [item.after for item in records] == [first, second]
    assert records[0].before == base
    assert records[1].before == first


def exact_case(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "topic")
    original = commit_file(repo, "source.txt", "source\n", "source change")
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "main\n", "advance main")
    git(repo, "cherry-pick", original)
    source_merge = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "release", base)
    before = commit_file(repo, "release.txt", "release\n", "release setup")
    git(repo, "cherry-pick", source_merge)
    after = git(repo, "rev-parse", "HEAD")
    after_tree = git(repo, "rev-parse", "HEAD^{tree}")
    case_type = required("HistoricalReplayCase")
    case = case_type.from_dict(
        valid_case(
            source_merge_commit=source_merge,
            source_head=original,
            source_commits=[original],
            target_before=before,
            target_after=after,
            target_after_tree=after_tree,
        )
    )
    return case


def corpus_repository(tmp_path):
    path = tmp_path / "replay-data" / "TheRock.git"
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Replay Test")
    git(path, "config", "user.email", "replay@example.com")
    base = commit_file(path, "value.txt", "base\n", "base")
    git(path, "checkout", "-b", "topic")
    original = commit_file(path, "source.txt", "source\n", "source change")
    git(path, "checkout", "main")
    git(path, "cherry-pick", original)
    git(path, "commit", "--amend", "-m", "source change (#100)")
    source_merge = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-b", "release/therock-7.14", base)
    release_setup = commit_file(path, "release.txt", "release\n", "release setup")
    git(path, "cherry-pick", source_merge)
    git(path, "commit", "--amend", "-m", "source change (#100) (#200)")
    target_tip = git(path, "rev-parse", "HEAD")
    git(path, "update-ref", "refs/remotes/origin/main", source_merge)
    git(
        path,
        "update-ref",
        "refs/remotes/origin/release/therock-7.14",
        target_tip,
    )
    git(path, "update-ref", "refs/pull/100/head", original)
    return path.parent, release_setup, target_tip


def test_exact_historical_replay_is_a_strict_tree_gate(repo):
    run_case = required("run_replay_case")
    outcome = run_case(repo, exact_case(repo))

    assert outcome.disposition.value == "passed"
    assert outcome.engine_status == "draft_planned"
    assert outcome.planned_tree == outcome.historical_tree
    assert outcome.strict_failure is False


def test_missing_source_object_is_an_evidence_gap(repo):
    run_case = required("run_replay_case")
    case = exact_case(repo)
    case_type = type(case)
    missing = case_type.from_dict({**case.as_dict(), "source_merge_commit": "9" * 40})

    outcome = run_case(repo, missing)
    assert outcome.disposition.value == "evidence_gap"
    assert outcome.root_cause == "missing_source_evidence"


def test_offline_git_environment_disables_lazy_fetch_and_prompts():
    environment = required("offline_git_environment")({"EXISTING": "value"})
    assert environment["EXISTING"] == "value"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_HTTP_LOW_SPEED_LIMIT"] == "1024"
    assert environment["GIT_HTTP_LOW_SPEED_TIME"] == "120"


def test_refresh_commands_are_official_read_only_and_disable_push(tmp_path):
    mirror_spec = required("MirrorSpec")
    commands_for = required("build_refresh_commands")
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=(
            "release/therock-7.12",
            "release/therock-7.14",
            "release/therock-10.0",
        ),
    )

    commands = commands_for(spec, tmp_path / "TheRock.git", (3897, 6160))
    flattened = [item for command in commands for item in command.args]
    assert "https://github.com/ROCm/TheRock.git" in flattened
    assert "disabled://read-only" in flattened
    assert "http.version=HTTP/1.1" in flattened
    assert "push" not in [command.args[1] for command in commands]
    assert any("refs/pull/3897/head" in item for item in flattened)
    assert any("refs/heads/release/therock-10.0" in item for item in flattened)


def test_refresh_rejects_unapproved_repository_and_requires_explicit_authority(
    tmp_path,
):
    mirror_spec = required("MirrorSpec")
    refresh = required("refresh_mirror")
    with pytest.raises(ValueError, match="supported"):
        mirror_spec(
            repository="ROCm/rockrel",
            source_branch="main",
            target_branches=("release/therock-7.14",),
        )
    valid = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )
    with pytest.raises(PermissionError, match="read-only network"):
        refresh(valid, tmp_path / "TheRock.git", allow_read_only_network=False)


def test_markdown_report_explains_strict_failures_and_diagnostics(repo):
    report_type = required("ReplayReport")
    render = required("render_markdown_report")
    run_case = required("run_replay_case")
    outcome = run_case(repo, exact_case(repo))
    report = report_type.from_outcomes((outcome,))

    text = render(report)
    assert "Historical cherry-pick replay" in text
    assert "Strict eligible" in text
    assert "Evidence gaps" in text
    assert outcome.case_id in text
    assert report.exit_code == 0


def test_builds_exhaustive_corpus_and_auto_qualifies_only_exact_case(tmp_path):
    build = required("build_corpus_manifest")
    audit_inventory = required("audit_manifest_inventory")
    mirror_spec = required("MirrorSpec")
    data_root, release_setup, target_tip = corpus_repository(tmp_path)
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )

    manifest = build((spec,), data_root)

    assert {case.target_after for case in manifest.cases} == {
        release_setup,
        target_tip,
    }
    strict = [
        case for case in manifest.cases if case.classification.value == "strict_exact"
    ]
    assert len(strict) == 1
    assert strict[0].source_prs == (100,)
    assert strict[0].source_head
    assert strict[0].source_commits
    assert audit_inventory(manifest, data_root).exit_code == 0


def test_inventory_audit_detects_a_manifest_that_drops_a_release_commit(tmp_path):
    build = required("build_corpus_manifest")
    audit_inventory = required("audit_manifest_inventory")
    mirror_spec = required("MirrorSpec")
    manifest_type = required("CorpusManifest")
    data_root, _release_setup, _target_tip = corpus_repository(tmp_path)
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )
    manifest = build((spec,), data_root)
    incomplete = manifest_type.from_dict(
        {**manifest.as_dict(), "cases": manifest.as_dict()["cases"][1:]}
    )

    result = audit_inventory(incomplete, data_root)
    assert result.exit_code == 2
    assert result.evidence_gap_count == 1
