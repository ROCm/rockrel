# Draft — local review required

# Label-driven cherry-pick automation: technical design

## 1. Safety boundary for this implementation phase

All work remains local. Implementation and tests MUST NOT fetch from or push to
network Git remotes, call GitHub or Jira, dispatch Actions, or mutate public
state. Tests use only temporary filesystem Git repositories and fake in-process
HTTP transports. A `RemoteWriteCapability` cannot be constructed by the normal
CLI in local-review builds; environment variables alone never enable writes.

The eventual remote deployment described here is a design target, not authority
to perform it. Every remote step is queued in `REMOTE_ACTIONS_TODO.md`.

## 2. Repository-aligned architecture

`rockrel` owns train configuration, policy, reusable workflows, reconciliation,
and the Python controller, matching its existing role in release branch and tag
orchestration. TheRock, rocm-systems, and rocm-libraries contain only a pinned
caller workflow plus a repository-native contract test.

The source caller uses `pull_request_target` for `labeled`, `unlabeled`, and
`closed`, but never checks out or executes PR-head code. It passes only the
canonical PR URL, action, affected label, and pinned automation SHA to one
reusable workflow. Label discovery and train fan-out occur centrally; duplicated
embedded Python is removed from callers.

The central controller has six layers:

1. Typed configuration and result contracts.
1. GitHub/Jira read adapters.
1. Pure qualification and dependency policy.
1. Git changeset proof and disposable preflight.
1. Read-only orchestration and reconciliation.
1. Capability-gated Git/GitHub draft transaction.

No layer exposes ready-for-review, review, merge, auto-merge, force-push,
remote-delete, or draft-close operations.

## 3. Configuration contract

Schema version 3 is the only accepted schema because the project has not been
deployed. The migration is local and atomic.

```json
{
  "schema_version": 3,
  "trains": [
    {
      "id": "10.1-20260811",
      "label": "cherry-pick:10.1-20260811",
      "state": "active",
      "mode": "validate",
      "requirements": {
        "jira_fix_version": "10.1.0a20260811",
        "block_on_dependencies": true
      },
      "repositories": {
        "ROCm/TheRock": {
          "source_branches": ["main"],
          "destination_branch": "release/bkc/therock-10.1-20260811"
        }
      }
    }
  ]
}
```

Validation rules:

- IDs match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
- Labels are unique and exactly `cherry-pick:<id>`.
- Repositories are restricted to the supported ROCm allowlist.
- `source_branches` is a non-empty unique string list.
- Source and destination names pass `git check-ref-format --branch`; no branch
  prefix is required.
- Each train maps a repository once, and two active trains may not use the same
  label. Different trains may intentionally target the same destination.
- Requirements accept only `jira_fix_version` and
  `block_on_dependencies`; unknown fields fail loading.
- Initial committed modes remain `validate`.

`disabled`, `validate`, `shadow`, and `create-draft` are behaviors, not aliases.
Inactive trains are excluded from label discovery. Disabled trains return no
feedback. Explicit manual validation of an inactive train is rejected.

## 4. Typed public interfaces

### Result

`Result` contains only JSON-safe typed evidence and one `Status` enum value:

```text
awaiting_merge, ineligible_source, blocked_evidence, blocked_policy,
blocked_dependency, blocked_ambiguous_changeset, blocked_conflict,
already_contained, covered_by_existing_pr, draft_planned, draft_created,
draft_exists, retryable_partial_write, cancelled
```

Required fields are `status`, `reason_code`, `message`, `source_pr`,
`source_repository`, `train_id`, and `destination_branch`. Planning after merge
also records destination SHA, changeset kind, ordered commits, mainline when
applicable, proof method, and dependency/policy evidence.

Safety-critical branch, destination-policy, Jira, configuration, changeset, and
result values are decoded into dataclasses. GitHub PR, commit, compare, and
comment payloads retain their extensible API dictionaries, but required fields
are type-checked before use. Malformed or missing canonical evidence produces a
structured blocked result; product commands do not expose raw stack traces.

### Changeset

```python
@dataclass(frozen=True)
class Changeset:
    kind: ChangesetKind  # single, squash, merge_commit, rebase_range
    commits: tuple[str, ...]  # application order
    aggregate_base: str
    aggregate_head: str
    mainline: int | None
    proof: ChangesetProof
```

`ChangesetProof` records the canonical PR head, merged SHA, parent/range SHAs,
patch IDs or tree-delta identifiers, and why the representation is complete.

### Remote-write capability

`DraftWriter` requires an injected `RemoteWriteCapability` created only by a
future reviewed workflow adapter. The local CLI has no factory for it. GitHub
writer methods additionally accept an explicit idempotency identity. Fake
capabilities are available only under tests.

## 5. Read adapters and permissions

GitHub reads use typed or shape-checked paginated adapters for:

- canonical PR, PR commits, and label timeline;
- collaborator permission;
- destination branch and effective rules for that branch;
- pull requests by destination and exact head branch;
- comments and reconciliation search results;
- commits and comparisons needed for changeset proof.

Pagination continues until a short page. Search never silently stops at the
first 100 results: every available page is requested, and a platform cap or API
error becomes blocked evidence. Retryable GitHub `429`, `502`, `503`, and `504`
responses and rate-limit `403` responses use bounded exponential backoff with
an injected sleeper; other errors fail immediately.

Effective destination rules must contain an active `pull_request` rule. The
evidence records rule source/ID, required approvals, last-push approval, and
allowed merge methods. A branch merely reporting `protected: true` is
insufficient.

The future GitHub App maximum is contents write, issues write, and pull requests
write. Metadata read is implicit/read-only. Administration permission is not
requested because the effective-rules and collaborator-permission endpoints use
metadata read.

Jira is called only when configured policy requires it. It returns typed Fix
Version and dependency/order facts. Jira failures block immediately rather than
being translated into policy facts. Arbitrary free text is not interpreted as
an executable graph; configured non-empty ordering evidence blocks for review.

## 6. Qualification and event flow

The central reusable workflow receives one event and performs:

1. Use the single immutable SHA rendered into both the caller's reusable
   workflow reference and its `automation_ref` input.
1. Resolve current canonical `cherry-pick:` labels plus the affected label for
   an `unlabeled` event.
1. Resolve configured trains and fan out once per source PR/train identity.
1. Re-fetch the canonical PR and most recent label event.
1. Validate actor permission, source base, merge state, optional Jira policy,
   dependencies, destination existence, and effective PR rule.
1. Build and prove the merged changeset.
1. Inspect identity/coverage and run disposable preflight.
1. Emit a typed plan. Only a future `create-draft` job may pass a `draft_planned`
   result to the writer.

One PR may target multiple trains. Workflow concurrency is keyed by canonical
source repository, PR number, and train ID, with `cancel-in-progress: false`.
Transaction code still handles races because workflow concurrency is not a
database lock.

Removing a label before a draft exists yields `cancelled`. If a draft or remote
branch already exists, removal records operator action required and performs no
destructive mutation.

## 7. Complete changeset proof

All Git commands use argument arrays, `shell=False`, and disposable worktrees.
The engine never uses a nightly/build occurrence as branch containment proof.

### Merge commit

When the merged SHA has two parents, verify parent one is the destination-side
base and the merge tree represents the canonical PR head integration. The
application unit is the merge SHA with mainline one.

### Single or squash commit

When the merged SHA has one parent, compare that commit's normalized tree delta
with the canonical complete PR delta. Equality proves a single/squash
representation. The application unit is the merged SHA.

### Rebase range

If the merged SHA's single-commit delta is not the aggregate PR delta, walk the
number of canonical PR commits backward from the merged SHA. Prove the ordered
range corresponds to the PR commits by normalized patch identity and that the
range's aggregate tree delta equals the PR delta. Apply the proven range oldest
to newest.

Any missing object, unexpected intervening commit, patch mismatch, octopus
merge, or uncertain base returns `blocked_ambiguous_changeset`.

### Containment and preflight decision table

| Evidence                                                  | Outcome                       |
| --------------------------------------------------------- | ----------------------------- |
| Exact application units are ancestors of destination      | `already_contained`           |
| Applying the full proven changeset produces no tree delta | `already_contained`           |
| Active/merged PR has exact identity and expected tree     | `covered_by_existing_pr`      |
| Full proven application is clean and non-empty            | `draft_planned`               |
| Full proven application has unmerged paths                | `blocked_conflict`            |
| Only some commits/paths appear equivalent                 | `blocked_ambiguous_changeset` |
| Required Git evidence cannot be read                      | `blocked_evidence`            |

Gitlink containment additionally requires directional submodule ancestry or
common full `cherry picked from` provenance. Divergence blocks manual review.

## 8. Draft transaction and recovery

The identity is `(source repository, source PR number, train ID)`. The default
branch is `shared/cherry-pick/<train-id>/<source-pr-number>`, validated against
effective repository rules before use.

Before any future mutation the writer:

1. Requires a valid capability and `draft_planned` result.
1. Re-fetches the exact destination SHA and replans if it moved.
1. Looks up an existing PR by exact head/base and identity marker.
1. Looks up the exact remote branch SHA.
1. Reproduces the application in a disposable worktree with explicit bot name
   and noreply email.
1. Compares trees before deciding to reuse an existing branch.

State handling:

| Branch                      | Draft PR | Tree     | Result/action                     |
| --------------------------- | -------- | -------- | --------------------------------- |
| absent                      | absent   | expected | creation lease, then create draft |
| expected                    | absent   | expected | create missing draft; no push     |
| expected                    | expected | expected | `draft_exists`                    |
| different                   | any      | mismatch | `blocked_policy`; never overwrite |
| branch created during lease | absent   | expected | re-read and recover               |
| push succeeds, PR API fails | present  | absent   | `retryable_partial_write`         |

Fetching an existing branch uses its resolved SHA or an explicit temporary ref;
it never assumes that `git fetch origin <branch>` creates a same-named local
branch. API failure after push is converted to a structured result. Reconcile
repairs only the missing draft when tree and identity still match.

Generated commits use `ROCm Cherry-Pick Automation` and a reviewed GitHub
noreply address. Pushing uses a creation lease only; force push and deletion are
not implemented.

## 9. Generated draft body

The draft template mirrors established ROCm cherry-pick PRs and contains:

- draft/operator-review warning;
- source PR, repository, head SHA, merged SHA/range, and train;
- exact destination branch and planned head SHA;
- Jira keys and Fix Version evidence;
- merge representation, application command/strategy, and `-x` provenance;
- containment/conflict preflight and dependencies/order section;
- test plan, observed local result, and repository-native CI reminder;
- submission checklist and immutable identity marker;
- unmodified source PR description below generated metadata.

The automation does not claim CI success and does not interpret unrelated
repository check failures as changeset correctness.

## 10. Workflow and repository conventions

- Every action is pinned to a full SHA already approved by the owning ROCm
  repository at implementation review time.
- The called workflow and checked-out rockrel revision are identical.
- Python is installed explicitly; runner-preinstalled Python is not assumed.
- Source callers request read-only built-in token permissions and do not receive
  write App credentials.
- Central Python follows TheRock's documented Black/PEP 8, modern typing,
  dataclass, fail-fast, and subprocess-array conventions.
- New Python files have ROCm copyright and SPDX headers.
- Tests use the previously requested `*_test.py` naming convention.
- Caller verification is integrated into each repository's existing local CI
  entry point. Focused contract assertions cover event metadata and pin
  equality, while actionlint supplies full workflow parsing.

## 11. TDD and verification

The implementation sequence is mandatory:

1. PRD, design, gap register, and test contract.
1. Complete new test suite.
1. Recorded red run proving intended behavioral failures.
1. Product implementation in small green slices.
1. Refactor only while green.
1. Full local repository gates and evidence bundle.

Tests cover schema, modes, API decoding/pagination/retries, rule evidence,
authorization, dependencies, every merge representation, exact/partial
containment, conflicts, fresh-runner recovery, Git identity, partial writes,
races, workflow events, security boundaries, and draft rendering. Integration
tests use temporary local bare repositories and fake GitHub/Jira servers.

Coverage for `scripts/cherry_pick` must be at least 90% for both lines and
branches before activation. Writer and recovery modules cannot be omitted. The
checked-in unit workflow enforces that gate. If the local-only environment lacks
`pytest-cov`, the gate is recorded as unverified; the no-network boundary is not
relaxed to manufacture a local result. Black, actionlint, Markdown/JSON
validation, SPDX checks, and `git diff --check` must pass locally.

## 12. Local handoff

The deliverable is a local-only draft branch/diff in all four repositories,
red/green TDD evidence, the coverage-gate status, and an unexecuted remote-action
TODO. All remote-write jobs remain behind an impossible repository predicate
and contain no active transaction step. A separately reviewed activation change
must replace those stubs only after the local evidence and coverage gate are
accepted. No public branch, PR, App setting, secret, label, workflow, or CI
action is part of this implementation phase.

## 13. Historical replay architecture

Historical validation has separate discovery, reviewed-oracle, and execution
phases. Engine output is never allowed to become its own expected result.

### Read-only corpus refresh

A standalone replay CLI creates full bare mirrors under a caller-selected data
root. It accepts only the official TheRock, rocm-systems, and rocm-libraries
URLs and only invokes read-only Git fetch operations. Push URLs are replaced by
an invalid `disabled://read-only` URL. It fetches the configured source and
target branches, discovers explicit source PR references, and fetches the
corresponding `refs/pull/<number>/head` refs so squash, merge-commit, and rebase
representations can be proven locally.

The refresh writes a deterministic schema-v2 candidate inventory outside the
tracked fixture. Each record contains repository and branch identity, immutable
source and destination SHAs/trees, source PR metadata, provenance method, and
observed engine behavior. Target commits are enumerated along the first-parent
history between the source/target merge base and the pinned target tip.

The tracked schema-v2 golden is a separate, immutable review artifact. It pins
each case's classification, changeset representation and order, execution
phase, status/reason, historical/planned tree, conflict paths, post-merge
containment result, coverage dimensions, and rationale. Candidate generation
cannot write that path. Comparing a candidate with the golden fails on added,
removed, reclassified, or changed cases until a human reviews the exact diff.

Corpus branch names use the same `git check-ref-format --branch` validation as
train configuration. The corpus specification explicitly allowlists the three
supported repositories and pinned destination refs; the implementation does
not encode a release-branch prefix.

### Offline replay

Replay always exports `GIT_NO_LAZY_FETCH=1` and
`GIT_TERMINAL_PROMPT=0`. Missing objects produce an evidence-gap exit, never a
network request or inferred result. The runner calls only changeset proof and
Git evaluation; it cannot construct API clients or `DraftWriter`.

For a strict one-source case, the engine evaluates the source changeset against
the historical target parent. `draft_planned` is required, and the engine's
`planned_tree` must exactly equal the recorded historical after-tree. Commit
IDs are not compared because cherry-pick metadata changes commit identity. The
same source is then evaluated against the historical after-commit and pinned
target tip; both must return `already_contained` with positive evidence.

Bundles, release-native changes, target-only reverts, gitlink rollups,
clean-but-adapted trees, and manual resolutions retain exact diagnostic
contracts. Inventory-only and engine-executed cases are reported separately.
Cross-repository gitlink cases use pinned component mirrors and the production
direction/provenance classifier. A conflict is never containment evidence. Any
strict failure is minimized into a synthetic red unit test before an engine
correction is made.

### Containment by proven destination application

Direct source ancestry and a completely empty trial application remain the
fast containment proofs. When later destination evolution makes the source
conflict, the evaluator may inspect reachable first-parent commits carrying
strong source identity: a full source merge SHA, Git's `-x` trailer, a canonical
source URL, or an explicit cherry-pick PR marker.

For each identity candidate, the evaluator applies the complete changeset to
the candidate's first parent in an isolated worktree. Only exact equality with
the candidate commit tree proves `complete_changeset_application_ancestor`.
Merely finding text, a similar patch, or a conflict is insufficient. An
explicit later revert of the proven application returns an ambiguous blocking
result for operator review.

### Local production-pipeline replay

The deep runner can construct frozen GitHub/Jira adapters from the golden,
invoke the real `Planner`, and pass writable plans to the real `DraftWriter`
using only a filesystem bare remote and in-memory pull-request adapter. It
checks the generated branch parent/tree, `-x` provenance, bot identity, draft
flag/body, idempotent retry, and post-merge result. Negative cases must leave
the local remote and fake API unchanged. This simulator has no network client
and cannot create a public branch or pull request.

The runner emits canonical JSON and Markdown reports. Each ordered row records
repository, branch, classification, expected and actual phase/status/reason,
destination/planned/historical trees, conflict paths, post-merge result,
coverage dimensions, and a root-cause category. Reports separately count
inventory-only, core, planner, writer, and post-merge execution. Exit status
`0` means every reviewed expectation passed, `1` means behavior differed, and
`2` means evidence, object completeness, inventory review, or coverage is
insufficient.

### Fast and deep gates

The fast gate contains every minimized regression and a representative matrix,
with a two-minute warm and five-minute cold target. The deep gate processes all
pinned transitions and emits uncovered historical cells for repository,
destination family, changeset kind, outcome, file operation, change size, and
recovery mode. A required cell may be backed by historical or deterministic
synthetic evidence, but the report exposes the source. Serial and parallel
reports must be byte-identical.

### Persistent replay worktrees and rollback

The data root owns one persistent worktree per repository under
`.cherry-pick-replay-worktrees/<repository>`. This is derived local cache, not
a source checkout. The runner parallelizes repository lanes up to `--jobs` and
serializes cases within a lane. This permits TheRock, rocm-systems, and
rocm-libraries to replay concurrently without concurrent writes to a shared
index.

Before and after each trial, the engine validates that the worktree's Git common
directory belongs to the expected mirror, clears cherry-pick sequencer state,
resets to the immutable historical parent, removes untracked trial files, and
requires exact HEAD, clean status, index tree, and destination tree agreement.
Failure at any point returns blocked evidence.

After a clean rollback, the worktree index is copied to an atomic sidecar
snapshot. A missing or invalid `DIRC` index is restored from that snapshot. If
the first snapshot has not yet been created, Git reconstructs a temporary index
from the local pinned HEAD and atomically installs it; this does not fetch or
recreate the worktree. The standalone `rollback` command applies this recovery
to all cached repositories, making interruption recovery explicit and fast.
