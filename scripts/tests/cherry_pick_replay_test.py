# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import importlib
import subprocess
import threading
import time
from pathlib import Path

import pytest

from scripts.cherry_pick import git as git_engine

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


def valid_expectation(case=None, **overrides):
    case = case or valid_case()
    value = {
        "execution_phase": "core",
        "expected_status": "draft_planned",
        "expected_reason": "clean_trial_application",
        "expected_planned_tree": case["target_after_tree"],
        "expected_conflict_paths": [],
        "expected_after_status": "already_contained",
        "expected_after_reason": "complete_changeset_already_applied",
        "expected_tip_status": "already_contained",
        "expected_tip_reason": "complete_changeset_already_applied",
        "tier": "fast",
    }
    value.update(overrides)
    return value


def valid_reviewed_corpus(cases=None, expectations=None):
    inventory = valid_manifest(cases)
    records = inventory["cases"]
    return {
        "schema_version": 2,
        "inventory": inventory,
        "expectations": expectations
        if expectations is not None
        else {case["id"]: valid_expectation(case) for case in records},
    }


def test_manifest_round_trip_has_strict_typed_contract():
    manifest_type = required("CorpusManifest")
    manifest = manifest_type.from_dict(valid_manifest())

    assert manifest.schema_version == 1
    assert manifest.cases[0].classification.value == "strict_exact"
    assert manifest.as_dict() == valid_manifest()


def test_reviewed_corpus_requires_one_immutable_expectation_per_case():
    reviewed_type = required("ReviewedCorpus")
    value = valid_reviewed_corpus()

    reviewed = reviewed_type.from_dict(value)

    assert reviewed.schema_version == 2
    assert reviewed.expectations[valid_case()["id"]].execution_phase.value == "core"
    assert reviewed.as_dict() == value

    value["expectations"] = {}
    with pytest.raises(ValueError, match="expectation"):
        reviewed_type.from_dict(value)


def test_candidate_comparison_blocks_silent_classification_downgrade():
    manifest_type = required("CorpusManifest")
    reviewed_type = required("ReviewedCorpus")
    compare = required("compare_candidate_to_golden")
    golden = reviewed_type.from_dict(valid_reviewed_corpus())
    downgraded = valid_manifest()
    downgraded["cases"][0]["classification"] = "historical_adaptation"
    candidate = manifest_type.from_dict(downgraded)

    result = compare(candidate, golden)

    assert result.exit_code == 2
    assert result.added_case_ids == ()
    assert result.removed_case_ids == ()
    assert result.changed_case_ids == (valid_case()["id"],)


@pytest.mark.parametrize(
    "branch",
    [
        "release/bkc/therock-10.1-20260811",
        "release/rocm-rel-7.2",
        "release-staging/rocm-rel-7.0",
        "staging/candidate-1",
    ],
)
def test_mirror_spec_accepts_any_safe_destination_branch(branch):
    spec_type = required("MirrorSpec")

    spec = spec_type(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=(branch,),
    )

    assert spec.target_branches == (branch,)


@pytest.mark.parametrize("branch", ["-bad", "bad..name", "refs/heads/main", "bad name"])
def test_mirror_spec_rejects_unsafe_destination_branch(branch):
    spec_type = required("MirrorSpec")

    with pytest.raises(ValueError, match="target branch"):
        spec_type(
            repository="ROCm/TheRock",
            source_branch="main",
            target_branches=(branch,),
        )


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
            "### Cherry-picked PRs:\n#6275 - labels\nwrapped description\n#6269 - defaults\n### Changes:\n#999 is unrelated",
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
        (
            "Fix VM reset. Cherry-pick #3925 (#3934)",
            "Release validation details.",
            (3925,),
            (),
            "explicit_source_pr",
        ),
        (
            "fix(jax): cherry-pick release fixes (#7074)",
            "Cherry-picks required fixes:\n\n#6956 — first\n#6968 — second\n#6708 — third",
            (6956, 6968, 6708),
            (),
            "explicit_source_pr",
        ),
        (
            "[CherryPick] Fix dependency (#4043)",
            "Cherrypick of https://github.com/ROCm/TheRock/pull/4033",
            (4033,),
            (),
            "explicit_source_pr",
        ),
        (
            "rocr: Cache probes (#4048)",
            "ROCM-2928 (cherry-picking\n[https://github.com/ROCm/rocm-systems/pull/3508])",
            (3508,),
            (),
            "explicit_source_pr",
        ),
        (
            "fix(jax): cherry-pick release fixes (#7074)",
            "Cherry-picks required fixes from main into\nrelease/therock-10.0:\n\n#6956 — first\n#6968 — second",
            (6956, 6968),
            (),
            "explicit_source_pr",
        ),
        (
            "Workflow pinning (#5551)",
            "3. Cherry-pick [3d58a508d9cc5f5e5fa5a9c961b9f977fc375658\n](https://github.com/ROCm/rocm-libraries/commit/3d58a508d9cc5f5e5fa5a9c961b9f977fc375658)",
            (),
            ("3d58a508d9cc5f5e5fa5a9c961b9f977fc375658",),
            "explicit_commit",
        ),
        (
            "rocjitsu bump (#9880)",
            "## Original commits\n\n* 41bdeac5c11c7c8a5d86dde74ae47c013a04eb5e\n* 74324f666a320b5f0ce38043b7049bc4105a5b72",
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


def test_unqualified_body_urls_and_shas_are_not_source_provenance():
    extract = required("extract_provenance")
    result = extract(
        "feat: Add JAX release support (#6054) (#6202)",
        "Test: https://github.com/ROCm/example/pull/65 at " + "9" * 40,
        repository="ROCm/TheRock",
    )
    assert result.source_prs == (6054,)
    assert result.source_commits == ()


def test_dependency_cherry_pick_url_is_not_the_current_change_provenance():
    extract = required("extract_provenance")
    mentions = required("mentions_cherry_pick")
    subject = "HIP patch version bump for 7.12 cherry-pick (#4010)"
    body = (
        "For 7.12 cherry-pick https://github.com/ROCm/rocm-systems/pull/4009, "
        "we need to bump HIP patch version in the release branch."
    )
    result = extract(
        subject,
        body,
        repository="ROCm/rocm-systems",
    )

    assert result.source_prs == ()
    assert result.source_commits == ()
    assert mentions(subject, body) is False


def test_contextual_cherry_pick_reference_is_not_a_source_claim():
    mentions = required("mentions_cherry_pick")

    assert (
        mentions(
            "Adjusting workflow files to support release/therock-7.14 (#7867)",
            "Pin CI so that cherry pick PRs to this branch run on the release branch.",
        )
        is False
    )
    assert mentions("Cherry-pick required fixes (#201)", "") is True


def test_explicit_plural_cherry_pick_title_is_a_multi_source_claim():
    claimed_bundle = required("is_multi_source_claim")

    assert claimed_bundle("Cherry-pick commits needed for Hotswap v2 (#9624)")
    assert claimed_bundle("cherry picks for Hotswap improvements (#9786)")
    assert not claimed_bundle("Cherry-pick required fixes (#201)")


def test_conventional_revert_title_is_diagnostic_not_source_provenance():
    is_revert = required("is_revert_subject")
    extract = required("extract_provenance")
    subject = "revert(ck): magic division (#8983) (#9110)"

    assert is_revert(subject) is True
    assert extract(
        subject, "Original commit " + "a" * 40, repository="ROCm/rocm-libraries"
    ) == required("Provenance")((), (), "none")


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


def test_reviewed_replay_checks_forward_and_postmerge_outcomes(repo):
    run_reviewed = required("run_reviewed_case")
    expectation_type = required("ReplayExpectation")
    case = exact_case(repo)
    expectation = expectation_type.from_dict(valid_expectation(case.as_dict()))

    outcome = run_reviewed(
        repo,
        case,
        expectation,
        target_tip=case.target_after,
    )

    assert outcome.disposition.value == "passed"
    assert outcome.execution_phase == "core"
    assert outcome.engine_status == "draft_planned"
    assert outcome.postmerge_status == "already_contained"
    assert outcome.postmerge_reason == "complete_changeset_already_applied"
    assert outcome.tip_status == "already_contained"
    assert outcome.expectation_mismatches == ()


def test_reviewed_replay_fails_when_expected_behavior_is_relaxed(repo):
    run_reviewed = required("run_reviewed_case")
    expectation_type = required("ReplayExpectation")
    case = exact_case(repo)
    expectation = expectation_type.from_dict(
        valid_expectation(
            case.as_dict(),
            expected_status="blocked_conflict",
            expected_reason="cherry_pick_conflict",
        )
    )

    outcome = run_reviewed(
        repo,
        case,
        expectation,
        target_tip=case.target_after,
    )

    assert outcome.disposition.value == "strict_failure"
    assert "engine_status" in outcome.expectation_mismatches
    assert "engine_reason" in outcome.expectation_mismatches


def test_batch_replay_is_bounded_parallel_and_preserves_case_order(
    monkeypatch, tmp_path
):
    run_cases = required("run_replay_cases")
    case_type = required("HistoricalReplayCase")
    repositories = (
        ("ROCm/TheRock", "main"),
        ("ROCm/rocm-systems", "develop"),
        ("ROCm/rocm-libraries", "develop"),
    )
    cases = []
    for number in range(1, 7):
        repository, source_branch = repositories[(number - 1) % len(repositories)]
        cases.append(
            case_type.from_dict(
                valid_case(
                    id=f"case-{number}",
                    repository=repository,
                    source_branch=source_branch,
                    target_after=f"{number:040x}",
                )
            )
        )
    cases = tuple(cases)
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    worktrees = {}

    def fake_run(_repo, case, *, worktree_path=None):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            worktrees.setdefault(case.repository, set()).add(worktree_path)
        time.sleep(0.03)
        with lock:
            active -= 1
        return case.id

    monkeypatch.setattr(replay, "run_replay_case", fake_run)

    outcomes = run_cases(tmp_path, cases, jobs=3)

    assert outcomes == tuple(case.id for case in cases)
    assert 1 < maximum_active <= 3
    assert worktrees == {
        repository: {
            tmp_path / ".cherry-pick-replay-worktrees" / repository.split("/", 1)[1]
        }
        for repository, _source_branch in repositories
    }
    with pytest.raises(ValueError, match="jobs"):
        run_cases(tmp_path, cases, jobs=0)


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


def test_corpus_qualification_reuses_persistent_repository_index(monkeypatch, tmp_path):
    build = required("build_corpus_manifest")
    mirror_spec = required("MirrorSpec")
    data_root, _release_setup, _target_tip = corpus_repository(tmp_path)
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )
    worktree_adds = 0
    original_run = git_engine._run

    def capture_run(repo, *args, **kwargs):
        nonlocal worktree_adds
        if args[:2] == ("worktree", "add"):
            worktree_adds += 1
        return original_run(repo, *args, **kwargs)

    monkeypatch.setattr(git_engine, "_run", capture_run)

    first = build((spec,), data_root)
    second = build((spec,), data_root)

    assert first.as_dict() == second.as_dict()
    assert worktree_adds == 1
    assert (data_root / ".cherry-pick-replay-worktrees" / "TheRock").exists()


def test_unmerged_explicit_pr_head_is_retained_as_diagnostic_adaptation(tmp_path):
    build = required("build_corpus_manifest")
    mirror_spec = required("MirrorSpec")
    path = tmp_path / "data" / "rocm-systems.git"
    path.mkdir(parents=True)
    git(path, "init", "-b", "develop")
    git(path, "config", "user.name", "Replay Test")
    git(path, "config", "user.email", "replay@example.com")
    base = commit_file(path, "base.txt", "base\n", "base")
    git(path, "checkout", "-b", "unmerged-topic")
    source_head = commit_file(path, "fix.txt", "fix\n", "unmerged fix")
    git(path, "update-ref", "refs/pull/100/head", source_head)
    git(path, "checkout", "-b", "release/therock-7.14", base)
    git(path, "cherry-pick", source_head)
    git(
        path,
        "commit",
        "--amend",
        "-m",
        "Release fix (#200)",
        "-m",
        "Cherry-picking https://github.com/ROCm/rocm-systems/pull/100",
    )
    target = git(path, "rev-parse", "HEAD")
    git(path, "update-ref", "refs/remotes/origin/develop", base)
    git(
        path,
        "update-ref",
        "refs/remotes/origin/release/therock-7.14",
        target,
    )
    spec = mirror_spec(
        repository="ROCm/rocm-systems",
        source_branch="develop",
        target_branches=("release/therock-7.14",),
    )

    manifest = build((spec,), path.parent)
    case = next(item for item in manifest.cases if item.target_after == target)

    assert case.source_prs == (100,)
    assert case.source_head == source_head
    assert case.source_commits == (source_head,)
    assert case.classification.value == "historical_adaptation"
    assert "not merged" in case.analysis_notes.lower()


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


def test_inventory_audit_rederives_premerge_parent_and_known_good_tree(tmp_path):
    build = required("build_corpus_manifest")
    audit_inventory = required("audit_manifest_inventory")
    mirror_spec = required("MirrorSpec")
    manifest_type = required("CorpusManifest")
    data_root, _release_setup, target_tip = corpus_repository(tmp_path)
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )
    manifest = build((spec,), data_root)
    value = manifest.as_dict()
    target_case = next(
        case for case in value["cases"] if case["target_after"] == target_tip
    )
    target_case["target_before"] = target_tip
    target_case["target_after_tree"] = "f" * 40
    tampered = manifest_type.from_dict(value)

    result = audit_inventory(tampered, data_root)
    assert result.exit_code == 2
    assert result.evidence_gap_count == 1


def test_pull_request_discovery_skips_cross_repository_gitlink_rollups(tmp_path):
    discover = required("discover_corpus_pull_requests")
    mirror_spec = required("MirrorSpec")
    path = tmp_path / "data" / "TheRock.git"
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Replay Test")
    git(path, "config", "user.email", "replay@example.com")
    base = commit_file(path, "base.txt", "base\n", "base")
    git(path, "checkout", "-b", "release/therock-7.14")
    git(path, "update-index", "--add", "--cacheinfo", f"160000,{base},component")
    git(path, "commit", "-m", "Bump component (#100) (#200)")
    target = git(path, "rev-parse", "HEAD")
    git(path, "update-ref", "refs/remotes/origin/main", base)
    git(
        path,
        "update-ref",
        "refs/remotes/origin/release/therock-7.14",
        target,
    )
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )

    result = discover((spec,), path.parent)
    assert result == {"ROCm/TheRock": ()}


def test_pull_request_discovery_skips_nested_gitlink_rollups(tmp_path):
    build = required("build_corpus_manifest")
    discover = required("discover_corpus_pull_requests")
    mirror_spec = required("MirrorSpec")
    path = tmp_path / "data" / "TheRock.git"
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Replay Test")
    git(path, "config", "user.email", "replay@example.com")
    base = commit_file(path, "base.txt", "base\n", "base")
    git(path, "checkout", "-b", "release/therock-7.14")
    git(
        path, "update-index", "--add", "--cacheinfo", f"160000,{base},compiler/amd-llvm"
    )
    git(path, "commit", "-m", "Bump amd-llvm to include cherry-pick (#200)")
    target = git(path, "rev-parse", "HEAD")
    git(path, "update-ref", "refs/remotes/origin/main", base)
    git(
        path,
        "update-ref",
        "refs/remotes/origin/release/therock-7.14",
        target,
    )
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )

    result = discover((spec,), path.parent)
    manifest = build((spec,), path.parent)
    case = next(item for item in manifest.cases if item.target_after == target)

    assert result == {"ROCm/TheRock": ()}
    assert case.classification.value == "gitlink_rollup"


def test_corpus_classifies_explicit_plural_cherry_pick_as_bundle(tmp_path):
    build = required("build_corpus_manifest")
    mirror_spec = required("MirrorSpec")
    data_root, _release_setup, target_tip = corpus_repository(tmp_path)
    path = data_root / "TheRock.git"
    git(path, "checkout", "release/therock-7.14")
    assert git(path, "rev-parse", "HEAD") == target_tip
    bundle = commit_file(
        path,
        "bundle.txt",
        "bundle\n",
        "Cherry-pick commits needed for Hotswap (#201)",
    )
    git(
        path,
        "update-ref",
        "refs/remotes/origin/release/therock-7.14",
        bundle,
    )
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )

    manifest = build((spec,), data_root)
    case = next(item for item in manifest.cases if item.target_after == bundle)

    assert case.classification.value == "multi_source_bundle"


def test_corpus_leaves_cherry_pick_claim_without_source_identity_unresolved(tmp_path):
    build = required("build_corpus_manifest")
    mirror_spec = required("MirrorSpec")
    data_root, _release_setup, target_tip = corpus_repository(tmp_path)
    path = data_root / "TheRock.git"
    git(path, "checkout", "release/therock-7.14")
    assert git(path, "rev-parse", "HEAD") == target_tip
    ambiguous = commit_file(
        path,
        "ambiguous.txt",
        "unknown\n",
        "Cherry-pick required fixes (#201)",
    )
    git(
        path,
        "update-ref",
        "refs/remotes/origin/release/therock-7.14",
        ambiguous,
    )
    spec = mirror_spec(
        repository="ROCm/TheRock",
        source_branch="main",
        target_branches=("release/therock-7.14",),
    )

    manifest = build((spec,), data_root)
    case = next(item for item in manifest.cases if item.target_after == ambiguous)
    assert case.classification.value == "unresolved"
    assert "provenance" in case.analysis_notes.lower()
