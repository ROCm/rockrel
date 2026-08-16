# Draft — local review required

# Cherry-pick automation TDD evidence

## Rules

- Documentation is updated before remediation product code.
- The complete remediation suite is added before implementation changes.
- Each red test must fail for the intended missing behavior, not because of a
  syntax error, broken fixture, or accidental network access.
- Product implementation starts only after the red suite and failures are
  recorded below.
- Tests use local filesystem Git repositories and fake transports only.

## Baseline before remediation

Date: 2026-08-15

```text
$ .venv/bin/python -m pytest -q scripts/tests
165 passed in 1.59s

$ git diff --check
passed

$ .venv/bin/python -m black --check scripts/cherry_pick scripts/tests scripts/render_cherry_pick_workflow.py
/home/jusharri/code/label-driven-cherrypick-automation/rockrel/.venv/bin/python: No module named black
```

The missing Black executable is a local tool limitation, not a passing gate. No
network installation is permitted.

## Remediation red suite

Date: 2026-08-15

The complete remediation suite was written before product implementation
changes and run only against local files, temporary Git repositories, and fake
transports.

```text
$ .venv/bin/python -m pytest -q --tb=no scripts/tests
112 failed, 86 passed in 2.41s

$ python3 -m unittest build_tools.tests.cherry_pick_request_test  # TheRock
3 tests run; 2 intended caller-contract failures

$ python3 .github/scripts/tests/cherry_pick_request_test.py       # rocm-systems
4 tests run; 2 intended caller-contract failures

$ python3 .github/scripts/tests/cherry_pick_request_test.py       # rocm-libraries
4 tests run; 3 intended caller/CI-contract failures
```

The failures map to the planned missing behavior:

- schema v3, canonical ref validation, source-branch sets, and mode semantics;
- explicit result/status contract and required identity;
- effective pull-request rules, typed branch/Jira evidence, dependencies;
- complete squash, merge-commit, and rebase-range proof;
- exact/partial containment and conflict classification;
- transport isolation, pagination, bounded retry, and response validation;
- writer capability, Git identity, partial-write and fresh-clone recovery;
- rich draft rendering and idempotent existing-draft behavior;
- centralized label discovery and thin source callers;
- impossible local-review workflow write gates, least privilege, and Python
  setup;
- SPDX/format/coverage infrastructure.

There were no collection, syntax, fixture-setup, or network failures. Existing
unaffected behavior remained green in 86 tests. The caller tests failed on the
intended duplicated discovery logic and stale CI naming.

## First green implementation

Product implementation began only after the red test commit. The first green
controller commit produced:

```text
$ .venv/bin/python -m pytest -q scripts/tests
198 passed
```

Relevant local commits:

- `cbcd6ae` — complete remediation test contract (red);
- `a295011` — central implementation (first green);
- TheRock `cf103047`, rocm-systems `c914f5639b`, and rocm-libraries
  `f874736809` — thin-caller contracts (red).

Two test-fixture corrections were made before the green assertion: an unlabeled
event was exercised in `shadow` because `validate` is manual-only, and the Git
identity assertion checked the committer because cherry-pick intentionally
preserves the source author. These were contract clarifications, not relaxed
product expectations.

## Second red/green slice

Review of the first implementation found five additional fail-closed cases.
They were committed as tests before the corresponding fix:

```text
$ pytest <five selected tests>  # at 7d31b78
5 failed in 0.54s
```

The intended failures proved that the old implementation:

- did not retry a rate-limit `403`;
- treated a patch-equivalent subset of a rebase range as clean planning;
- treated a closed-unmerged PR as an active draft;
- allowed an existing PR to hide a mismatched deterministic branch tree; and
- propagated a PR-lookup API error instead of returning blocked evidence.

Commit `077aba1` implemented those five cases. Pinned Black 25.11.0 then found
and mechanically formatted branch-modified Python in `c53e703`; tests remained
green:

```text
$ .venv/bin/python -m pytest -q scripts/tests
203 passed in 2.25s
```

## Source caller green results

The canonical template was rendered against the full local controller SHA
`c53e703568fe41129abf7139f018ac920bca9c59`. Repository-local results:

```text
TheRock:        3 tests, OK
rocm-systems:   4 tests, OK
rocm-libraries: 4 tests, OK
```

The final local caller commits at evidence time are TheRock `8113258f`,
rocm-systems `ea11e53142`, and rocm-libraries `f3ccfdba38`.

## Final local gates

Passed locally with no network access:

- 203 rockrel pytest tests and 11 source-caller unittest tests;
- Black 25.11.0 on every branch-modified Python file;
- actionlint 1.7.10 on every changed workflow/template;
- mdformat 0.7.21 on the local project Markdown set;
- JSON parsing for train configuration, App manifest, and fixtures;
- SPDX/header and automation-module/test pairing assertions in pytest;
- `git diff --check` in all four repositories.

Not run and not claimed as passing:

```text
$ .venv/bin/python -m coverage --version
No module named coverage
```

Neither `pytest-cov` nor `coverage.py` exists in the local virtual environment,
system Python, pre-commit environments, or package cache. The no-network rule
forbids installing it. `.github/workflows/unit_tests.yml` enforces both line and
branch coverage at 90% for a future separately approved CI run; until then the
coverage success criterion remains explicitly unverified.

## Historical replay and reusable-index slice

Date: 2026-08-15 through 2026-08-16

Historical replay was implemented in independently committed red/green slices.
The red commits cover manifest validation, exhaustive inventory, exact
pre-merge/after-tree endpoints, provenance ambiguity, offline operation,
parallel ordering, disk-backed worktrees, persistent index reuse, contextual
dependency provenance, unmerged PR heads, and corrupt-index recovery. Relevant
red commits include `5ba0672`, `339cd2c`, `eecf040`, `aeba4f6`, `389db7e`,
`a3442e3`, `a106725`, `26cfc7d`, `176ce69`, `69b62b8`, `8834d12`, and
`cbb4ac7`. Each selected test failed for its intended missing behavior before
the paired implementation commit.

The final local results are:

```text
$ .venv/bin/python -m pytest -q scripts/tests
254 passed

$ .venv/bin/python scripts/replay_cherry_pick_history.py rollback ...
3 repositories rolled back and verified in 4.1 seconds

$ .venv/bin/python scripts/replay_cherry_pick_history.py freeze ...
77 cases; 31 strict; 46 diagnostic; 0 evidence gaps

$ .venv/bin/python scripts/replay_cherry_pick_history.py run ... --jobs 4
77 cases; 31 passed; 46 diagnostic; exit 0
```

The first disk-backed cache population took 4m42s. The verified warm freeze
took 1m52s, approximately 60% faster. Full standalone parallel replay runs took
1m00s to 1m36s with warm indexes. The corpus contains 3 real conflict
diagnostics and 4 clean planned tree mismatches. A deliberately zero-filled test
index is recovered from an atomic snapshot; the real interrupted cache was
repaired locally by the rollback command without fetching, cloning, or
recreating its worktree.

## Reviewed-oracle and containment hardening slice

The product requirements, technical design, and runbook were corrected in
local commit `9b90160` before this slice changed tests or product code.

The complete first red contract added typed source identity, exact reachable
application containment, explicit-revert blocking, conflict path/stage
evidence, schema-v2 reviewed expectations, downgrade detection, generic safe
destination refs, forward/post-merge reviewed replay, a filesystem-only full
planner/writer simulator, and safe inventory/compare CLI behavior.

```text
$ .venv/bin/python -m pytest -q scripts/tests
20 failed, 254 passed in 4.72s
```

The failures were the intended missing behavior: four Git containment/evidence
contracts, eight reviewed-corpus and generic-branch contracts, three local
pipeline simulation contracts, and five safe CLI contracts. No production
Python changed before this red result was captured.

The paired implementation added those contracts and migrated the reviewed
fixture to schema v2 only after generating it outside the repository and
comparing it with the reviewed inventory. The candidate digest was
`a7cad5c2bf23b9635eb2a3896ee094ec7dc88cc08ce8f9f2f31d69b38ae336a0`.

```text
$ .venv/bin/python -m pytest -q scripts/tests
274 passed in 6.75s

$ .venv/bin/python scripts/replay_cherry_pick_history.py run \
    --data-root /home/jusharri/code/rocm-cherrypick-replay-data \
    --manifest scripts/tests/fixtures/historical_cherry_picks.json \
    --report-dir /home/jusharri/code/rocm-cherrypick-replay-data/reports-v2 \
    --tier deep --jobs 4
77 cases; 31 passed; 46 diagnostic; 0 expectation mismatches
```

All 31 core cases were also evaluated at the known post-merge commit and the
release tip. Each returned `already_contained`: 20 by complete changeset patch
identity and 11 by an exact, reachable destination application. The remaining
46 cases retain explicit inventory-only diagnostic reasons rather than being
misrepresented as engine passes.

## Coverage-audit and oracle-mutation slice

The next tests were written before their implementation. They add Git file
shape and conflict-shape cases, mutate each safety-critical reviewed outcome
field to prove the oracle rejects regressions, and require an explicit combined
historical/synthetic coverage audit.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_git_test.py \
    scripts/tests/cherry_pick_replay_test.py
8 failed, 78 passed in 4.28s
```

The eight intended failures were six missing
`compare_outcome_to_expectation` contracts and two missing
`audit_replay_coverage` contracts. The new delete, rename, executable mode,
symlink, binary, add/add, delete/modify, and rename/rename Git cases already
passed against the preceding engine implementation.

The paired implementation extracted the reviewed-field oracle into a pure
comparator and added a deterministic coverage auditor that keeps historical
counts and named synthetic evidence separate.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_git_test.py \
    scripts/tests/cherry_pick_replay_test.py
86 passed in 5.14s
```

## Coverage-dimension and standalone-gate slice

The next red contract requires real Git-derived coverage dimensions, generic
destination-family reporting, stable change-size boundaries, a typed registry
of named synthetic tests, inventory/core separation, report-level fail-closed
coverage, and standalone CLI integration.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_replay_test.py \
    scripts/tests/replay_cherry_pick_history_test.py
18 failed, 67 passed in 3.65s
```

The intended failures cover five missing destination-family cases, five size
boundaries, Git operation extraction, outcome dimensions, inventory exclusion,
report gating, typed synthetic evidence, and two CLI contracts. No production
code changed before this result was captured.

The implementation now derives coverage from immutable Git endpoints, keeps
inventory-only evidence out of engine cells, validates every synthetic claim
against a required vocabulary and concrete pytest node ID, and makes uncovered
required cells an exit-2 condition in the standalone CLI.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_replay_test.py \
    scripts/tests/replay_cherry_pick_history_test.py
85 passed in 3.63s
```
