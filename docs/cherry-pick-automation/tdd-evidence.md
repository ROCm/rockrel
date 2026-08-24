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

Entries are chronological. “Final” in an older heading means final for that
slice, not the current worktree; later dated sections supersede earlier
current-state counts and status claims.

## Local gh adapter and command-transcript slice

Date: 2026-08-19

Tests were added before implementation for local `gh` REST credentials, the
token-free Git credential helper, explicit one-plan local write authority,
action/local isolation, exact revalidation, and one recorded PR-body command
per materialized commit.

```text
$ .venv/bin/python -m pytest -q <focused files>
2 collection errors: local_runtime missing; gh_git_environment missing
```

After the first implementation pass the same focused suite reported 143
passes and two intended remaining failures: final planner evidence omitted the
local execution context, and the skill scaffold had not yet been created.
The first full green review exposed that the new module was not yet listed in
the per-module coverage gate. A new infrastructure assertion failed, then the
workflow and runbook were updated to enforce it as a critical module.

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

At completion of that implementation slice, before the later reviewed-oracle
and coverage work, the local results were:

```text
$ .venv/bin/python -m pytest -q scripts/tests
254 passed

$ .venv/bin/python scripts/replay_cherry_pick_history.py rollback ...
3 repositories rolled back and verified in 4.1 seconds

$ .venv/bin/python scripts/replay_cherry_pick_history.py inventory ...
77 cases; 31 strict; 46 diagnostic; 0 evidence gaps

$ .venv/bin/python scripts/replay_cherry_pick_history.py run ... --jobs 4
77 cases; 31 passed; 46 diagnostic; exit 0
```

The first disk-backed cache population took 4m42s. A warm inventory took 1m52s,
approximately 60% faster. Full standalone parallel replay runs took 1m00s to
1m36s with warm indexes. The later reviewed corpus identifies three real
conflicts and five clean historical adaptations: four planned-tree mismatches
and one noncanonical merged-source case. A deliberately zero-filled test index
is recovered from an atomic snapshot; the real interrupted cache was repaired
locally by the rollback command without fetching, cloning, or recreating its
worktree.

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

## Fast/deep tier separation slice

The tier-diversity contract was added before changing the reviewed fixture. It
requires a bounded fast subset while retaining all three repositories, all
three historical release lines, every classification, every manual conflict,
and every historical adaptation; deep must still select all 77 rows.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_replay_test.py::test_reviewed_fast_tier_is_minimized_without_dropping_negative_diversity
1 failed in 0.10s
```

The intended failure showed that all 77 rows were marked `fast`, making the
fast and deep gates identical.

The reviewed tier field was then curated locally: 17 cases remain fast and 60
are deep-only. The fast set retains every historical adaptation and manual
conflict plus representative positive and inventory cases across all required
dimensions; the deep set still includes all 77.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_replay_test.py::test_reviewed_fast_tier_is_minimized_without_dropping_negative_diversity
1 passed in 0.07s
```

## Report-schema and reviewer-visibility slice

The final report contract was made red before implementation. It requires the
coverage-bearing JSON to use schema v3 and the Markdown report to name both
historical-only and genuinely uncovered required cells.

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_replay_test.py::test_report_includes_coverage_and_fails_closed_on_a_gap
1 failed in 0.25s
```

The intended failure was the stale schema-v2 marker; the pre-change Markdown
also reported only aggregate counts.

The JSON report now declares schema v3, and Markdown names every historical-only
and uncovered cell (or explicitly says `none`).

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_replay_test.py::test_report_includes_coverage_and_fails_closed_on_a_gap
1 passed in 0.15s
```

## Final replay evidence

The current local draft produced the following standalone results with the
persistent disk-backed worktrees:

```text
$ replay ... --tier fast --jobs 4
17 cases; 5 passed; 12 diagnostic; 0 expectation mismatches;
0 combined coverage gaps; 67.45 seconds

$ replay ... --tier deep --jobs 4
77 cases; 31 passed; 46 diagnostic; 0 expectation mismatches;
0 combined coverage gaps; 21 historical-only gaps; 71.62 seconds

$ replay ... --tier deep --jobs 1
77 cases; 31 passed; 46 diagnostic; 0 expectation mismatches;
0 combined coverage gaps; 21 historical-only gaps; 116.12 seconds
```

Serial and parallel schema-v3 reports are byte-identical:

```text
JSON     c35e36ed21cc4ff7843a1303ada3c89b7903e7cd513cff912a401af8733848e5
Markdown ab0341982950ab45daed29751e6aa96cdad8ea957bf1f05ed192596f3c2f2017
```

A fresh offline schema-v1 inventory was generated from the already hydrated
mirrors in 69.38 seconds. It contained 77 cases, 31 strict candidates, 46
diagnostics, and zero evidence gaps. Its SHA-256 is
`7e40b43694927497045ebd13e4f92e3a9edd4839e27a9965053ee7730ce34301`.
Safe comparison with the reviewed schema-v2 golden reported no added, removed,
changed, or snapshot-drifted cases.

The standalone rollback then verified all three persistent worktrees and their
indexes in 0.54 seconds without deleting or recreating any worktree.

## Independent line/branch coverage remediation slice

After a separately approved local dependency installation, the previously
unverified gate produced a real red result:

```text
$ pytest ... --cov=scripts.cherry_pick --cov-branch --cov-fail-under=90
308 passed
combined coverage: 80.05%
required coverage: 90%
```

Focused tests were added for CLI command/error paths, API transports and
pagination, configuration validation, existing-PR coverage, planner evidence
failures, replay-schema validation, local simulation, and writer safety. The
combined gate then passed at 90.21%, but an audit identified a second red gap:
pytest-cov's single threshold was combined and display-rounded, while the PRD
requires line and branch percentages to reach 90% independently.

The independent checker was written test-first. Its first run failed at
collection because `scripts.check_cherry_pick_coverage` did not exist. The
first implementation then exposed and fixed a runtime stdout-capture defect.
The workflow contract and ignored-artifact tests were also recorded red before
their workflow and `.gitignore` changes. The initial independent result was:

```text
line coverage:   92.4719% (pass)
branch coverage: 83.5616% (fail)
```

Additional Git and writer safety tests cover malformed identity and Git output,
corrupt-index rebuild, rollback failures, changeset proof boundaries,
non-conflict trial failures, missing/equal/unknown gitlink relationships,
writer preflight failures, merge mainline application, active-PR recovery, and
concurrent push races. The final independent result is:

```text
line coverage:   95.8479% (2470/2577)
branch coverage: 90.4110% (792/876)
combined:        94.47%
tests:           444 passed
```

## Final current local gates

```text
$ .venv/bin/python -m pytest -q scripts/tests --basetemp=/disk-backed/path \
    --cov=scripts.cherry_pick --cov-branch \
    --cov-report=term-missing --cov-report=json:coverage.json \
    --cov-fail-under=90
444 passed in 25.83s
combined coverage: 94.47%

$ .venv/bin/python scripts/check_cherry_pick_coverage.py coverage.json \
    --minimum-lines 90 --minimum-branches 90
line coverage: 95.8479%; branch coverage: 90.4110%; status: passed

$ ROCM_CHERRYPICK_REPLAY_DATA=... .venv/bin/python -m pytest -q \
    scripts/historical_replay_tests --basetemp=/disk-backed/path
3 passed in 114.23s

TheRock caller:        3 tests, OK
rocm-systems caller:   4 tests, OK
rocm-libraries caller: 4 tests, OK
```

Pinned Black 25.11.0, mdformat 0.7.21, actionlint 1.7.10, JSON parsing,
`git diff --check`, source-caller pin/permission tests, candidate/golden
comparison, and persistent-worktree rollback all pass locally. Black had to be
invoked once per file because its cached multi-file worker stalled in this
sandbox; the same pinned executable performed every check.

The earlier missing-tool record remains above as historical evidence for that
slice; it is superseded by the independently passing final coverage gate.

One attempted integration rerun was interrupted by the command wrapper and
left two persistent worktrees mid-operation. A mistakenly overlapping rerun
failed closed with 13 `worktree_rollback_failed` outcomes. The standalone
rollback command repaired all three worktrees in 1.0 second without mirror or
index resynchronization; the subsequent non-overlapping disk-backed run passed
all three tests as recorded above.

## 2026-08-16 Git-core and GitHub-control-plane remediation red run

The revised PRD and technical design were completed before the remediation
tests. The complete new contract was then added for schema v4, the offline core
manifest/CLI, dependency DAGs, authenticated label envelopes, GitHub timeline
and Check adapters, exact ref hydration, Action-only write authority,
Jira-free orchestration, draft rendering/writing, workflows, and local pipeline
simulation.

The first run was intentionally executed before any matching implementation:

```text
$ .venv/bin/python -m pytest -q \
    scripts/tests/cherry_pick_config_test.py \
    scripts/tests/cherry_pick_models_test.py \
    scripts/tests/cherry_pick_dependencies_test.py \
    scripts/tests/cherry_pick_authorization_test.py \
    scripts/tests/cherry_pick_core_test.py \
    scripts/tests/cherry_pick_core_cli_test.py \
    scripts/tests/cherry_pick_github_control_test.py \
    scripts/tests/cherry_pick_refs_test.py \
    scripts/tests/cherry_pick_action_runtime_test.py \
    scripts/tests/cherry_pick_orchestrator_test.py \
    scripts/tests/cherry_pick_writer_test.py \
    scripts/tests/cherry_pick_workflows_test.py \
    scripts/tests/cherry_pick_app_manifest_test.py \
    scripts/tests/cherry_pick_cli_test.py \
    scripts/tests/cherry_pick_simulation_test.py

exit 2: eight expected collection errors
- scripts.cherry_pick.dependencies missing
- scripts.cherry_pick.authorization missing
- scripts.cherry_pick.core missing
- scripts.cherry_pick.core_cli missing
- scripts.cherry_pick.refs missing
- scripts.cherry_pick.action_runtime missing
- dependent GitHub-control and orchestrator imports consequently missing
```

This is the red baseline for the implementation below. An early design also
proposed a Release Hub label-writing adapter and recorded the following red
experiment:

```text
$ ./node_modules/.bin/tsx --test \
    lambdas/query-proxy/src/cherryPickLabelPolicy.test.ts \
    lambdas/webhook-ingest/src/cherryPickLabelPolicySql.test.ts

exit 1: 0 passed, 3 failed
- ERR_MODULE_NOT_FOUND: cherryPickLabelPolicy.js
- unknown SQL catalog id: cherry_pick_labels.ensure_schema (two tests)
```

The first sandboxed invocation could not create tsx's local Unix IPC socket
(`EPERM`) and did not reach the tests. The recorded red result above is the
rerun with local IPC permission; it performed no network or remote write. The
active ROCm Release Hub policy was then reviewed and found to require a strictly
read-only service boundary. The proposed adapter, tests, SQL catalog, and all
Release Hub product changes were removed rather than implemented. The red run
is retained here only as chronological evidence; it is not an open product
requirement.

## 2026-08-16 control-plane completion and current green gates

Subsequent red/green slices covered:

- Action-only construction of the real GitHub transport;
- self-authenticating authorization artifacts and authorized plan
  fingerprints;
- exact write-time replan comparison and plan-scoped authority;
- strict single-plan and reconciliation artifacts;
- real but mechanically disabled draft, feedback, reconciliation, and label
  synchronization jobs;
- credential-free automation-revision validation before checkout, including
  manual-dispatch binding to the workflow commit;
- process-scoped Git credentials without token-bearing URLs/arguments;
- a shared validated planner/writer cherry-pick command contract;
- mandatory planned-tree evidence and exact conflict paths/stages;
- exact PR identity, draft state, and plan marker;
- same-name fork-head rejection;
- structured unknown-train errors and strict reconciliation repository maps;
- end-to-end propagation of an explicit disk-backed core scratch root; and
- production blocking of unattributed patch-equivalent no-op containment.

Representative final hardening red runs were one fork-head lookup failure,
three CLI boundary failures (malformed mappings, duplicate reconciliation
results, and an escaping unknown-train exception), and the independent coverage
gate:

```text
lines:    3233 / 3393 = 95.2844% (pass)
branches: 1061 / 1188 = 89.3098% (fail)
```

Focused tests exercised the existing fail-closed Action runtime,
authorization-envelope, feedback, and artifact-validation branches. No
threshold was lowered. The current complete result is:

```text
$ .venv/bin/python -m pytest -q scripts/tests \
    --basetemp=/home/jusharri/code/label-driven-cherrypick-automation/pytest-tmp/full-current \
    --cov=scripts.cherry_pick --cov-branch \
    --cov-report=json:coverage.json --cov-fail-under=90
621 passed

$ .venv/bin/python scripts/check_cherry_pick_coverage.py coverage.json \
    --minimum-lines 95 --minimum-branches 90 \
    --minimum-module-lines 90 --minimum-module-branches 90 \
    --critical-module <each named safety-critical module>
lines:    3294 / 3440 = 95.7558%
branches: 1111 / 1222 = 90.9165%
status: passed
```

The final scratch-root slice began with two focused failures: the Git evaluator
rejected `scratch_root`, and `CorePlanner` discarded it before invoking the
evaluator. After implementation, those tests passed; the broader
Git/core/core-CLI/simulator slice passed 82 tests, and the complete 621-test run
above includes both contracts. Disposable evaluation creates and uses the
requested disk directory, while reusable replay worktrees retain their tested
rollback path.

The final integration audit then exposed two previously untested mismatches:
the CLI rejected declared `edited`/`synchronize` events, and continuation could
recompute a fresh envelope from an old label without proving the label-time
snapshot. Tests were written first. The focused red run produced seven expected
failures (plus four already-green parameter cases): unsupported configuration,
both rejected events, missing trusted-Check reader, and missing planner
continuity behavior. The implementation added an exact numeric executor App ID,
same-head/name/App/external-ID Check filtering, continuation comparison, and
the two parser choices. The focused slice then passed 19 tests and the broader
configuration/CLI/client/orchestrator/feedback/workflow slice passed 168 tests.
The first complete run then correctly exposed three simulator fixtures that had
not declared an executor identity (603 passed, three failed). After making that
test trust input explicit, all 606 tests passed but the independent per-module
gate rejected `clients.py` at 89.4231% branch coverage. Malformed App-ID,
executor-ID, and Check external-ID contracts closed those missing negative
branches without lowering a threshold. The final 621-test measurement is the
passing result shown above.

A final integration-boundary slice was also driven red before implementation.
Two workflow tests proved the write-time replan token lacked the Checks-read
permission required to validate the trusted authorization snapshot. Seven
client tests proved incomplete/capped GitHub Search results, malformed totals,
and a mismatch between the Pull API's declared commit count and the returned
commit list were not yet blocked. A writer test proved the destination could
move during materialization without a second pre-push check. The implementation
added the narrow read permission, strict evidence-completeness validation, and
the post-materialization destination re-read. The combined focused integration
suite then passed 123 tests; the complete 621-test run and exact coverage gate
above include all of these contracts.

The comprehensive production-design documentation was also changed
tests-first. A focused infrastructure test first failed because the technical
design lacked the required readiness verdict, end-to-end architecture and flow
sections, threat model, implementation status, known gaps, TODOs, and Mermaid
views. The completed design adds eleven diagrams, including three sequences and
two state machines, while retaining an explicit `NOT READY` verdict until the
private-sandbox and draft-pilot gates pass. The focused documentation contract
then passed, followed by the complete 621-test run and unchanged exact coverage
result above.

Human rendering review then found that literal semicolons in sequence-message
text were interpreted by Mermaid as statement terminators, causing each next
`else` or `Note` to fail parsing. A regression assertion was added first and
failed against seven semicolons across the diagram set. All Mermaid text now
avoids literal semicolons, including the three reported sequence diagrams; the
focused regression passes and protects the whole diagram set from recurrence.

The current disk-backed replay gates also pass:

```text
fast, jobs=4:  17 rows, zero gaps
deep, jobs=4:  77 rows, zero oracle/combined-coverage gaps
deep, jobs=1:  77 rows, zero oracle/combined-coverage gaps
parallel/serial JSON and Markdown: byte-identical
rollback: all three persistent repository lanes restored without resync
```

No network or remote write was used for these completion gates.

## 2026-08-17 #10153 dry-run hardening: typed prerequisites and exact open-PR coverage

The rocm-systems #9716/#10153 dry run was treated as a new product-contract
slice. Product requirements and technical design were updated first. The first
test tranche then specified canonical PR/full-commit prerequisites, strict
schema-v5 reviewed overrides, standalone-commit proof, and exact existing-PR
coverage before production code existed:

```text
focused dependency/config/core/Git red run:
115 failed, 68 passed

after implementing only that contract:
183 passed
```

A second test-first tranche specified exact commit/candidate ref hydration,
complete open destination-PR discovery, candidate authorization binding,
writer snapshot drift, and the frozen #10153 evidence. Its first collection
failed because the new ref functions did not exist. Subsequent focused red
runs exposed one missing Action snapshot comparison and one missing local
simulator open-PR operation. After implementation:

```text
adapter/ref/writer/authorization integration slice: 138 passed
simulator plus Action-runtime slice:                 25 passed
```

The first complete partitioned run passed every test but correctly failed the
independent coverage policy. Partitioning was required only because the local
command harness terminates one process at roughly 30 seconds; all partitions
used the same disk-backed base temp and one append-only coverage dataset:

```text
core/config/dependencies/Git:               183 passed
adapter/authorization/refs/writer:          189 passed
CLI/control/configuration contracts:        116 passed
replay/release/infrastructure:              177 passed
initial total:                              665 passed

lines:    3684 / 3890 = 94.7044% (fail)
branches: 1251 / 1412 = 88.5977% (fail)
below per-module branch floor: config, core, orchestrator, refs
```

New negative-path tests were then written for malformed override shapes,
typed-node identity confusion, unrelated/ambiguous coverage, candidate
normalization, and commit/coverage ref failures. No production behavior or
threshold changed. The focused slice passed 170 tests, and the combined final
measurement is:

```text
688 tests collected and passed across the complete partitioned run
lines:    3722 / 3887 = 95.7551%
branches: 1283 / 1412 = 90.8640%
all 15 named safety-critical modules: at least 90% lines and branches
status: passed
```

The frozen real-object check used only the existing local rocm-systems clone
with `GIT_NO_LAZY_FETCH=1`. It did not query GitHub or another service:

```text
destination head:    800045c8ab865991f4cec1549de2bb44e76b9904
root status:         draft_planned / clean_trial_application
planned tree:        2b7467c293ea312349db32372bdc51a495fd419d
candidate head:      411a04e98648ef442751e8e219ab9fa1cfb228bf
candidate tree:      2b7467c293ea312349db32372bdc51a495fd419d
coverage:            covered_by_existing_pr / exact_existing_pull_coverage
source attribution:  true / complete_changeset_application_ancestor
```

The standalone commit, #8221 merge, and #9480 merge were ancestors of that
destination; the #9716 merge was not. The engine therefore correctly suppresses
a duplicate branch/PR because #10153 is exact coverage, while explicitly
leaving CI and semantic readiness to native checks and human review.

### 2026-08-18 literal core-CLI reproducibility correction

The earlier dry-run directory retained only schema-v1 request files. Running
one through the current CLI failed before Git evaluation:

```text
error: invalid core manifest: manifest contains unsupported field dependencies
exit: 2
```

A test was added first requiring a complete current-schema #10153 request. The
red run failed because the fixture did not exist (`1 failed, 4 passed`). After
adding `scripts/tests/fixtures/cherry_pick_10153_core_request.json`, the focused
fixture suite passed (`5 passed`). The literal offline CLI then consumed that
fixture against the local rocm-systems objects and returned:

```text
status:               covered_by_existing_pr
reason:               covered_by_existing_pr
prerequisites:        3a3fb320 -> #8221 -> #9480, all already_contained
coverage:             exact / exact_existing_pull_coverage
planned tree:         2b7467c293ea312349db32372bdc51a495fd419d
candidate tree:       2b7467c293ea312349db32372bdc51a495fd419d
source attribution:   complete_changeset_application_ancestor
```

No GitHub client, token, Jira integration, Release Hub query, or remote write
participates in this replay. Missing promised blobs in a partial clone remain a
fail-closed local-evidence condition and are not reported as a successful
cherry-pick.

The final historical gate also exercised interrupted-run recovery. The local
command harness forcibly terminated an initial fast replay before it could
write a report. Reusing that state without the documented explicit rollback
produced three rocm-libraries expectation mismatches. The standalone rollback
command restored and verified all three pinned lanes without fetch, reclone, or
index rebuild. The rerun then passed 17/17 fast rows. Fresh deep runs passed all
77 rows with both four jobs and one job, zero required coverage gaps, and
byte-identical reports:

```text
JSON:     c35e36ed21cc4ff7843a1303ada3c89b7903e7cd513cff912a401af8733848e5
Markdown: ab0341982950ab45daed29751e6aa96cdad8ea957bf1f05ed192596f3c2f2017
```

A final rollback again verified the three pinned lane heads. This confirms the
runbook requirement: after any interrupted replay, invoke explicit rollback
before trusting a subsequent run.

### 2026-08-19 fresh-checkout local materialization

The local checkout slice began with three failing core-CLI tests because no
`materialize` subcommand existed. After the offline materializer was added,
the focused core slice passed. Three more high-level tests were then written
before implementation for the single `local-materialize` controller command,
its local-gh-only boundary, and non-writable operator authorization.

A final integration audit found that `--scratch-root` was accepted but not
forwarded and that the fail-fast `CREATE_DRAFT` confirmation check had become
unreachable. The focused red run showed all four relevant tests failing. After
implementation, those four passed, followed by a 103-test CLI, orchestrator,
core-CLI, and repository-infrastructure slice.

The first complete measurement passed all 720 functional tests, but the exact
per-module checker rejected the new `core_cli.py` surface:

```text
core_cli lines:    87.0690% (fail)
core_cli branches: 80.9524% (fail)
overall exact gate status: failed
```

Negative-path tests were then added for relative/existing/unparented output
paths, missing source repositories, every malformed evidence condition, Git
setup failure, cherry-pick failure, tree/head read failure, and final-tree
mismatch. The focused module run passed 18 tests at 99% displayed coverage.
The final complete result is:

```text
732 tests passed
lines:    3901 / 4084 = 95.5191%
branches: 1364 / 1500 = 90.9333%
core_cli lines:    115 / 116 = 99.1379%
core_cli branches: 41 / 42 = 97.6190%
all named safety-critical modules: at least 90% lines and branches
status: passed
```

All pytest temporary directories and coverage artifacts used explicit
workspace-backed disk paths. No test or validation step pushed, labeled,
commented, dispatched a workflow, or created a remote pull request.

A final CLI-surface test was then written to require that `local-materialize`
reject the remote `--publish-status` option. It failed first because the generic
parser exposed that flag. The flag was removed from the local-only subcommand;
all three focused local-materialization CLI tests passed, and the complete
result increased to the 732-test measurement above.

The final operator-level validation used a fresh local source repository. Its
first run deliberately exposed an invalid replay-mirror origin and failed
closed with `blocked_evidence / ref_fetch_failed`, creating no output. After
setting that disposable clone's origin to the normal read-only ROCm GitHub
repository, the identical top-level CLI command returned:

```text
status:             local_materialized
reason:             local_checkout_created
destination head:   b6cf6ab7abab454a7c4a7e7d37cda7c99736ef3e
source merge:       6691fe3e61967465422ed5b974e494f5520dbfe6
result tree:        8621c291ae78d9affc518b57bbb0498a60facba9
planned tree:       8621c291ae78d9affc518b57bbb0498a60facba9
command:            git -c core.hooksPath=/dev/null cherry-pick -x 6691fe3e61967465422ed5b974e494f5520dbfe6
```

The checkout is local, its push URL is disabled by construction, and the CLI
reported `ci_checks=not_evaluated` plus
`semantic_readiness=human_review_required`. No remote branch or pull request
was created.

### 2026-08-19 operator user manual

A documentation-contract test was added first for a standalone user manual. It
required the direct-Git decision boundary, fresh-checkout command,
`planned_tree` verification, disabled push URL, assurance limits, dependency
and conflict guidance, and README discoverability. The red run failed because
`docs/cherry-pick-automation/user-manual.md` did not exist. After writing and
linking the manual, the focused test passed. The complete suite then passed 732
tests, and the independent exact gate remained at 95.5191% lines and 90.9333%
branches. Formatting and `git diff --check` passed. No remote action occurred.

### 2026-08-19 SLAI bundle, Release Hub adapter, and Developer Central token handoff

The PRD and technical design were extended before this implementation slice.
The first focused rockrel run then failed during collection because
`release_hub_auth`, `release_hub`, `marketplace_cli`, and the skill builder did
not exist. Tests specified token storage and redaction, bounded read-only HTTP,
exact train snapshots, a three-command local CLI, deterministic allowlist
packaging, and the absence of remote writer code before implementation.

The Developer Central tests were also written before the API-token UI and
capability response. One later red test made the backend-default boundary
explicit: a mocked capability default of 60 rendered 30. After the component
copied the server-owned default into state, the focused browser suite passed
4/4.

The first complete rockrel run after implementation passed 815 tests and failed
one stale infrastructure assertion that prohibited any Release Hub reference,
including an adapter outside the pure core. The assertion was corrected to
enforce the actual boundary: no Release Hub import in the core and no mutation
surface in the adapter. The next complete run passed 821 tests. The exact
coverage gate still rejected orchestrator branch coverage at 89.6226%; new
negative snapshot-contract tests raised it to 91.5094% without lowering a
threshold. Final measurement:

```text
821 tests passed
lines:    4490 / 4684 = 95.8582%
branches: 1543 / 1682 = 91.7360%
all named safety-critical modules: at least 90% lines and branches
status: passed
```

The generated bundle was regenerated into a disk-backed directory and compared
byte-for-byte with `skills/rocm-cherry-pick`. Its standalone help exposes only
`auth`, `plan`, and `materialize`; the missing-auth smoke exits 2 with the exact
Developer Central setup URL and performs no network or filesystem mutation.
Black checked all 63 Python files without a change.

The final complete run caught one additional packaging defect after an earlier
standalone smoke: importing the checked-in bundle had written 17 `.pyc` files
under `scripts/cherry_pick/__pycache__`, so the byte comparison failed with 820
tests passing and one failing. Before changing the launcher, a new focused
assertion reproduced the cache directory (`1 failed`). Setting
`sys.dont_write_bytecode` before importing the vendored runtime, then rebuilding
from the allowlist, removed the generated directory and prevented recurrence.
The packaging suite passed 8/8, followed by the final 821/821 complete run and
the exact coverage result above.

The unchanged disk-backed historical corpus was then replayed without fetch or
resync: fast passed 17/17; deep parallel passed 77/77; deep serial passed 77/77;
and parallel/serial JSON plus Markdown were byte-identical. A final rollback
verified all three pinned lane heads. Canonical hashes remained:

```text
JSON:     c35e36ed21cc4ff7843a1303ada3c89b7903e7cd513cff912a401af8733848e5
Markdown: ab0341982950ab45daed29751e6aa96cdad8ea957bf1f05ed192596f3c2f2017
```

Focused Release Hub checks passed for the token backend, generated client,
browser, Settings navigation, server auth, OpenAPI, feature flags, language,
and TypeScript. The local rootless production-image rebuild succeeded. Both
health endpoints returned OK; `/settings/api-tokens` returned HTML with the new
hashed JavaScript and CSS; and the same-origin list endpoint returned
`feature_disabled`, `createEnabled=false`, no allowed scopes, and zero tokens.

The repository-wide `npm run verify` is not green. A focused rerun proves five
failures belong to a separate pre-existing Release Watch strategy/navigation
diff: its implementation intentionally changed Claim copy and removed drafted
message bodies from pre-Chase review while the old navigation tests still
expect them. That file reports 21 passed and 5 failed. The focused server-auth
suite passes 2/2. In the canonical gate, all 296 script tests, all 111 primary
frontend tests, and all 863 query-proxy/shared/webhook tests passed before npm
returned failure for that frontend-simple workspace. This project neither
reverted the Release Watch change nor weakened its tests.

Normal SLAI validation was attempted without an author bypass or scan bypass.
It stopped because this workstation has no
`/tool/sysadmin/scripts/query_ad` for AMD NTID verification and no configured
`AMD_LLM_API_KEY` for the mandatory security scan. The submission dry-run
stopped on the same prerequisites. No compliance result, package upload, or
Marketplace write occurred.

### 2026-08-21 final fail-closed hardening and completion gates

Tests were added before implementation for the remaining failure surfaces:
exact configuration-snapshot digest binding, snapshot path and file safety,
managed-frontier root identity and drift, malformed stack metadata, OIDC URL,
audience, query, and token rejection, full OIDC-to-config composition, control
plane subprocess execution, local-create authentication failure, and writer
source-kind validation. The first focused and full runs failed on the missing
or incomplete behaviors. Production code was then changed only until those
tests and the existing suite passed. The first full integration run also
exposed three regressions in the new tests and adapters; each was reproduced in
isolation and corrected before the final broad run.

The independent coverage gate was then run as a separate exact-count decision.
It initially rejected uncovered failure branches. Additional failure-path tests
were written first; no threshold was reduced and no production exception was
added. The final post-format command was:

```console
$ .venv/bin/python -m pytest -q scripts/tests \
    --basetemp=/home/jusharri/code/label-driven-cherrypick-automation/rockrel/.tmp/pytest/full-rockrel-final \
    --cov=scripts.cherry_pick --cov=scripts.build_cherry_pick_skill \
    --cov-branch --cov-report=term-missing \
    --cov-report=json:.tmp/coverage/coverage-final.json \
    --cov-fail-under=90
920 passed in 13.91s
```

The workflow-equivalent exact checker then passed:

```text
lines:    4942 / 5154 = 95.8867%
branches: 1719 / 1872 = 91.8269%
critical modules: 23 / 23 above 90% lines and branches
status: passed
```

The generated SLAI bundle was rebuilt from rockrel revision
`8432a05b8c081df871d426525728de39569ff3cb`. Its package suite passed 24
tests, and the local structural validator reported `Skill is valid!`. Hosted
author/NTID validation, hosted security scanning, and Marketplace submission
remain queued and were not bypassed.

The final Release Hub command was the repository hard gate, not a focused
substitute:

```console
$ npm run verify
exit 0
```

It passed 328 script tests, 111 primary-frontend tests, 251 Developer Central
tests, 84 CLI tests, 891 query-proxy/shared/webhook tests, and 9 Python tests,
as well as language, test-policy, feature-flag, thin-client, skill, OpenAPI, SQL,
production build, lint, and typecheck gates. The earlier five Release Watch
failures remain recorded above as an intermediate red state; the current
worktree has since reconciled that separate change and the complete gate is
green.

A rootless `docker compose up -d --build api frontend-simple` completed with
exit zero and reported no npm vulnerabilities. API and Developer Central
health/readiness passed, both containers were healthy, the API-token settings
route served the newly built `index-BvMfU1X3.js` asset, the unauthenticated
configuration read returned the expected 401 `invalid_token` contract, and
startup logs contained no fatal error.

Finally, disk-backed replay remained unchanged by the hardening: fast parallel
passed 17/17, deep parallel and deep serial passed 77/77, both deep reports were
byte-identical, and explicit rollback restored all three pinned lane heads.
No fetch, push, label, Check, comment, workflow dispatch, App change, draft PR,
Marketplace submission, or production deployment occurred.

### 2026-08-21 stale compliance-result invalidation

The final package inventory exposed a dated `PASSED` scan report from August 20
inside a bundle whose runtime and manifest had been rebuilt on August 21.
Because that report did not bind the changed bundle, carrying it forward would
have been a false compliance claim.

All required behavior was specified in tests before builder code changed. The
new tests require fresh-output and in-place builds to remove scanner-owned
frontmatter and files, exclude those files from the manifest, reject a
validation-output directory, and perform no partial write on that rejection.
The pre-implementation run recorded:

```text
3 failed, 8 passed
```

After the two-phase preflight and stripping implementation, the three new tests
passed. The checked bundle was then rebuilt locally; this intentionally removed
`metadata.compliance_scan`, `COMPLIANCE_SCAN_WAIVERS.yaml`,
`COMPLIANCE_FINDINGS.json`, and `COMPLIANCE_SCAN.md` before rehashing. The
complete builder suite and local structural validator passed:

```text
11 passed
Skill is valid!
```

The final broad post-fix gate passed without lowering coverage:

```text
922 tests passed
lines:    4970 / 5183 = 95.8904%
branches: 1735 / 1888 = 91.8962%
critical modules: 23 / 23 above 90% lines and branches
status: passed
```

The checked bundle is now explicitly pre-scan. A fresh hosted author/NTID
validation and security scan must run against this exact rebuilt bundle; any
subsequent rebuild strips and invalidates those outputs again. No scan bypass,
submission, upload, or remote GitHub action occurred.

### 2026-08-21 final readability, integration, and qualification slice

The final review slice again followed red-contract, implementation, focused
green, then aggregate-green order. Its recorded red states were:

- the initial structure/quality contract reported seven intended failures for
  oversized control flow, missing documentation contracts, package closure,
  integration, rendering, and coverage requirements;
- after the substantive-docstring rule was strengthened, 55 generic
  production docstrings failed before they were rewritten;
- the five-repository integration contract began with 20 intended failures
  before the manifest and checker existed;
- four Mermaid accessibility/link contracts failed before unique
  `accTitle`/`accDescr` metadata and source/bundle link checks were enforced;
- the private-sandbox security suite began at 23 failures and one pass, then a
  remaining boolean repository-ID alias failed with 24 passes before the final
  fail-closed parser correction;
- focused Release Hub tests first rejected the wrong OIDC audience, an
  abbreviated or inconsistent reusable-workflow identity, missing OIDC failure
  cases, and absent TypeScript coverage enforcement; and
- package-closure tests exposed an unused bundled coverage module before the
  allowlist and generated asset were corrected.

Characterization tests were green before the three oversized control-flow
routines were split. The resulting CLI, planner, managed-stack builder, and
writer routines satisfy the PRD's AST-enforced size and decision limits. The
strengthened docstring gate now accepts every production Python module, class,
function, and method, including private helpers, and Release Hub's exported
cherry-pick APIs satisfy the equivalent TSDoc contract. Targeted invariant
comments remain limited to security, race, transaction, or recovery rationale.

The bundle builder now packages the transitive local import closure of the
Marketplace entrypoint and rejects closure drift. The generated skill passes
its focused package/quality checks, byte comparison, missing-auth forward
smoke, and canonical structural validation. It remains an explicitly pre-scan
artifact: hosted author/NTID validation, mandatory security scanning, and
Marketplace submission have not run.

The five-repository checker now passes its 25 deterministic tests and returns
`valid: true` against the local rockrel, Release Hub, TheRock, rocm-systems,
and rocm-libraries worktrees. That is deliberately a structural oracle: it
proves configured cross-file equality for the endpoint, exact OIDC issuer and
audience, reusable-workflow path, full-SHA pin, and caller set. It does not
prove token exchange, verifier, authorization, or workflow semantics. Release
Hub OIDC/configuration tests and rockrel workflow/adapter tests supply those
behavioral oracles.

The exact OIDC audience is now
`api://developer-central.amd.com/rocm-cherry-pick-config`.
`job_workflow_ref` must end in the reviewed lowercase 40-character rockrel
SHA, and `job_workflow_sha` must equal that SHA. The final Release Hub
`npm run verify` exits zero: scripts 348/348, primary frontend 111/111,
Developer Central 259/259, query-proxy/shared/webhook 971/971, and Python 9/9.
OIDC verification measures 100% line/function and 98.77% branch coverage;
backend configuration/capability modules measure 100%/100%/98%; and the
API-token UI measures 100%/100%/91.3%. Both build-only Docker images complete
locally. They were not started with Compose, pushed, or deployed.

All fourteen Mermaid diagrams have unique non-empty accessibility metadata,
all checked source and packaged-skill links resolve, and the pinned-digest
renderer produces SVG for every diagram with networking disabled, a read-only
filesystem, dropped capabilities, and no-new-privileges.

The private-sandbox security suite finishes 25/25. It requires an exact
allowlisted private repository name and numeric ID, rejects production IDs,
requires the literal `PRIVATE` visibility proof and sentinel, constrains
branches to `sandbox/cherry-pick/`, and checks all gates before invoking an
injected executor. The harness is prepared only. No private GitHub sandbox,
installation-token exchange, branch creation, draft creation/recovery,
duplicate delivery, or branch-protection exercise has run remotely.

The superseded pre-compatibility aggregate snapshot on Python 3.12.13
reported:

```text
979 passed in 13.56s
lines:    5323 / 5538 = 96.1177%
branches: 1786 / 1944 = 91.8724%
critical modules: 24 / 24 above 90% lines and branches
status: passed
```

At this historical checkpoint only Python 3.12 had executed locally. The later
compatibility and full-matrix evidence below supersedes that limitation; the
979/coverage/24-module figures above remain only a chronological snapshot. No
public PR, branch, label, Check, comment, workflow dispatch, App/secret/ruleset
change, image push, deployment, SLAI submission, or other remote write occurred
during this slice.


### 2026-08-21 Python compatibility and final human-review hardening

Python 3.10 first failed during collection with 36 errors because
`enum.StrEnum` exists only on Python 3.11 and newer. Tests were added for exact
string values, formatting, JSON serialization, lookup, iteration, and `auto()`.
The compatibility module then used the native class on 3.11+ and a
standard-library `str`/`Enum` shim on 3.10; the affected suite turned green.
An earlier Python 3.10.20 full pre-bundle snapshot passed 999 tests with one
bundle-equality deselection and measured 5,422/5,650 lines (95.9646%),
1,808/1,968 branches (91.8699%), and 25/25 critical modules. Python 3.11.15 and
3.12.13 had clean 999-test runs before the compatibility edit and then passed
the 169 affected tests afterward.

After the coordinated bundle rebuild, the final full matrix reports:

```text
Python 3.10.20: 1036 passed in 18.12 s
Python 3.11.15: 1036 passed in 17.79 s
Python 3.12.13: 1036 passed in 17.47 s
lines:          5470 / 5703 = 95.9144% on every interpreter
branches:       1818 / 1982 = 91.7255% on every interpreter
critical:       25 / 25 at or above 90% lines and branches
```

There are no deselections. Checked-in bundle equality is included in all three
1,036-test results.

A standalone-commit write-time regression now binds the canonical digest of an
empty open-PR coverage snapshot. Unrelated open pull requests do not alter that
snapshot; injecting any non-canonical value returns
`coverage_snapshot_moved_during_write` before a branch or pull-request write.

Credential-bearing GitHub and Release Hub transports now use explicit
no-redirect handlers and read at most 2 MiB plus one detection byte. Focused
tests cover bounded success payloads, oversized success and HTTP-error bodies,
cross-origin redirects, timeouts, malformed JSON, sanitized failures, and the
GET-only Marketplace GitHub adapter.

Bundle provenance is now `rocm-cherry-pick-bundle.v2`.
`source_provenance` records the exact `base_revision`, `clean_commit` or
`dirty_worktree_review`, and `source_content_sha256` over the exact packaged
source closure; generated files retain their individual hashes. A dirty build
fails by default and requires `--allow-dirty-review`, whose result is explicitly
nonpublishable. Scanner-owned output is stripped before hashes are regenerated.
The rebuilt review bundle records base
`8432a05b8c081df871d426525728de39569ff3cb` and source digest
`ce9fcd327f311357359bf8f86db88971caf733a042a90e95682ef38b56c1159b`.
Bundle equality, AST closure, and Marketplace qualification pass 37/37, and
`quick_validate` reports `Skill is valid!`. It remains deliberately
nonpublishable `dirty_worktree_review`; a new build from the clean reviewed
commit, hosted author/NTID validation, security scan, and submission remain
pending. The Marketplace package remains read-only/local-only (`auth`, `plan`,
and `materialize`); no mutation runtime is in its closure.

Integration checker v2 began red with 55 passing and six failing tests. It then
passed 61/61 at 96.8468% line and 90% branch coverage, and its canonical local
report returns `valid: true`. It checks exact positive numeric owner/repository
IDs, immutable-only subjects, events, refs, workflow kind/ref/SHA, audience,
endpoint, and caller pins across five repositories. Its canonical
`c53e703568fe41129abf7139f018ac920bca9c59` pin is intentionally stale relative
to the dirty local rockrel HEAD; the checker is structural and does not replace
runtime OIDC/verifier tests.

The OIDC PR/direct-event slice started with all five new contract cases red and
finished 41/41 green at 100% line/function/branch coverage. The policy pins
owner `21157610` and repository IDs TheRock `765605091`, rocm-systems
`962090208`, rocm-libraries `971570345`, and rockrel `1071689640`. Reusable
`pull_request_target` callers bind exact `base_ref`, `ref`, event,
`job_workflow_ref`, and `job_workflow_sha`; direct rockrel callers bind exact
event, `workflow_ref`, and `workflow_sha`. Only immutable ID-qualified subjects
are accepted; legacy name-only subjects have no fallback. GitHub's official
OIDC guidance says repositories created before 2026-07-15 require authorized
opt-in to immutable subjects, so remote verification/enablement remains an
unchecked production TODO and was not performed.

The final Release Hub hard gate is green: scripts 348/348, primary frontend
111/111, Developer Central 259/259, OpenAPI 90 operations/292 schemas, SQL
546/546, production builds, and Python 9/9. OIDC is 41/41 at 100/100/100;
seven backend configuration tests measure 100% lines/functions and 98%
branches, and eleven API-token UI tests measure 100% lines/functions and 91.3%
branches. The compact query-proxy/shared/webhook rerun records 971 success
markers and exit zero. The private-sandbox security contract passes 26/26 after
its production denylist was changed from placeholders to TheRock `765605091`,
rocm-systems `962090208`, rocm-libraries `971570345`, and rockrel `1071689640`.
No private-sandbox adapter/run, hosted CI, SLAI hosted validation, human review,
App/secret/label/ruleset provisioning, caller deployment, or remote mutation
occurred.
