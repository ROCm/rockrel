# Draft — local review required

# Label-driven cherry-pick automation runbook

## Current operating boundary

This runbook is design documentation only. The automation is not deployed.
During local review, do not fetch, push, call GitHub/Jira, dispatch workflows,
or mutate any remote state. The only exception is a separately approved,
explicitly gated read-only corpus refresh; ordinary replay never uses it. Use
filesystem repositories and fake transports for every exercise. Queue all
public actions in `REMOTE_ACTIONS_TODO.md`.

## Operating principles

- A label requests evaluation; it never approves or merges a change.
- Every future generated pull request starts and remains a draft until a person
  acts.
- Only the exact configured destination branch is authoritative.
- A nightly/build occurrence is not destination containment evidence.
- A conflict, partial match, or ambiguous history is never containment.
- Declared dependencies or ordering requirements block v1 for operator review.
- The automation never force-pushes, deletes branches, closes drafts, marks
  ready, approves, merges, or enables auto-merge.

## Local review procedure

1. Inspect the PRD, technical design, audit, and complete local diff.
1. Confirm the TDD evidence shows the complete remediation suite failing before
   implementation and passing afterward.
1. Run unit and integration tests with local filesystem repositories and fake
   API transports.
1. Run repository-native formatting, pre-commit, actionlint, JSON/Markdown, SPDX,
   coverage, and diff checks using already available local tooling.
1. Inspect rendered source callers without publishing them.
1. Confirm initial train configuration is `validate` and the local safety gate
   cannot construct a real writer.
1. Record missing tools or unavailable gates as limitations; do not download or
   invoke a remote service to hide them.

## Future train setup (requires separate approval)

1. Add a unique schema-v3 train in `validate` mode.
1. Confirm every source branch and exact destination branch.
1. Confirm effective destination rules require a pull request.
1. Configure Jira and dependency policy only when required.
1. Review and merge the configuration through normal repository review.
1. Synchronize labels only after reviewing the exact mutations.
1. Run `validate`, then `shadow`, then a separately approved low-risk
   `create-draft` pilot.

## Review a future generated draft

1. Confirm source PR, canonical head, merged commit/range, and changeset proof.
1. Confirm train, exact base branch, and planned destination SHA.
1. Confirm Jira Fix Version and dependency/order evidence.
1. Reproduce the application strategy and inspect `-x` provenance.
1. Review the complete diff and repository-native CI.
1. Confirm the PR remains a draft.
1. Only a human may decide to mark the PR ready.

## Replay and partial transaction

Run read-only planning first. A future retry may repair a branch-pushed/PR-missing
state only when the branch tree and identity exactly match the recomputed plan.
An existing expected draft yields `draft_exists`. Any operator modification or
tree mismatch blocks; never overwrite it.

## Historical replay suite

Corpus refresh is the sole approved network-read exception for this local test
suite. It writes only dedicated local bare mirrors and an unreviewed candidate
inventory outside the repository; it never writes to GitHub or an existing
checkout. Replay is then run with lazy fetching disabled and produces reports
outside the repository.

The operator must review the inventory totals, ensure no case is unresolved,
and distinguish strict exact replays from bundles, manual resolutions, reverts,
release-native changes, and gitlink adaptations. A conflict or missing object is
never accepted as proof that the source change was already present.

Run the complete unit suite before treating the named synthetic coverage
registry as evidence:

```bash
.venv/bin/python -m pytest -q scripts/tests
```

Generate a candidate inventory from already hydrated mirrors without network
access. This command refuses to overwrite the tracked golden:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py inventory \
  --data-root /path/to/replay-data \
  --candidate-out /path/to/replay-data/candidates/historical-candidate.json
```

Compare the candidate with the reviewed golden. Added, removed, reclassified,
or changed cases are blocking until the JSON diff is reviewed and the golden is
edited deliberately:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py compare \
  --candidate /path/to/replay-data/candidates/historical-candidate.json \
  --golden scripts/tests/fixtures/historical_cherry_picks.json
```

Run the full standalone regression suite. `--jobs` bounds concurrent repository
lanes; cases in the same repository remain serialized and reuse one index:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py run \
  --data-root /path/to/replay-data \
  --manifest scripts/tests/fixtures/historical_cherry_picks.json \
  --report-dir /path/to/replay-data/reports \
  --tier deep \
  --jobs 4
```

The tracked synthetic registry is used by default; pass
`--synthetic-coverage /reviewed/path.json` only when deliberately reviewing a
different registry. `--tier fast` runs 17 minimized/representative rows. `deep`
runs all 77 reviewed transitions. An inventory-only case is reported as such
and does not count toward changeset/outcome/file/recovery engine coverage.

The schema-v3 JSON and Markdown reports list both historical-only gaps and
required cells lacking any evidence. The latter produce exit code 2. Named
synthetic tests can close a combined gap, but remain visibly separate from
historical counts. See `historical-replay-analysis.md` for the reviewed current
result and limitations.

To verify scheduling determinism, run the same deep corpus once with `--jobs 1`
and once with `--jobs 4`, then compare both report files byte-for-byte:

```bash
cmp serial/historical-replay.json parallel/historical-replay.json
cmp serial/historical-replay.md parallel/historical-replay.md
```

After interruption—or whenever an operator wants a known clean cache—run:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py rollback \
  --data-root /path/to/replay-data
```

Rollback does not delete or recreate worktrees. It validates ownership, repairs
an invalid index from an atomic snapshot or local HEAD, clears sequencer state,
resets the cached worktree, removes trial-only untracked files, and verifies its
HEAD/status/tree. A nonzero result is blocking; do not reuse that cache until it
is understood.

The persistent worktrees can be large, particularly for rocm-libraries. Do not
delete them during normal regression work: deletion discards the warm-index
benefit. Any eventual cache deletion is a separate, local, destructive cleanup
decision and is not part of replay rollback.

## Conflict or ambiguity

For `blocked_conflict` or `blocked_ambiguous_changeset`:

1. Preserve the JSON evidence.
1. Reproduce the full proven changeset in a disposable worktree.
1. Consult the owning component team.
1. Use a separately reviewed manual draft for any resolution.
1. Never change the result to contained merely because application conflicted.

## Disable or roll back after future deployment

Set the affected train to `disabled` through a reviewed configuration change.
Retain labels, drafts, branches, and evidence for operator disposition. Do not
perform destructive cleanup automatically.

## Human handoff checklist

- Product requirements and design match the reviewed implementation.
- Red/green evidence is complete; coverage either meets the documented gate or
  remains an explicit activation blocker.
- All source callers are thin, pinned, formatted, and locally tested.
- App permissions exclude administration, Actions, and Workflows.
- Initial modes are non-writing.
- Every remote action has separate approval and remains queued until granted.
