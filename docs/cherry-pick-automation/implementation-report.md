# Draft — local review required

# Label-driven cherry-pick automation: implementation audit

## Current verdict

The implementation is a local-review candidate, not a production-ready or
error-free service. Its offline Git engine, GitHub control-plane adapters,
draft-only writer, disabled workflows, three thin callers, read-only Release
Hub train adapter, and self-contained local SLAI bundle are implemented and
covered by deterministic local tests. No public workflow, GitHub App,
installation token, label event, branch push, draft PR, Check, comment, SLAI
submission, or production deployment has been exercised.

Remote mutation remains mechanically disabled in the review copy:

- the committed train is `validate`;
- every workflow job that could mutate GitHub has the literal predicate
  `github.repository == 'LOCAL_REVIEW_REMOTE_WRITES_DISABLED'`;
- the App manifest webhook is inactive;
- the App authority and Actions transport remain unavailable outside GitHub
  Actions; the separate local `gh` path requires `create-draft` mode, an exact
  reviewed plan artifact, exact replanning, and literal operator confirmation;
  and
- all remote follow-up work is queued in `REMOTE_ACTIONS_TODO.md`.

Release Hub is explicitly outside the cherry-pick write architecture. The
packaged skill uses its versioned API only to resolve one exact configured
train and destination branch, then passes an immutable snapshot to the Git
engine. A separate local Developer Central change adds a safe-default-off
`developer-central.api-tokens` browser surface and additive token-capability
contract. It adds no cherry-pick label writer, queue, GitHub App permission, or
deployment, and the feature flag remains dark.

## Measured local evidence

The final coordinated post-rebuild matrix is green on every configured local
interpreter:

```text
Python 3.10.20: 1036 passed in 18.12 s
Python 3.11.15: 1036 passed in 17.79 s
Python 3.12.13: 1036 passed in 17.47 s
lines:          5470 / 5703 = 95.9144% on every interpreter
branches:       1818 / 1982 = 91.7255% on every interpreter
critical:       25 / 25 at or above 90% lines and branches
```

There are no deselections. The tested coverage checker enforces the thresholds
without display rounding. The 25th critical path is the Marketplace GitHub
read-only transport. Python 3.10 originally produced 36 collection errors
because `enum.StrEnum` is a Python 3.11 addition; the standard-library-only
compatibility shim preserves string, JSON, `auto()`, and Enum behavior on 3.10
while using the native class on newer interpreters.

All repository-native thin-caller tests pass: three in TheRock, four in
rocm-systems, and four in rocm-libraries. Their currently rendered
rockrel SHA is intentionally treated as stale test input; it must not be
published or activated until a reviewed rockrel commit exists and the callers
are regenerated from that exact SHA.

Integration checker v2 began with 55 passing and six failing tests, then
passed 61/61 with 96.8468% line and 90% branch coverage. The canonical local
five-repository report returns `valid: true`. It proves equality for the exact
endpoint, OIDC trust anchors, positive numeric owner/repository IDs,
immutable-only subjects, per-caller event/ref/workflow tuple, and full-SHA pin.
It does not prove token exchange, verifier, authorization, or workflow behavior;
Release Hub and rockrel behavioral suites own those assertions. The canonical
`c53e703568fe41129abf7139f018ac920bca9c59` pin is intentionally stale relative
to the dirty local rockrel HEAD and is not an activation candidate.

The Marketplace package is a read-only/local-only split: its public CLI
exposes only `auth`, `plan`, and `materialize`; it contains no writer, feedback
publisher, Action runtime, or draft-creation command. Bundle manifest v2 records
`source_provenance.base_revision`, `clean_commit` or
`dirty_worktree_review`, and the exact source-closure digest
`source_content_sha256`, plus hashes of generated files. Dirty inputs require
`--allow-dirty-review` and produce a nonpublishable review bundle. The rebuilt
bundle records base `8432a05b8c081df871d426525728de39569ff3cb` and source digest
`ce9fcd327f311357359bf8f86db88971caf733a042a90e95682ef38b56c1159b`.
Bundle equality, AST closure, and Marketplace qualification pass 37/37, and
`quick_validate` reports `Skill is valid!`. A clean reviewed-commit rebuild is
still required before publication and hosted validation.

The complete Release Hub `npm run verify` gate is green: scripts 348/348,
primary frontend 111/111, Developer Central 259/259, a compact
query-proxy/shared/webhook rerun with 971 success markers and exit zero, OpenAPI
90 operations/292 schemas, SQL 546/546, production builds, and Python 9/9. The OIDC event/identity
suite passes 41/41 with 100% line/function/branch coverage; seven backend
configuration tests reach 100% line/function and 98% branch coverage; and eleven
API-token UI tests reach 100% line/function and 91.3% branch coverage. The private-sandbox security contract separately passes 26/26 after its
production denylist was corrected to the real TheRock, rocm-systems,
rocm-libraries, and rockrel numeric IDs. No deployment, Compose startup, image
push, production-parity sandbox adapter, or private-sandbox execution occurred.

All fourteen Mermaid diagrams have unique non-empty accessibility metadata,
valid local links, and successfully rendered SVG output from the pinned-digest,
network-disabled container.

The disk-backed historical suite was rerun with the current engine:

| Gate            | Result                                                             |
| --------------- | ------------------------------------------------------------------ |
| Fast, four jobs | 17/17 reviewed rows; zero gaps                                     |
| Deep, four jobs | 77/77 reviewed rows; zero oracle or combined-coverage gaps         |
| Deep, one job   | 77/77 reviewed rows; zero oracle or combined-coverage gaps         |
| Determinism     | Parallel and serial JSON/Markdown are byte-identical               |
| Rollback        | All three warm repository lanes restored without reclone or resync |

The canonical report hashes are:

```text
JSON:     c35e36ed21cc4ff7843a1303ada3c89b7903e7cd513cff912a401af8733848e5
Markdown: ab0341982950ab45daed29751e6aa96cdad8ea957bf1f05ed192596f3c2f2017
```

This completion run also confirmed the documented interrupted-run procedure:
after the command harness forcibly terminated one replay, reuse before explicit
rollback exposed three lane-state mismatches. The rollback command restored and
verified all pinned heads without refetch/reclone; fast and both deep runs then
passed. Operators must run rollback after any interruption before trusting the
next report.

The 77 rows are not presented as 77 successful cherry-picks. They comprise 31
strict exact tree replays with positive post-merge containment, three expected
conflicts, five adaptation/evidence diagnostics, and 38 inventory-only rows.
Only the 31 strict rows are historical pass cases for the application engine.

The #9716/#10153 dry run is now a separate frozen regression. Against the
already-hydrated rocm-systems objects, with lazy fetch disabled, the engine
proved the reviewed prerequisite order `3a3fb3206000a3b47e953fd6613571ae6ca0edb4`
then #8221 then #9480, planned #9716 at tree
`2b7467c293ea312349db32372bdc51a495fd419d`, and proved open PR #10153 head
`411a04e98648ef442751e8e219ab9fa1cfb228bf` has exact #9716 attribution and
that same tree. Its final decision is `covered_by_existing_pr`; it would create
neither an automation branch nor a duplicate PR. This proves Git structure,
not CI or semantic readiness: the result explicitly records
`ci_checks=not_evaluated` and `semantic_readiness=human_review_required`.

The regression now retains a complete schema-v3 `CoreRequest` at
`scripts/tests/fixtures/cherry_pick_10153_core_request.json`. This corrects the
earlier state in which only obsolete schema-v1 dry-run requests and a summary
fixture were available. The current artifact is accepted verbatim by
`scripts.cherry_pick.core_cli` and has been replayed against the local real Git
objects with the expected exact-coverage decision.

## Implemented behavior

| Area                     | Local implementation and evidence                                                                                                                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Product scope            | A train is configuration mapping each repository to one destination branch; no Express-Train-specific branch convention is encoded                                                                                |
| Mainline-first           | Source PR must be merged to that repository's configured source branch (`main` for TheRock, `develop` for current component entries)                                                                              |
| Core boundary            | `CoreRequest` v3 and `core_cli` are deterministic, Git-only, offline, token-free, Jira-free, Release-Hub-free, and LLM-free                                                                                       |
| Local materialization    | One `local-materialize` command resolves read-only PR evidence, invokes the same Git core, creates an independent exact-destination checkout, verifies `planned_tree`, disables pushing, and emits exact commands |
| Changeset proof          | Squash/single, two-parent merge, ordered rebase, and one-parent standalone prerequisite commits are proven from immutable Git objects                                                                             |
| Containment              | Exact ancestry or strongly attributed, tree-verified destination application can auto-contain; unattributed patch-equivalent no-op blocks for semantic review                                                     |
| Prerequisites            | Canonical PR/full-commit trailers plus reviewed train overrides produce a bounded DAG; commit leaves, missing nodes, cycles, and ambiguity fail closed                                                            |
| Existing-PR coverage     | Every bounded open same-repository destination PR is evaluated from Git; exactly one attributed planned tree returns `covered_by_existing_pr`, while ambiguous coverage blocks                                    |
| Authorization            | Latest canonical label transition must belong to a current write-capable human or exact allowlisted numeric labeler App; envelope binds source/body/graph/config                                                  |
| Snapshot continuity      | Later events/reconciliation require the exact envelope external ID on the same head from the configured numeric executor App; stale/missing/foreign snapshots require relabeling                                  |
| Plan identity            | Authorized plan fingerprint binds the immutable core-request digest, typed graph, and open-PR candidate snapshot to the self-authenticating authorization envelope                                                |
| Ref hydration            | Explicit pull head, merge, ordered commits, prerequisite commits, coverage heads, and destination refs are hydrated with prompts, hooks, and lazy fetch disabled                                                  |
| Evidence completeness    | Incomplete/capped GitHub Search results, malformed totals, and Pull/commit-list count mismatches block instead of authorizing from partial evidence                                                               |
| Planning/write isolation | Read plan is an immutable artifact; Action and local-gh writers replan and compare identity, authorization, destination, tree, and fingerprints before minting path-specific plan authority                       |
| Git application          | Planner, writer, and PR renderer share one validated command builder; writer materializes each commit with `-x`, requires the planned tree, and records the exact commands in the draft body                      |
| Draft identity           | Deterministic branch plus exact source/repository/train/fingerprint marker; same-name fork heads, ready PRs, malformed URLs, and marker mismatch block                                                            |
| Transaction safety       | Destination and open-PR snapshot are re-read after materialization; existing refs are never updated/deleted; absent ref is compare-and-created; exact concurrent work is reused                                   |
| Partial recovery         | Branch-created/PR-failed returns `retryable_partial_write`; reconciliation may create only the missing exact draft                                                                                                |
| Conflict handling        | Full application stops with `blocked_conflict`, sorted paths/stages, no push, and no automatic resolution                                                                                                         |
| Feedback                 | Typed total status mapping drives an authorization-bound Check when available and one sticky status comment                                                                                                       |
| Reconciliation           | Scheduled/manual read planning is separate from disabled per-result write revalidation and disabled feedback publishing                                                                                           |
| Credentials              | Actions uses scoped tokens and process-local HTTP auth; local operation resolves REST access through `gh auth token` and Git access through `gh auth git-credential`, without recording tokens                    |
| Draft policy             | Writer can create only `draft: true`; it has no ready, approve, merge, auto-merge, close, force-push, delete, or existing-ref-update operation                                                                    |
| Workflow supply chain    | Credential-free preflight validates immutable revisions and binds manual dispatch to its workflow commit; caller ref/config equality is tested; Actions are full-SHA pinned                                       |
| SLAI local distribution  | Reproducible allowlist bundle, source/hash manifest, no third-party Python dependency, and only `auth`, `plan`, and `materialize`; no remote writer is packaged                                                   |
| Train discovery          | Read-only Release Hub API adapter resolves an exact train/configuration snapshot; current branch objects and every containment/application decision remain pure Git                                               |
| Developer Central token  | Safe-default-off self-service preset requests only `read:evidence`, uses server-owned expiry capabilities, shows the secret once, and requires explicit owner-bound revoke                                        |

## TDD record

The PRD and technical design preceded the schema-v4/core/control-plane slice;
the #10153 hardening then began with new failing schema-v5, typed-prerequisite,
open-PR coverage, final snapshot, and frozen-regression tests before production
code changed.
The initial implementation run failed during collection for eight deliberately
missing modules. Later behavior slices were also recorded red before their
implementation, including independent coverage enforcement, Action-only
transport and capability boundaries, plan-bound revalidation, strict artifacts,
real disabled workflow jobs, Git credential isolation, exact PR identity, and
same-repository patch-equivalence review.

The local-gh slice also started red: the first focused run failed collection
because `local_runtime` and `gh_git_environment` did not exist. After the first
implementation, two remaining focused failures drove final execution-context
evidence and the project-local skill. A later red infrastructure assertion
added the new runtime to the critical per-module coverage gate. The final
write-authority module measures 100% line and branch coverage; the local
runtime measures 97.7778% lines and 95% branches.

The most recent hardening used focused red tests for a same-name fork PR,
unknown train handling, duplicate/malformed reconciliation artifacts, and
duplicate/empty/extra repository mappings. Coverage-only tests then exercised
existing fail-closed branches; the gate moved from 95.2844% lines / 89.3098%
branches (failing) to the measured passing result above without lowering a
threshold.

The SLAI/Release Hub slice also began red: tests failed collection while the
auth, adapter, Marketplace CLI, and bundle builder did not exist; the browser
expiry test initially received 30 when the mocked server required 60; the first
complete rockrel run exposed one stale infrastructure assertion; and the exact
coverage gate rejected orchestrator branch coverage at 89.6226%. Production
code and tests were completed without lowering a threshold. Later readability,
integration, rendering, sandbox-contract, Python-compatibility, transport,
provenance, and immutable-OIDC slices added more tests. The final post-rebuild matrix passes all 1,036 tests on all three interpreters
with no deselections; all 25 named critical modules meet the independent gate.

Detailed command/output history is retained in `tdd-evidence.md`.

## Remaining blockers before any public activation

- Human review of the complete rockrel and three caller diffs is incomplete.
- Caller pins refer to an older reviewed SHA and cannot represent this local
  implementation until rockrel is reviewed and committed.
- `trusted_app_ids` is empty. Any non-human label principal requires a separate
  identity, permission, and ownership review; Release Hub must remain read-only.
- `executor_app_id` is intentionally null while the train is `validate`; the
  reviewed numeric ID cannot be populated until the dedicated App exists.
- Executor App creation/installation, secret provisioning, rules, label
  provisioning, and workflow predicates have not been approved.
- The Python 3.10-3.12 local matrix, including bundle equality, is completely
  green. Hosted CI from the eventual clean reviewed revision remains mandatory.
- A production-parity sandbox executor adapter is not implemented; the current
  harness validates a prepare-only manifest and injected-test boundary, not the
  real planner/writer transaction against a substituted private repository.
- No private-sandbox end-to-end run has validated real GitHub installation-token
  scopes, API behavior, Checks, branch compare-and-create, or draft recovery.
- The schema-v5 #9716 prerequisite override is a local review proposal. Its
  exact objects, rationale, ownership, and lifecycle must be approved before
  any configuration publication.
- Real GitHub pagination and hydration for a ready manual covering PR, multiple
  simultaneous exact candidates, and an open-PR change during the write race
  have only fake-transport/local-Git coverage; they require private-sandbox
  drills.
- Historical evidence does not contain every merge/rebase, file-mode, gitlink,
  BKC/staging, planner, writer, or recovery shape; named deterministic tests
  cover those cells but remain synthetic evidence.
- Managed dependency stacking is implemented only for a reviewed, bounded DAG:
  the engine emits the currently unblocked frontier and requires exact
  containment before advancing the next wave. Multi-source bundles, automatic
  conflict resolution, and TheRock component-rollup synthesis remain
  intentionally unsupported.
- The rebuilt local bundle passes equality and structural validation but is
  deliberately nonpublishable `dirty_worktree_review` provenance. Publication
  requires another rebuild from a clean reviewed commit. Hosted Marketplace
  author/NTID validation, the mandatory security scan of that exact clean
  bundle, and submission remain incomplete; no current-revision compliance
  result or package upload was produced.
- Release Hub and Developer Central have complete local test/build/container
  evidence only. Private OIDC/JWKS exchange, an authenticated deployed config
  read, production token-pepper handling, and production rollout/rollback have
  not been exercised.
- GitHub's official OIDC rollout requires repositories created before
  2026-07-15 to opt in to immutable repository identities in the subject. The
  required remote setting has not been verified or enabled for TheRock,
  rocm-systems, rocm-libraries, or rockrel; legacy name-only subjects remain
  forbidden and there is no fallback.

These blockers prevent claims of production readiness or flawless behavior.
They do not invalidate the measured local Git-engine regression result.

## Activation boundary

No remote operation was performed during this local-only completion pass; the
existing hydrated corpus and clones were reused. The Release Hub API and
Developer Central images were built locally only; they were not started with
Compose, pushed, or deployed, and no Nod or other shared environment was
touched. No public PR, label change, Check, comment, workflow dispatch, App
change, secret change, ruleset change, public CI run, Marketplace submission,
private-sandbox remote execution, or Release Hub production mutation was
performed. The next step is human review of local uncommitted changes. Every
later remote action requires separate operator authorization and every
generated PR must remain a draft until a human explicitly decides otherwise.
