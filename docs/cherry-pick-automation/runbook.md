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
2. Confirm the TDD evidence shows the complete remediation suite failing before
   implementation and passing afterward.
3. Run unit and integration tests with local filesystem repositories and fake
   API transports.
4. Run repository-native formatting, pre-commit, actionlint, JSON/Markdown, SPDX,
   coverage, and diff checks using already available local tooling.
5. Inspect rendered source callers without publishing them.
6. Confirm initial train configuration is `validate` and the local safety gate
   cannot construct a real writer.
7. Record missing tools or unavailable gates as limitations; do not download or
   invoke a remote service to hide them.

## Future train setup (requires separate approval)

1. Add a unique schema-v3 train in `validate` mode.
2. Confirm every source branch and exact destination branch.
3. Confirm effective destination rules require a pull request.
4. Configure Jira and dependency policy only when required.
5. Review and merge the configuration through normal repository review.
6. Synchronize labels only after reviewing the exact mutations.
7. Run `validate`, then `shadow`, then a separately approved low-risk
   `create-draft` pilot.

## Review a future generated draft

1. Confirm source PR, canonical head, merged commit/range, and changeset proof.
2. Confirm train, exact base branch, and planned destination SHA.
3. Confirm Jira Fix Version and dependency/order evidence.
4. Reproduce the application strategy and inspect `-x` provenance.
5. Review the complete diff and repository-native CI.
6. Confirm the PR remains a draft.
7. Only a human may decide to mark the PR ready.

## Replay and partial transaction

Run read-only planning first. A future retry may repair a branch-pushed/PR-missing
state only when the branch tree and identity exactly match the recomputed plan.
An existing expected draft yields `draft_exists`. Any operator modification or
tree mismatch blocks; never overwrite it.

## Conflict or ambiguity

For `blocked_conflict` or `blocked_ambiguous_changeset`:

1. Preserve the JSON evidence.
2. Reproduce the full proven changeset in a disposable worktree.
3. Consult the owning component team.
4. Use a separately reviewed manual draft for any resolution.
5. Never change the result to contained merely because application conflicted.

## Disable or roll back after future deployment

Set the affected train to `disabled` through a reviewed configuration change.
Retain labels, drafts, branches, and evidence for operator disposition. Do not
perform destructive cleanup automatically.

## Human handoff checklist

- Product requirements and design match the reviewed implementation.
- Red/green evidence and coverage meet the documented gates.
- All source callers are thin, pinned, formatted, and locally tested.
- App permissions exclude administration, Actions, and Workflows.
- Initial modes are non-writing.
- Every remote action has separate approval and remains queued until granted.
