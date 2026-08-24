# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from scripts.cherry_pick import orchestrator
from scripts.cherry_pick.config import (
    RepositoryConfig,
    TrainCatalog,
    TrainConfig,
)


def train(train_id, *, mode="shadow", state="active"):
    return TrainConfig(
        id=train_id,
        label=f"cherry-pick:{train_id}",
        state=state,
        mode=mode,
        repositories={
            "ROCm/TheRock": RepositoryConfig(
                source_branches=("main",), destination_branch=f"release/{train_id}"
            )
        },
    )


def discover(catalog, labels, *, action="labeled", event_label=""):
    function = getattr(orchestrator, "discover_train_ids", None)
    assert function is not None, "central event discovery must be a pure function"
    return function(
        catalog,
        current_labels=tuple(labels),
        event_action=action,
        event_label=event_label,
    )


def test_discovers_multiple_configured_shadow_or_write_train_labels():
    catalog = TrainCatalog(
        trains={
            "one": train("one", mode="shadow"),
            "two": train("two", mode="create-draft"),
        }
    )
    assert discover(
        catalog,
        ["bug", "cherry-pick:two", "cherry-pick:one", "cherry-pick:one"],
    ) == ("one", "two")


def test_unlabeled_event_includes_only_removed_configured_train_for_cancellation():
    catalog = TrainCatalog(trains={"one": train("one"), "two": train("two")})
    assert discover(
        catalog,
        ["cherry-pick:two"],
        action="unlabeled",
        event_label="cherry-pick:one",
    ) == ("one", "two")


def test_ignores_unknown_inactive_disabled_and_validate_only_trains():
    catalog = TrainCatalog(
        trains={
            "shadow": train("shadow"),
            "inactive": train("inactive", state="inactive"),
            "disabled": train("disabled", mode="disabled"),
            "validate": train("validate", mode="validate"),
        }
    )
    labels = [f"cherry-pick:{name}" for name in catalog.trains]
    assert discover(catalog, labels) == ("shadow",)
