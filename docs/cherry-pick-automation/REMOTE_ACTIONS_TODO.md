# Draft — explicit operator approval required

# Remote actions TODO

Nothing in this file is authorized by the local implementation phase. Do not
execute any item until a human has reviewed the complete local diff and granted
specific approval for a later remote phase.

## Existing stopped attempt

- [ ] Read-only operator verification of the state of the previously stopped
  rockrel, TheRock, rocm-systems, and rocm-libraries public attempts.
- [ ] Decide whether any separate cleanup is required; do not perform cleanup
  from the local implementation workspace.

## Public review

- [ ] Review all four local draft branches, commits, red/green evidence,
  coverage, threat model, permissions, and generated workflow diffs.
- [ ] Approve or reject publishing each repository independently.
- [ ] If approved, push new reviewed branches without reusing old temporary
  branches.
- [ ] Open new PRs as drafts and leave them draft through operator and owner
  review.
- [ ] Run public repository CI only after explicit approval.

## GitHub App and repository configuration

- [ ] Review an App maximum of contents write, issues write, and pull requests
  write, with metadata read; do not grant administration, Actions, or Workflows
  permission.
- [ ] Create/install the private App only on specifically approved repositories.
- [ ] Install selected-repository secrets after security review.
- [ ] Add or modify train labels only after reviewing the exact label set.
- [ ] Configure required rockrel checks only through a reviewed ruleset change.

## Caller and workflow deployment

- [ ] Record the immutable reviewed rockrel SHA.
- [ ] Render and review each caller pinned to that exact SHA.
- [ ] Merge the central workflow before merging callers that reference it.
- [ ] Dispatch no workflow until configuration and credentials are reviewed.

## Controlled rollout

- [ ] Run approved manual `validate` cases and retain artifacts.
- [ ] Promote one train to `shadow` through a reviewed change.
- [ ] Confirm shadow minted no write credential and produced no branch/PR.
- [ ] Review representative squash, merge-commit, rebase, dependency, conflict,
  containment, and recovery evidence.
- [ ] Approve one low-risk `create-draft` pilot through a separate reviewed
  configuration change.
- [ ] Confirm the qualifying label creates exactly one draft and does not mark
  it ready or merge it.
- [ ] Require independent operator confirmation of source changeset, Jira,
  destination, dependencies, diff, and native CI before anyone marks it ready.
