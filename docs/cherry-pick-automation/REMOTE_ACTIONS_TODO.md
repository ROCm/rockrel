# Draft — explicit operator approval required

# Remote actions TODO

Nothing in this file is authorized by the local implementation phase. Do not
execute any item until a human has reviewed the complete local diff and granted
specific approval for a later remote phase.

The historical replay task has one narrow exception approved on 2026-08-15:
read-only Git fetches from the three official public ROCm repositories into a
dedicated local corpus directory. This exception does not authorize GitHub API
mutation, push, comment, label, workflow, App, secret, branch, or pull-request
changes.

## Existing stopped attempt

- [ ] Read-only operator verification of the state of the previously stopped
  rockrel, TheRock, rocm-systems, and rocm-libraries public attempts.
- [ ] Decide whether any separate cleanup is required; do not perform cleanup
  from the local implementation workspace.

## Public review

- [ ] Review all four local draft branches, commits, red/green evidence,
  coverage, threat model, permissions, and generated workflow diffs.
- [ ] Review the 17-case fast selection, all 77 schema-v2 golden expectations,
  the schema-v3 deep report, 21 historical-only gaps, and every named synthetic
  coverage claim.
- [ ] Run the configured 90% line/branch coverage gate in an approved
  environment with the pinned test dependencies; treat failure as blocking.
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
- [ ] Implement and review the currently inert remote transaction steps; keep
  the impossible local-review predicates until that separate change is
  approved with coverage evidence.
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

## Future corpus maintenance

- [ ] Approve any new read-only refresh separately and review the exact official
  repository/branch refspecs before execution.
- [ ] Generate the candidate outside the repository and compare it with the
  tracked golden; never overwrite or auto-promote the golden.
- [ ] Review every added, removed, reclassified, endpoint-changed, expectation,
  and fast/deep-tier change before committing an updated corpus.
- [ ] Re-run unit, fast, deep parallel, deep serial, byte-comparison, and
  rollback gates before accepting a refreshed corpus.
