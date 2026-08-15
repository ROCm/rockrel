# Draft — local review required

# Label-driven cherry-pick automation runbook

## Current operating boundary

This runbook is design documentation only. The automation is not deployed.
During local review, do not fetch, push, call GitHub/Jira, dispatch workflows,
or mutate any remote state. Use temporary filesystem repositories and fake
transports for every exercise. Queue all public actions in
`REMOTE_ACTIONS_TODO.md`.

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
suite. It writes only dedicated local bare mirrors and a reviewable manifest;
it never writes to GitHub or an existing checkout. Replay is then run with lazy
fetching disabled and produces reports outside the repository.

The operator must review the inventory totals, ensure no case is unresolved,
and distinguish strict exact replays from bundles, manual resolutions, reverts,
release-native changes, and gitlink adaptations. A conflict or missing object is
never accepted as proof that the source change was already present.

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
