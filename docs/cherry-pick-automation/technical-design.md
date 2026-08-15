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
2. GitHub/Jira read adapters.
3. Pure qualification and dependency policy.
4. Git changeset proof and disposable preflight.
5. Read-only orchestration and reconciliation.
6. Capability-gated Git/GitHub draft transaction.

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

Raw API dictionaries are decoded at the adapter boundary into dataclasses.
Malformed shapes raise a structured `EvidenceError`; product code does not
propagate arbitrary `KeyError`, `TypeError`, or raw stack traces.

### Changeset

```python
@dataclass(frozen=True)
class Changeset:
    kind: ChangesetKind       # single, squash, merge_commit, rebase_range
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

GitHub reads use typed, fully paginated adapters for:

- canonical PR, PR commits, and label timeline;
- collaborator permission;
- destination branch and effective rules for that branch;
- pull requests by destination and exact head branch;
- comments and reconciliation search results;
- commits and comparisons needed for changeset proof.

Pagination continues until a short page or `Link` exhaustion. Search respects
GitHub's result limit by deterministic time/number windows rather than silently
stopping at 100. Retryable `429`, `502`, `503`, and `504` responses and
rate-limit `403` responses use bounded exponential backoff with injected clock
and sleeper; other errors fail immediately.

Effective destination rules must contain an active `pull_request` rule. The
evidence records rule source/ID, required approvals, last-push approval, and
allowed merge methods. A branch merely reporting `protected: true` is
insufficient.

The future GitHub App maximum is contents write, issues write, and pull requests
write. Metadata read is implicit/read-only. Administration permission is not
requested because the effective-rules and collaborator-permission endpoints use
metadata read.

Jira is called only when configured policy requires it. It returns typed Fix
Version and dependency/order facts. Arbitrary free text is not interpreted as
an executable graph; configured non-empty ordering evidence blocks for review.

## 6. Qualification and event flow

The central reusable workflow receives one event and performs:

1. Reject a caller whose checkout SHA differs from the pinned reusable-workflow
   SHA.
2. Resolve current canonical `cherry-pick:` labels plus the affected label for
   an `unlabeled` event.
3. Resolve configured trains and fan out once per source PR/train identity.
4. Re-fetch the canonical PR and most recent label event.
5. Validate actor permission, source base, merge state, optional Jira policy,
   dependencies, destination existence, and effective PR rule.
6. Build and prove the merged changeset.
7. Inspect identity/coverage and run disposable preflight.
8. Emit a typed plan. Only a future `create-draft` job may pass a `draft_planned`
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

| Evidence | Outcome |
| --- | --- |
| Exact application units are ancestors of destination | `already_contained` |
| Applying the full proven changeset produces no tree delta | `already_contained` |
| Active/merged PR has exact identity and expected tree | `covered_by_existing_pr` |
| Full proven application is clean and non-empty | `draft_planned` |
| Full proven application has unmerged paths | `blocked_conflict` |
| Only some commits/paths appear equivalent | `blocked_ambiguous_changeset` |
| Required Git evidence cannot be read | `blocked_evidence` |

Gitlink containment additionally requires directional submodule ancestry or
common full `cherry picked from` provenance. Divergence blocks manual review.

## 8. Draft transaction and recovery

The identity is `(source repository, source PR number, train ID)`. The default
branch is `shared/cherry-pick/<train-id>/<source-pr-number>`, validated against
effective repository rules before use.

Before any future mutation the writer:

1. Requires a valid capability and `draft_planned` result.
2. Re-fetches the exact destination SHA and replans if it moved.
3. Looks up an existing PR by exact head/base and identity marker.
4. Looks up the exact remote branch SHA.
5. Reproduces the application in a disposable worktree with explicit bot name
   and noreply email.
6. Compares trees before deciding to reuse an existing branch.

State handling:

| Branch | Draft PR | Tree | Result/action |
| --- | --- | --- | --- |
| absent | absent | expected | creation lease, then create draft |
| expected | absent | expected | create missing draft; no push |
| expected | expected | expected | `draft_exists` |
| different | any | mismatch | `blocked_policy`; never overwrite |
| branch created during lease | absent | expected | re-read and recover |
| push succeeds, PR API fails | present | absent | `retryable_partial_write` |

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
  entry point and uses behavior/event fixtures rather than text-only assertions.

## 11. TDD and verification

The implementation sequence is mandatory:

1. PRD, design, gap register, and test contract.
2. Complete new test suite.
3. Recorded red run proving intended behavioral failures.
4. Product implementation in small green slices.
5. Refactor only while green.
6. Full local repository gates and evidence bundle.

Tests cover schema, modes, API decoding/pagination/retries, rule evidence,
authorization, dependencies, every merge representation, exact/partial
containment, conflicts, fresh-runner recovery, Git identity, partial writes,
races, workflow events, security boundaries, and draft rendering. Integration
tests use temporary local bare repositories and fake GitHub/Jira servers.

Coverage for `scripts/cherry_pick` must be at least 90% for both lines and
branches. Writer and recovery modules cannot be omitted. Black, pre-commit,
actionlint, Markdown/JSON validation, SPDX checks, and `git diff --check` must
pass locally.

## 12. Local handoff

The deliverable is a local-only draft branch/diff in all four repositories,
red/green TDD evidence, coverage output, and an unexecuted remote-action TODO.
No public branch, PR, App setting, secret, label, workflow, or CI action is part
of this implementation phase.
