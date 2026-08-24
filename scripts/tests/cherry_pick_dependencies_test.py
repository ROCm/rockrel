# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import pytest

from scripts.cherry_pick.dependencies import (
    DependencyError,
    build_dependency_graph,
    parse_dependency_trailers,
    parse_dependency_url,
)


ROOT = "https://github.com/ROCm/TheRock/pull/100"
SYSTEMS = "https://github.com/ROCm/rocm-systems/pull/200"
LIBRARIES = "https://github.com/ROCm/rocm-libraries/pull/300"
COMMIT_SHA = "3a3fb3206000a3b47e953fd6613571ae6ca0edb4"
COMMIT = f"https://github.com/ROCm/rocm-systems/commit/{COMMIT_SHA}"


def test_parses_only_repeated_footer_trailers_and_deduplicates_in_order():
    body = f"""This prose mentions Depends-On: {LIBRARIES} but is not a footer.

Details.

Depends-On: {SYSTEMS}
depends-on: {LIBRARIES}
Depends-On: {SYSTEMS}
Reviewed-By: Release Operator <operator@example.com>
"""

    refs = parse_dependency_trailers(body)

    assert [item.url for item in refs] == [SYSTEMS, LIBRARIES]
    assert refs[0].repository == "ROCm/rocm-systems"
    assert refs[0].number == 200
    assert refs[0].kind == "pull_request"
    assert refs[0].commit_sha is None


def test_parses_canonical_full_commit_trailer_as_a_typed_prerequisite():
    refs = parse_dependency_trailers(f"Details\n\nDepends-On: {COMMIT}\n")

    assert refs == (parse_dependency_url(COMMIT),)
    assert refs[0].kind == "commit"
    assert refs[0].repository == "ROCm/rocm-systems"
    assert refs[0].number is None
    assert refs[0].commit_sha == COMMIT_SHA


def test_empty_body_and_non_dependency_trailers_have_no_edges():
    assert parse_dependency_trailers("") == ()
    assert parse_dependency_trailers("Title\n\nReviewed-By: A <a@example.com>\n") == ()


@pytest.mark.parametrize(
    "value",
    [
        "ROCm/TheRock#1",
        "a" * 40,
        "http://github.com/ROCm/TheRock/pull/1",
        "https://gitlab.com/ROCm/TheRock/pull/1",
        "https://github.com/someone/TheRock/pull/1",
        "https://github.com/ROCm/unsupported/pull/1",
        "https://github.com/ROCm/TheRock/issues/1",
        "https://github.com/ROCm/TheRock/pull/1?diff=split",
        "https://github.com/ROCm/TheRock/pull/0",
        "https://github.com/ROCm/rocm-systems/commit/3a3fb32",
        f"https://github.com/ROCm/rocm-systems/commit/{COMMIT_SHA.upper()}",
        f"https://github.com/ROCm/unsupported/commit/{COMMIT_SHA}",
    ],
)
def test_rejects_noncanonical_dependency_values(value):
    with pytest.raises(
        DependencyError, match="canonical ROCm pull request or full commit URL"
    ):
        parse_dependency_trailers(f"Message\n\nDepends-On: {value}\n")


def test_builds_deterministic_transitive_dag_independent_of_mapping_order():
    first = build_dependency_graph(
        ROOT,
        {
            LIBRARIES: (),
            ROOT: (LIBRARIES, SYSTEMS),
            SYSTEMS: (LIBRARIES,),
        },
        max_nodes=64,
        max_depth=16,
    )
    second = build_dependency_graph(
        ROOT,
        {
            SYSTEMS: (LIBRARIES,),
            ROOT: (SYSTEMS, LIBRARIES),
            LIBRARIES: (),
        },
        max_nodes=64,
        max_depth=16,
    )

    assert first == second
    assert first.nodes == (LIBRARIES, SYSTEMS)
    assert first.edges == (
        (ROOT, LIBRARIES),
        (ROOT, SYSTEMS),
        (SYSTEMS, LIBRARIES),
    )
    assert first.topological_order == (LIBRARIES, SYSTEMS)


def test_rejects_self_dependency_and_transitive_cycle():
    with pytest.raises(DependencyError) as self_error:
        build_dependency_graph(ROOT, {ROOT: (ROOT,)}, max_nodes=64, max_depth=16)
    assert self_error.value.reason_code == "dependency_cycle"

    with pytest.raises(DependencyError) as cycle_error:
        build_dependency_graph(
            ROOT,
            {ROOT: (SYSTEMS,), SYSTEMS: (LIBRARIES,), LIBRARIES: (ROOT,)},
            max_nodes=64,
            max_depth=16,
        )
    assert cycle_error.value.reason_code == "dependency_cycle"


def test_rejects_missing_adjacency_and_unreachable_extra_nodes():
    with pytest.raises(DependencyError) as missing:
        build_dependency_graph(ROOT, {ROOT: (SYSTEMS,)}, max_nodes=64, max_depth=16)
    assert missing.value.reason_code == "dependency_evidence_missing"

    graph = build_dependency_graph(
        ROOT,
        {ROOT: (), SYSTEMS: ()},
        max_nodes=64,
        max_depth=16,
    )
    assert graph.nodes == ()


def test_commit_prerequisites_are_deterministic_leaf_nodes():
    graph = build_dependency_graph(
        ROOT,
        {ROOT: (SYSTEMS,), SYSTEMS: (COMMIT,), COMMIT: ()},
        max_nodes=64,
        max_depth=16,
    )

    assert graph.nodes == (COMMIT, SYSTEMS)
    assert graph.topological_order == (COMMIT, SYSTEMS)

    with pytest.raises(DependencyError) as outgoing:
        build_dependency_graph(
            ROOT,
            {ROOT: (COMMIT,), COMMIT: (SYSTEMS,), SYSTEMS: ()},
            max_nodes=64,
            max_depth=16,
        )
    assert outgoing.value.reason_code == "commit_prerequisite_not_leaf"


def test_enforces_node_and_depth_limits_at_the_boundary():
    with pytest.raises(DependencyError) as nodes:
        build_dependency_graph(
            ROOT,
            {ROOT: (SYSTEMS, LIBRARIES), SYSTEMS: (), LIBRARIES: ()},
            max_nodes=1,
            max_depth=16,
        )
    assert nodes.value.reason_code == "dependency_node_limit"

    with pytest.raises(DependencyError) as depth:
        build_dependency_graph(
            ROOT,
            {ROOT: (SYSTEMS,), SYSTEMS: (LIBRARIES,), LIBRARIES: ()},
            max_nodes=64,
            max_depth=1,
        )
    assert depth.value.reason_code == "dependency_depth_limit"


@pytest.mark.parametrize("max_nodes,max_depth", [(0, 1), (1, 0), (True, 1)])
def test_rejects_invalid_runtime_limits(max_nodes, max_depth):
    with pytest.raises(ValueError):
        build_dependency_graph(
            ROOT,
            {ROOT: ()},
            max_nodes=max_nodes,
            max_depth=max_depth,
        )
