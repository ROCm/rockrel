# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.cherry_pick.authorization import (
    AuthorizationEnvelope,
    LabelTransition,
    authorized_plan_fingerprint,
)
from scripts.cherry_pick.config import (
    AuthorizationPolicy,
    CoveragePolicy,
    DependencyPolicy,
    PrerequisiteOverride,
    RepositoryConfig,
    TrainCatalog,
    TrainConfig,
)
from scripts.cherry_pick.dependencies import DependencyError
from scripts.cherry_pick.models import Result, Status
from scripts.cherry_pick.orchestrator import (
    Planner,
    automation_branch,
    discover_train_ids,
    identity_marker,
    normalize_coverage_pulls,
    render_pull_body,
    render_status_comment,
    status_marker,
)
from scripts.cherry_pick.refs import RefHydrationError

SOURCE_URL = "https://github.com/ROCm/TheRock/pull/7282"
SYSTEMS_URL = "https://github.com/ROCm/rocm-systems/pull/8793"
COMMIT_SHA = "3a3fb3206000a3b47e953fd6613571ae6ca0edb4"
COMMIT_URL = f"https://github.com/ROCm/rocm-systems/commit/{COMMIT_SHA}"
SOURCE_SHA = "a" * 40
SOURCE_HEAD = "b" * 40
DESTINATION_SHA = "c" * 40
CONFIG_REVISION = "d" * 40
LABEL = "cherry-pick:10.1-20260811"


def config(
    mode="validate",
    *,
    state="active",
    trusted_app_ids=(123456,),
    executor_app_id=654321,
    max_nodes=64,
    max_depth=16,
    max_open_pull_requests=128,
    prerequisite_overrides=(),
    dependency_mode="gate",
):
    train = TrainConfig(
        id="10.1-20260811",
        label=LABEL,
        state=state,
        mode=mode,
        dependency_mode=dependency_mode,
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branches=("main", "integration/next"),
                destination_branch="release/bkc/therock-10.1-20260811",
            ),
            "ROCm/rocm-systems": RepositoryConfig(
                source_branches=("develop",),
                destination_branch="release/bkc/therock-10.1-20260811",
            ),
        },
        prerequisite_overrides=prerequisite_overrides,
    )
    return TrainCatalog(
        trains={train.id: train},
        authorization=AuthorizationPolicy(
            minimum_human_permission="write",
            trusted_app_ids=trusted_app_ids,
            executor_app_id=executor_app_id,
        ),
        dependency_policy=DependencyPolicy(
            max_nodes=max_nodes,
            max_depth=max_depth,
        ),
        coverage_policy=CoveragePolicy(
            max_open_pull_requests=max_open_pull_requests,
        ),
    )


def pull(
    *,
    repository="ROCm/TheRock",
    number=7282,
    body="Source body",
    merged=True,
    base=None,
    head=SOURCE_HEAD,
    merge=SOURCE_SHA,
):
    owner, name = repository.split("/", 1)
    return {
        "number": number,
        "html_url": f"https://github.com/{owner}/{name}/pull/{number}",
        "title": "Compiler update",
        "body": body,
        "state": "closed" if merged else "open",
        "merged": merged,
        "merged_at": "2026-08-16T10:00:00Z" if merged else None,
        "merge_commit_sha": merge if merged else None,
        "commits": 1,
        "head": {
            "sha": head,
            "ref": f"topic/{number}",
            "repo": {"full_name": repository},
        },
        "base": {
            "ref": base or ("main" if name == "TheRock" else "develop"),
            "sha": DESTINATION_SHA,
        },
        "labels": [{"name": LABEL}],
    }


def github(*, source=None, dependency=None, app_id=None, permission="write"):
    client = Mock()
    source = source or pull()
    dependency = dependency or pull(
        repository="ROCm/rocm-systems",
        number=8793,
        head="e" * 40,
        merge="f" * 40,
    )
    by_number = {7282: source, 8793: dependency}
    client.pull.side_effect = lambda owner, repo, number: by_number[number]
    client.pull_commits.side_effect = lambda owner, repo, number: (
        ("1" * 40,) if number == 7282 else ("2" * 40,)
    )
    client.label_transitions.return_value = (
        LabelTransition(
            event_id=10,
            node_id="LE_10",
            label=LABEL,
            action="labeled",
            created_at="2026-08-16T09:00:00Z",
            actor_id=7,
            actor_login="operator" if app_id is None else "approved-labeler[bot]",
            performed_via_app_id=app_id,
        ),
    )
    client.permission.return_value = permission
    client.branch.return_value = SimpleNamespace(exists=True, sha=DESTINATION_SHA)
    client.destination_policy.return_value = SimpleNamespace(
        pull_request_required=True,
        rule_ids=(20,),
        required_approvals=1,
        require_last_push_approval=True,
        allowed_merge_methods=("squash",),
    )
    client.pulls.return_value = []
    return client


def core_result(status=Status.DRAFT_PLANNED):
    return Result(
        status=status,
        reason_code="clean_trial_application",
        message="clean",
        evidence={"planned_tree": "9" * 40},
    )


def planner(
    *,
    catalog=None,
    client=None,
    core=None,
    hydrator=None,
    commit_hydrator=None,
    coverage_hydrator=None,
):
    core = core or Mock()
    core.plan.return_value = core_result()
    hydrator = hydrator or Mock()
    return (
        Planner(
            catalog or config(),
            client or github(),
            core_planner=core,
            ref_hydrator=hydrator,
            commit_hydrator=commit_hydrator or Mock(),
            coverage_hydrator=coverage_hydrator or Mock(),
            config_revision=CONFIG_REVISION,
        ),
        core,
    )


def repos(tmp_path):
    return {
        "ROCm/TheRock": tmp_path / "TheRock",
        "ROCm/rocm-systems": tmp_path / "rocm-systems",
    }


def open_coverage_pull(*, number=9001):
    candidate = pull(
        number=number,
        merged=False,
        base="release/bkc/therock-10.1-20260811",
    )
    candidate.update(
        {
            "html_url": f"https://github.com/ROCm/TheRock/pull/{number}",
            "draft": False,
            "state": "open",
            "head": {
                "sha": "7" * 40,
                "ref": f"manual/backport-{number}",
                "repo": {"full_name": "ROCm/TheRock"},
            },
            "base": {
                "ref": "release/bkc/therock-10.1-20260811",
                "sha": DESTINATION_SHA,
            },
        }
    )
    return candidate


@pytest.mark.parametrize("fingerprint", ["short", "F" * 64])
def test_identity_marker_rejects_noncanonical_plan_fingerprint(fingerprint):
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        identity_marker("ROCm/TheRock", 7282, "10.1-20260811", fingerprint)


def test_open_pull_normalization_ignores_forks_and_fails_closed_on_bad_evidence():
    destination = "release/bkc/therock-10.1-20260811"
    fork = open_coverage_pull()
    fork["head"]["repo"]["full_name"] = "someone/fork"
    assert (
        normalize_coverage_pulls(
            [fork], repository="ROCm/TheRock", destination_branch=destination
        )
        == []
    )

    malformed = open_coverage_pull()
    malformed["draft"] = "false"
    bad_url = open_coverage_pull()
    bad_url["html_url"] = "https://github.com/ROCm/TheRock/issues/9001"
    mismatched_url = open_coverage_pull()
    mismatched_url["number"] = 9002

    for candidate in (malformed, bad_url, mismatched_url):
        with pytest.raises(DependencyError, match="open destination pull request"):
            normalize_coverage_pulls(
                [deepcopy(candidate)],
                repository="ROCm/TheRock",
                destination_branch=destination,
            )


def test_validate_mode_is_manual_only_and_disabled_mode_does_no_api_io(tmp_path):
    for mode, event_action, expected in (
        ("validate", "labeled", "validate_mode_manual_only"),
        ("disabled", "manual", "train_disabled"),
    ):
        client = github()
        controller, core = planner(catalog=config(mode=mode), client=client)
        result = controller.plan(
            SOURCE_URL,
            "10.1-20260811",
            repos(tmp_path),
            event_action=event_action,
        )
        assert result.status is Status.CANCELLED
        assert result.reason_code == expected
        client.pull.assert_not_called()
        core.plan.assert_not_called()

    inactive, core = planner(catalog=config(mode="shadow", state="inactive"))
    result = inactive.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.INELIGIBLE_SOURCE
    core.plan.assert_not_called()


def test_open_authorized_pr_awaits_merge_without_core_work(tmp_path):
    client = github(source=pull(merged=False))
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    result = controller.plan(
        SOURCE_URL, "10.1-20260811", repos(tmp_path), event_action="labeled"
    )
    assert result.status is Status.AWAITING_MERGE
    assert result.evidence["authorization"]["label_event_id"] == 10
    core.plan.assert_not_called()


def test_continuation_requires_exact_trusted_label_snapshot(tmp_path):
    source = pull()
    client = github(source=source)
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    initial = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="labeled",
    )
    expected_external_id = AuthorizationEnvelope.from_dict(
        initial.evidence["authorization"]
    ).check_external_id()
    core.plan.reset_mock()

    client.trusted_check_external_ids.return_value = ()
    missing = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="edited",
    )
    assert missing.status is Status.BLOCKED_AUTHORIZATION
    assert missing.reason_code == "authorization_snapshot_missing_or_stale"
    core.plan.assert_not_called()

    client.trusted_check_external_ids.return_value = (expected_external_id,)
    unchanged = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="synchronize",
    )
    assert unchanged.status is Status.DRAFT_PLANNED
    client.trusted_check_external_ids.assert_called_with(
        "ROCm",
        "TheRock",
        head_sha=SOURCE_HEAD,
        name="ROCm Cherry-Pick / 10.1-20260811",
        executor_app_id=654321,
    )

    core.plan.reset_mock()
    source["body"] = "Body changed after the request label"
    stale = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="closed",
    )
    assert stale.status is Status.BLOCKED_AUTHORIZATION
    assert stale.reason_code == "authorization_snapshot_missing_or_stale"
    core.plan.assert_not_called()


def test_automated_mode_requires_configured_executor_app_identity(tmp_path):
    controller, core = planner(
        catalog=config(mode="shadow", executor_app_id=None),
    )

    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="labeled",
    )

    assert result.status is Status.BLOCKED_AUTHORIZATION
    assert result.reason_code == "executor_app_identity_unconfigured"
    core.plan.assert_not_called()


def test_planner_rejects_unknown_execution_context():
    with pytest.raises(ValueError, match="execution_context"):
        Planner(config(), github(), execution_context="unknown")


def test_local_gh_planning_uses_current_label_authority_without_executor_app(tmp_path):
    controller, core = planner(
        catalog=config(mode="create-draft", executor_app_id=None),
    )
    controller.execution_context = "local-gh"

    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="manual",
    )

    assert result.status is Status.DRAFT_PLANNED
    assert result.evidence["execution_context"] == "local-gh"
    controller.github.trusted_check_external_ids.assert_not_called()
    core.plan.assert_called_once()


def test_local_materialization_context_is_read_only_and_does_not_require_label(
    tmp_path,
):
    source = pull()
    source["labels"] = []
    client = github(source=source)
    controller, core = planner(catalog=config(), client=client)
    controller.execution_context = "local-materialize"

    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="manual",
    )

    assert result.status is Status.DRAFT_PLANNED
    assert result.evidence["authorization"]["kind"] == "local_only_operator_request"
    client.label_transitions.assert_not_called()
    client.permission.assert_not_called()
    core.plan.assert_called_once()


def test_local_control_plane_snapshot_is_copied_and_bound_into_plan_fingerprint(
    tmp_path,
):
    snapshot = {
        "schema_version": "release-hub-train-snapshot.v1",
        "train_id": "10.1-20260811",
        "configuration_sha256": "a" * 64,
        "destination_branch": "release/bkc/therock-10.1-20260811",
    }
    client = github()
    core = Mock()
    core.plan.return_value = core_result()
    controller = Planner(
        config(),
        client,
        core_planner=core,
        ref_hydrator=Mock(),
        commit_hydrator=Mock(),
        coverage_hydrator=Mock(),
        config_revision=CONFIG_REVISION,
        execution_context="local-materialize",
        control_plane_snapshot=snapshot,
    )
    snapshot["configuration_sha256"] = "f" * 64

    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="manual",
    )

    bound = result.evidence["authorization"]["control_plane_snapshot"]
    assert bound["configuration_sha256"] == "a" * 64
    changed = deepcopy(result.evidence["authorization"])
    changed["control_plane_snapshot"]["configuration_sha256"] = "f" * 64
    changed_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "core_request_sha256": result.evidence["core_request_fingerprint"],
                "local_authorization": changed,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert result.evidence["plan_fingerprint"] != changed_fingerprint


@pytest.mark.parametrize(
    "execution_context,snapshot,reason",
    [
        ("github-app", {"schema_version": "snapshot.v1"}, "only for local-materialize"),
        ("local-gh", {"schema_version": "snapshot.v1"}, "only for local-materialize"),
        ("local-materialize", {}, "non-empty object"),
        ("local-materialize", [], "non-empty object"),
        ("local-materialize", {"invalid": object()}, "JSON serializable"),
    ],
)
def test_control_plane_snapshot_rejects_unsafe_context_shape_or_value(
    execution_context, snapshot, reason
):
    with pytest.raises(ValueError, match=reason):
        Planner(
            config(),
            github(),
            execution_context=execution_context,
            control_plane_snapshot=snapshot,
        )


def test_human_and_allowlisted_app_can_authorize_but_untrusted_actor_cannot(tmp_path):
    human, _ = planner(
        catalog=config(mode="shadow"), client=github(permission="maintain")
    )
    assert (
        human.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path)).status
        is Status.DRAFT_PLANNED
    )

    app, _ = planner(
        catalog=config(mode="shadow"), client=github(app_id=123456, permission="none")
    )
    assert (
        app.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path)).status
        is Status.DRAFT_PLANNED
    )

    denied, core = planner(
        catalog=config(mode="shadow"), client=github(app_id=999, permission="none")
    )
    result = denied.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.BLOCKED_AUTHORIZATION
    assert result.reason_code == "label_actor_unauthorized"
    core.plan.assert_not_called()


def test_planner_queries_permission_only_for_latest_eligible_human_transition(
    tmp_path,
):
    client = github()
    client.label_transitions.return_value = (
        LabelTransition(
            event_id=9,
            node_id="LE_9",
            label=LABEL,
            action="labeled",
            created_at="2026-08-16T08:00:00Z",
            actor_id=8,
            actor_login="older-operator",
        ),
        client.label_transitions.return_value[0],
    )
    controller, _core = planner(catalog=config(mode="shadow"), client=client)

    assert (
        controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path)).status
        is Status.DRAFT_PLANNED
    )
    client.permission.assert_called_once_with("ROCm", "TheRock", "operator")

    bot = github()
    bot.label_transitions.return_value = (
        LabelTransition(
            event_id=10,
            node_id="LE_10",
            label=LABEL,
            action="labeled",
            created_at="2026-08-16T09:00:00Z",
            actor_id=8,
            actor_login="unattributed[bot]",
        ),
    )
    controller, _core = planner(catalog=config(mode="shadow"), client=bot)
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.BLOCKED_AUTHORIZATION
    bot.permission.assert_not_called()

    missing_label_pull = pull()
    missing_label_pull["labels"] = []
    missing_label = github(source=missing_label_pull)
    controller, _core = planner(
        catalog=config(mode="shadow"),
        client=missing_label,
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.reason_code == "train_label_missing"
    missing_label.permission.assert_not_called()


def test_planner_builds_immutable_core_request_and_passes_all_repository_paths(
    tmp_path,
):
    controller, core = planner(catalog=config(mode="shadow"))
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.DRAFT_PLANNED
    request, repository_paths = core.plan.call_args.args
    assert request.source.url == SOURCE_URL
    assert request.source.head_sha == SOURCE_HEAD
    assert request.source.merge_sha == SOURCE_SHA
    assert request.source.destination.head_sha == DESTINATION_SHA
    assert request.prerequisites == ()
    assert request.coverage_candidates == ()
    assert repository_paths == repos(tmp_path)
    assert result.evidence["core_request_fingerprint"] == request.fingerprint()
    assert result.evidence["plan_fingerprint"] == authorized_plan_fingerprint(
        request.fingerprint(),
        result.evidence["authorization"],
    )
    assert result.evidence["plan_fingerprint"] != request.fingerprint()
    assert result.evidence["train_mode"] == "shadow"


def test_resolves_transitive_dependency_graph_before_core(tmp_path):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    client = github(source=source)
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    request = core.plan.call_args.args[0]
    assert tuple(node.url for node in request.prerequisites) == (SYSTEMS_URL,)
    assert tuple((edge.source, edge.target) for edge in request.prerequisite_edges) == (
        (SOURCE_URL, SYSTEMS_URL),
    )
    assert request.prerequisites[0].destination.branch == (
        "release/bkc/therock-10.1-20260811"
    )


@pytest.mark.parametrize(
    "source_body,dependency_body,limits,reason",
    [
        (
            f"Body\n\nDepends-On: {SYSTEMS_URL}\n",
            "Body\n\nDepends-On: https://github.com/ROCm/rocm-systems/pull/8794\n",
            {"max_depth": 1},
            "dependency_depth_limit",
        ),
        (
            "Body\n\n"
            f"Depends-On: {SYSTEMS_URL}\n"
            "Depends-On: https://github.com/ROCm/rocm-systems/pull/8794\n",
            "Body",
            {"max_nodes": 1},
            "dependency_node_limit",
        ),
    ],
)
def test_dependency_limits_stop_resolution_before_fetching_excess_nodes(
    tmp_path, source_body, dependency_body, limits, reason
):
    source = pull(body=source_body)
    dependency = pull(
        repository="ROCm/rocm-systems",
        number=8793,
        body=dependency_body,
        head="e" * 40,
        merge="f" * 40,
    )
    excess = pull(
        repository="ROCm/rocm-systems",
        number=8794,
        head="3" * 40,
        merge="4" * 40,
    )
    client = github(source=source, dependency=dependency)
    pulls = {7282: source, 8793: dependency, 8794: excess}
    client.pull.side_effect = lambda owner, repo, number: pulls[number]
    controller, core = planner(
        catalog=config(mode="shadow", **limits),
        client=client,
    )

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.BLOCKED_DEPENDENCY
    assert result.reason_code == reason
    assert 8794 not in [call.args[2] for call in client.pull.call_args_list]
    core.plan.assert_not_called()


def test_hydrates_every_exact_pull_and_destination_ref_before_core(tmp_path):
    events = []
    hydrator = Mock(side_effect=lambda *args, **kwargs: events.append((args, kwargs)))
    core = Mock()
    core.plan.side_effect = lambda *args: events.append(("core", args)) or core_result()
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    controller, _ = planner(
        catalog=config(mode="shadow"),
        client=github(source=source),
        core=core,
        hydrator=hydrator,
    )

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.DRAFT_PLANNED
    assert hydrator.call_count == 2
    dependency_call, source_call = hydrator.call_args_list
    assert dependency_call.args[0] == repos(tmp_path)["ROCm/rocm-systems"]
    assert dependency_call.kwargs["pull_number"] == 8793
    assert source_call.args[0] == repos(tmp_path)["ROCm/TheRock"]
    assert source_call.kwargs["pull_number"] == 7282
    assert events[-1][0] == "core"


def test_reviewed_override_adds_typed_commit_sequence_without_parsing_prose(tmp_path):
    override = PrerequisiteOverride(
        source_pr=SOURCE_URL,
        rationale="Maintainer-reviewed ordering for the train.",
        edges=(
            (SOURCE_URL, SYSTEMS_URL),
            (SYSTEMS_URL, COMMIT_URL),
        ),
    )
    client = github(source=pull(body=f"Prose mentions {SYSTEMS_URL}, but no trailer."))
    controller, core = planner(
        catalog=config(mode="shadow", prerequisite_overrides=(override,)),
        client=client,
    )

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.DRAFT_PLANNED
    request = core.plan.call_args.args[0]
    assert [item.url for item in request.prerequisites] == [COMMIT_URL, SYSTEMS_URL]
    assert request.prerequisites[0].kind == "commit"
    assert request.prerequisites[0].commit_sha == COMMIT_SHA
    assert [edge.as_dict() for edge in request.prerequisite_edges] == [
        {"from": SOURCE_URL, "to": SYSTEMS_URL},
        {"from": SYSTEMS_URL, "to": COMMIT_URL},
    ]
    assert [call.args[2] for call in client.pull.call_args_list] == [7282, 8793]
    assert result.evidence["prerequisite_sources"] == {
        "trailers": [],
        "reviewed_overrides": [
            {
                "rationale": "Maintainer-reviewed ordering for the train.",
                "edges": [
                    {"from": SOURCE_URL, "to": SYSTEMS_URL},
                    {"from": SYSTEMS_URL, "to": COMMIT_URL},
                ],
            }
        ],
    }


def test_all_open_destination_pulls_are_snapshotted_as_core_coverage_candidates(
    tmp_path,
):
    candidate = pull(
        number=9001, merged=False, base="release/bkc/therock-10.1-20260811"
    )
    candidate.update(
        {
            "html_url": "https://github.com/ROCm/TheRock/pull/9001",
            "draft": False,
            "state": "open",
            "head": {
                "sha": "7" * 40,
                "ref": "manual/backport-7282",
                "repo": {"full_name": "ROCm/TheRock"},
            },
            "base": {
                "ref": "release/bkc/therock-10.1-20260811",
                "sha": DESTINATION_SHA,
            },
        }
    )
    client = github()
    client.pulls.return_value = [candidate]
    controller, core = planner(catalog=config(mode="shadow"), client=client)

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.DRAFT_PLANNED
    request = core.plan.call_args.args[0]
    assert [item.as_dict() for item in request.coverage_candidates] == [
        {
            "url": "https://github.com/ROCm/TheRock/pull/9001",
            "repository": "ROCm/TheRock",
            "number": 9001,
            "state": "open",
            "draft": False,
            "base_branch": "release/bkc/therock-10.1-20260811",
            "base_sha": DESTINATION_SHA,
            "head_repository": "ROCm/TheRock",
            "head_sha": "7" * 40,
        }
    ]
    assert result.evidence["coverage_snapshot_sha256"] == (
        request.coverage_snapshot_sha256()
    )
    client.pulls.assert_called_with(
        "ROCm",
        "TheRock",
        base="release/bkc/therock-10.1-20260811",
        state="open",
    )


def test_open_pull_candidate_limit_fails_closed_before_core(tmp_path):
    client = github()
    client.pulls.return_value = [
        {
            **pull(
                number=number,
                merged=False,
                base="release/bkc/therock-10.1-20260811",
            ),
            "number": number,
            "html_url": f"https://github.com/ROCm/TheRock/pull/{number}",
            "draft": False,
        }
        for number in (9001, 9002)
    ]
    controller, core = planner(
        catalog=config(mode="shadow", max_open_pull_requests=1),
        client=client,
    )

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "coverage_candidate_limit"
    core.plan.assert_not_called()


def test_ref_hydration_failure_blocks_before_core(tmp_path):
    hydrator = Mock(side_effect=RefHydrationError("pull_head_mismatch", "head changed"))
    controller, core = planner(catalog=config(mode="shadow"), hydrator=hydrator)

    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "pull_head_mismatch"
    core.plan.assert_not_called()


def test_unmerged_dependency_waits_without_calling_core(tmp_path):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    dependency = pull(
        repository="ROCm/rocm-systems", number=8793, merged=False, head="e" * 40
    )
    controller, core = planner(
        catalog=config(mode="shadow"),
        client=github(source=source, dependency=dependency),
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.AWAITING_DEPENDENCIES
    assert result.reason_code == "dependency_not_merged"
    assert result.evidence["dependency_url"] == SYSTEMS_URL
    core.plan.assert_not_called()


def test_invalid_dependency_graph_and_missing_destination_rule_fail_closed(tmp_path):
    cyclic = pull(body=f"Body\n\nDepends-On: {SOURCE_URL}\n")
    controller, core = planner(
        catalog=config(mode="shadow"), client=github(source=cyclic)
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.BLOCKED_DEPENDENCY
    assert result.reason_code == "dependency_cycle"
    core.plan.assert_not_called()

    client = github()
    client.destination_policy.return_value.pull_request_required = False
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "destination_pull_request_rule_missing"
    core.plan.assert_not_called()


def test_dependency_identity_and_repository_mapping_fail_closed(tmp_path):
    source = pull(body=f"Body\n\nDepends-On: {SYSTEMS_URL}\n")
    mismatched = pull(
        repository="ROCm/rocm-systems", number=8793, head="e" * 40, merge="f" * 40
    )
    mismatched["html_url"] = "https://github.com/ROCm/rocm-systems/pull/9999"
    controller, core = planner(
        catalog=config(mode="shadow"),
        client=github(source=source, dependency=mismatched),
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.reason_code == "dependency_identity_mismatch"
    core.plan.assert_not_called()

    controller, core = planner(catalog=config(mode="shadow"))
    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        {"ROCm/rocm-systems": tmp_path / "systems"},
    )
    assert result.reason_code == "local_repository_missing"
    core.plan.assert_not_called()


@pytest.mark.parametrize(
    "source_update,client_update,reason",
    [
        (
            {"base": {"ref": "release/wrong"}},
            None,
            "dependency_source_branch_ineligible",
        ),
        ({"merge_commit_sha": ""}, None, "dependency_merge_identity_missing"),
        (None, "missing_branch", "destination_branch_missing"),
        (None, "empty_commits", "change_commit_list_incomplete"),
    ],
)
def test_source_manifest_evidence_failures_are_structured(
    tmp_path, source_update, client_update, reason
):
    source = pull()
    if source_update:
        source.update(source_update)
    client = github(source=source)
    if client_update == "missing_branch":
        client.branch.return_value = SimpleNamespace(exists=False, sha=None)
    elif client_update == "empty_commits":
        client.pull_commits.return_value = ()
        client.pull_commits.side_effect = None
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.reason_code == reason
    core.plan.assert_not_called()


@pytest.mark.parametrize("declared_count", [None, True, 0])
def test_source_commit_count_must_be_available_and_positive(tmp_path, declared_count):
    source = pull()
    source["commits"] = declared_count
    controller, core = planner(
        catalog=config(mode="shadow"),
        client=github(source=source),
    )

    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="labeled",
    )

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "change_commit_count_invalid"
    core.plan.assert_not_called()


def test_incomplete_pull_commit_listing_blocks_before_core(tmp_path):
    source = pull()
    source["commits"] = 2
    controller, core = planner(
        catalog=config(mode="shadow"),
        client=github(source=source),
    )

    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="labeled",
    )

    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "change_commit_list_incomplete"
    core.plan.assert_not_called()


def test_untyped_ref_hydration_failure_is_sanitized(tmp_path):
    controller, core = planner(
        catalog=config(mode="shadow"),
        hydrator=Mock(side_effect=RuntimeError("local secret detail")),
    )
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.reason_code == "ref_hydration_unavailable"
    assert "secret" not in result.message
    core.plan.assert_not_called()


def test_unlabeled_event_cancels_when_current_label_is_absent(tmp_path):
    source = pull()
    source["labels"] = []
    client = github(source=source)
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    result = controller.plan(
        SOURCE_URL,
        "10.1-20260811",
        repos(tmp_path),
        event_action="unlabeled",
    )
    assert result.status is Status.CANCELLED
    assert result.reason_code == "train_label_removed"
    core.plan.assert_not_called()


def test_api_and_manifest_failures_are_structured_without_raw_exception(tmp_path):
    client = github()
    client.label_transitions.side_effect = RuntimeError("secret internal detail")
    controller, core = planner(catalog=config(mode="shadow"), client=client)
    result = controller.plan(SOURCE_URL, "10.1-20260811", repos(tmp_path))
    assert result.status is Status.BLOCKED_EVIDENCE
    assert result.reason_code == "github_evidence_unavailable"
    assert "secret internal detail" not in result.message
    core.plan.assert_not_called()


def test_pull_body_is_operator_grade_and_contains_no_jira_contract():
    marker = identity_marker("ROCm/TheRock", 7282, "10.1-20260811", "f" * 64)
    body = render_pull_body(
        marker=marker,
        source_url=SOURCE_URL,
        source_repository="ROCm/TheRock",
        source_sha=SOURCE_SHA,
        source_head=SOURCE_HEAD,
        train_id="10.1-20260811",
        destination_branch="release/bkc/therock-10.1-20260811",
        destination_head=DESTINATION_SHA,
        changeset_kind="merge_commit",
        ordered_commits=(SOURCE_SHA,),
        mainline=1,
        dependencies=(SYSTEMS_URL,),
        dependency_status="contained",
        proof_method="merge_second_parent",
        expected_tree="9" * 40,
        source_body="Original source text",
    )
    assert marker in body
    for heading in (
        "Operator review required",
        "Source and destination",
        "Application and provenance",
        "Dependencies",
        "Test plan and result",
        "Submission checklist",
        "Original source pull request",
    ):
        assert heading in body
    assert "Commands executed to create the cherry-pick" in body
    assert "git -c core.hooksPath=/dev/null cherry-pick -x -m 1" in body
    assert SYSTEMS_URL in body
    assert "Expected tree" in body
    assert "Jira" not in body
    assert "never marks this PR ready or merges it" in body


def test_pull_body_records_each_exact_materialization_command_in_order():
    first = "1" * 40
    second = "2" * 40
    body = render_pull_body(
        marker=identity_marker("ROCm/TheRock", 7, "train", "f" * 64),
        source_url=SOURCE_URL,
        source_repository="ROCm/TheRock",
        source_sha=second,
        source_head=second,
        train_id="train",
        destination_branch="release/test",
        destination_head=DESTINATION_SHA,
        changeset_kind="commit_sequence",
        ordered_commits=(first, second),
        mainline=None,
        dependencies=(),
        dependency_status="contained",
        proof_method="normalized_patch_identity",
        expected_tree="9" * 40,
        source_body="Source",
    )
    commands = [
        f"git -c core.hooksPath=/dev/null cherry-pick -x {first}",
        f"git -c core.hooksPath=/dev/null cherry-pick -x {second}",
    ]
    assert all(command in body for command in commands)
    assert body.index(commands[0]) < body.index(commands[1])
    assert f"cherry-pick -x {first} {second}" not in body


def test_identity_branch_discovery_and_status_rendering_are_stable():
    assert automation_branch("10.1-20260811", 7282) == (
        "shared/cherry-pick/10.1-20260811/7282"
    )
    assert identity_marker("ROCm/TheRock", 7282, "10.1-20260811", "f" * 64) == (
        "<!-- cherry-pick:v2:ROCm/TheRock#7282:10.1-20260811:" + "f" * 64 + " -->"
    )
    assert status_marker("train") == "<!-- cherry-pick-status:train -->"

    active = config(mode="create-draft").trains["10.1-20260811"]
    catalog = TrainCatalog(
        trains={
            "active": SimpleNamespace(
                id="active",
                label="cherry-pick:active",
                state=active.state,
                mode=active.mode,
            ),
            "validate": SimpleNamespace(
                id="validate",
                label="cherry-pick:validate",
                state="active",
                mode="validate",
            ),
            "inactive": SimpleNamespace(
                id="inactive",
                label="cherry-pick:inactive",
                state="inactive",
                mode="shadow",
            ),
        }
    )
    assert discover_train_ids(
        catalog,
        current_labels=("cherry-pick:active", "cherry-pick:validate"),
        event_action="labeled",
        event_label="cherry-pick:active",
    ) == ("active",)

    rendered = render_status_comment(
        Result(
            status=Status.DRAFT_CREATED,
            reason_code="draft_pull_created",
            message="created",
            source_pr=SOURCE_URL,
            source_repository="ROCm/TheRock",
            train_id="train",
            destination_branch="release/test",
            pull_request_url="https://github.com/ROCm/TheRock/pull/9000",
        )
    )
    assert "draft_created" in rendered
    assert "pull/9000" in rendered


def test_planner_source_has_no_jira_dependency():
    source = Path("scripts/cherry_pick/orchestrator.py").read_text()
    assert "Jira" not in source
    assert "jira" not in source
