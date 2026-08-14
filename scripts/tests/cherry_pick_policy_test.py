from scripts.cherry_pick.config import RepositoryConfig, TrainConfig, TrainRequirements
from scripts.cherry_pick.models import Status
from scripts.cherry_pick.policy import QualificationFacts, qualify_request


def train(**overrides):
    value = TrainConfig(
        id="10.1-20260811",
        label="cherry-pick:10.1-20260811",
        state="active",
        mode="validate",
        requirements=TrainRequirements(jira_fix_version="10.1.0a20260811"),
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branch="main",
                destination_branch="release/bkc/therock-10.1-20260811",
            )
        },
    )
    return TrainConfig(**({**value.__dict__, **overrides}))


def facts(**overrides):
    value = {
        "source_pr": "https://github.com/ROCm/TheRock/pull/123",
        "repository": "ROCm/TheRock",
        "base_branch": "main",
        "merged": True,
        "closed": True,
        "label_actor_permission": "write",
        "jira_fix_versions": frozenset({"10.1.0a20260811"}),
        "destination_exists": True,
        "destination_protected": True,
        "evidence_errors": (),
    }
    value.update(overrides)
    return QualificationFacts(**value)


def test_qualified_merged_request_advances_to_planning():
    result = qualify_request(train(), facts())
    assert result.status is Status.CHERRY_PICK_REQUIRED
    assert result.reason_code == "qualified_for_planning"


def test_open_request_waits_for_merge():
    result = qualify_request(train(), facts(merged=False, closed=False))
    assert result.status is Status.WAITING_FOR_MERGE


def test_closed_unmerged_request_is_cancelled():
    result = qualify_request(train(), facts(merged=False, closed=True))
    assert result.status is Status.CANCELLED


def test_transient_evidence_failure_blocks_and_is_not_invalid():
    result = qualify_request(train(), facts(evidence_errors=("jira_timeout",)))
    assert result.status is Status.BLOCKED
    assert result.reason_code == "evidence_unavailable"


def test_inactive_train_is_invalid():
    result = qualify_request(train(state="inactive"), facts())
    assert result.status is Status.INVALID
    assert result.reason_code == "inactive_train"


def test_unconfigured_repository_is_invalid():
    result = qualify_request(train(), facts(repository="ROCm/other"))
    assert result.status is Status.INVALID
    assert result.reason_code == "repository_not_configured"


def test_wrong_source_base_is_invalid():
    result = qualify_request(train(), facts(base_branch="release/old"))
    assert result.status is Status.INVALID
    assert result.reason_code == "source_branch_mismatch"


def test_unauthorized_labeler_is_invalid():
    result = qualify_request(train(), facts(label_actor_permission="read"))
    assert result.status is Status.INVALID
    assert result.reason_code == "label_actor_not_authorized"


def test_missing_fix_version_is_invalid():
    result = qualify_request(train(), facts(jira_fix_versions=frozenset({"10.2"})))
    assert result.status is Status.INVALID
    assert result.reason_code == "jira_fix_version_mismatch"


def test_train_without_jira_requirement_does_not_require_fix_version():
    result = qualify_request(
        train(requirements=TrainRequirements()),
        facts(jira_fix_versions=frozenset()),
    )
    assert result.status is Status.CHERRY_PICK_REQUIRED


def test_missing_target_is_invalid():
    result = qualify_request(train(), facts(destination_exists=False))
    assert result.status is Status.INVALID
    assert result.reason_code == "destination_branch_missing"


def test_unprotected_target_is_invalid():
    result = qualify_request(train(), facts(destination_protected=False))
    assert result.status is Status.INVALID
    assert result.reason_code == "destination_branch_not_protected"


def test_maintain_and_admin_permissions_are_authorized():
    for permission in ("maintain", "admin"):
        assert qualify_request(
            train(), facts(label_actor_permission=permission)
        ).status is Status.CHERRY_PICK_REQUIRED
