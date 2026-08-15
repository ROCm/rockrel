# Draft — local review required

# Label-driven cherry-pick automation: product requirements

## Problem

ROCm release operators repeatedly move merged changes from TheRock,
rocm-systems, and rocm-libraries development branches to release branches.
Today this is a careful manual process: identify the complete merged change,
confirm that the destination does not already contain it, apply it in a clean
branch, and open a draft pull request using the destination repository's normal
review and CI policy.

The product must automate those mechanics without weakening the manual safety
bar. A **train is a configured destination branch**. Express Train, nightly,
stabilization, and other release lines are policy profiles, not separate code
paths.

## Local-development boundary

This specification and its implementation are in local draft review. During
this phase the project MUST NOT perform network operations or mutate GitHub,
Jira, or any Git remote. Git write and API behavior is exercised only through
temporary filesystem repositories and fake transports. Public deployment work
is listed in `REMOTE_ACTIONS_TODO.md` and requires separate operator approval.

## Definitions

- **Train:** a stable ID and exact label mapped to one destination branch per
  configured repository.
- **Request label:** `cherry-pick:<train-id>`.
- **Source PR:** a canonical PR merged into a configured development branch.
- **Merged changeset:** the complete tree delta introduced by the source PR,
  proven from its actual squash, merge-commit, or rebase merge representation.
- **Plan:** a read-only, machine-readable decision tied to canonical source and
  destination SHAs.
- **Generated PR:** a draft PR containing the proven changeset and based on the
  exact destination branch.
- **Contained:** the complete changeset is already represented by the
  destination tree. Partial or ambiguous similarity is not containment.

## Goals

- Make an authorized label the GitHub-native request interface for any train.
- Keep trains, branches, lifecycle, rollout mode, and optional policy in
  version-controlled rockrel configuration.
- Support the branch patterns ROCm actually uses, including `release/` and
  `release-staging/`, without encoding either prefix as product policy.
- Support source PRs merged by squash, merge commit, or rebase and merge.
- Validate the canonical PR, source branch, label authorization, optional Jira
  policy, dependencies, destination ref, and effective PR rule before writing.
- Produce no more than one active draft for each source repository, source PR,
  and train identity.
- Do nothing to the destination when the complete change is already contained.
- Fail closed on conflicts, incomplete evidence, partial containment,
  destination movement, dependencies, races, or ambiguous history.
- Recover deterministically from duplicate events and partial transactions.
- Preserve repository-native review, branch rules, and CI in TheRock,
  rocm-systems, and rocm-libraries.

## Non-goals

- Marking generated PRs ready, approving, merging, or enabling auto-merge.
- Resolving conflicts or automatically ordering dependent changes in v1.
- Treating Release Hub nightly/build presence as destination-branch evidence.
- Parsing arbitrary dependency prose into an execution order.
- Replacing Jira, branch rules, CI, or reviewers.
- Force-pushing, deleting an automation branch, or closing a generated draft.

## Users

- **Release operator:** configures trains, applies labels, and reviews plans and
  generated drafts.
- **Source PR author:** receives actionable status without being granted release
  authority.
- **Repository maintainer:** reviews a generated draft under normal repository
  rules.
- **Automation administrator:** manages the App and rollout only after explicit
  approval.

## Product contract

### Configure a train

Schema version 3 models policy separately from branch naming:

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
        },
        "ROCm/rocm-systems": {
          "source_branches": ["develop"],
          "destination_branch": "release-staging/rocm-rel-10.1"
        }
      }
    }
  ]
}
```

Adding a train is configuration-only. Branch names must pass Git's canonical
ref-format validation; the product does not require a naming prefix.

### Request evaluation

An authorized maintainer applies the exact configured label. A label applied
before merge produces `awaiting_merge`; scheduled reconciliation reevaluates it
after merge. Multiple train labels are independent.

Removing a label before draft creation cancels only that train request. Removing
it after a draft exists does not close the draft or delete the branch; the
operator must decide what to do.

### Qualification

A request may proceed only when:

1. The exact label maps to an active train and configured repository.
2. The source base is in that repository's configured source-branch set.
3. The most recent canonical application of that label was performed by a user
   with `write`, `maintain`, or `admin` permission.
4. The source PR is merged and its complete merged changeset can be proven.
5. Configured Jira Fix Version evidence matches, when required.
6. No configured PR trailer or Jira signal declares an unresolved dependency or
   ordering requirement.
7. The exact destination exists and effective repository rules include an
   active pull-request requirement.
8. The current destination SHA matches the SHA used by the plan immediately
   before the simulated or future write transaction.

Transport failures and malformed evidence are blocked, never converted to
invalid or contained outcomes.

### Merge representations

- **Single/squash commit:** prove the commit delta equals the full PR delta and
  apply that commit.
- **Merge commit:** prove the two-parent merge result and apply it relative to
  parent one.
- **Rebase and merge:** identify the complete rebased range, prove the ordered
  range equals the full PR delta, and apply all commits in order.
- **Unknown/ambiguous:** return `blocked_ambiguous_changeset` without creating a
  branch.

### Dependencies

V1 recognizes machine-readable PR trailers and configured Jira dependency or
ordering evidence. If a prerequisite is not proven contained in the same
destination, evaluation returns `blocked_dependency`. V1 does not infer or
execute a dependency graph.

### Modes

| Mode | Trigger behavior | Credentials and writes |
| --- | --- | --- |
| `disabled` | Ignore requests and reconciliation | No API feedback or Git writes |
| `validate` | Manual planning only | Read-only; no event feedback |
| `shadow` | Event and scheduled planning | Read-only summaries/artifacts only |
| `create-draft` | Plan, replan, then create a draft | Future deployment only; unavailable in local-review mode |

### Outcomes

| Status | Meaning | Destination write |
| --- | --- | --- |
| `awaiting_merge` | Valid request on an open PR | None |
| `ineligible_source` | Deterministic source/policy failure | None |
| `blocked_evidence` | Required evidence is unavailable | None |
| `blocked_policy` | Destination or authorization policy is unsafe | None |
| `blocked_dependency` | Ordering/dependency requires an operator | None |
| `blocked_ambiguous_changeset` | Complete source change cannot be proven | None |
| `blocked_conflict` | Proven change conflicts with destination | None |
| `already_contained` | Exact complete change is already present | None |
| `covered_by_existing_pr` | Active or merged PR positively owns/covers identity | None |
| `draft_planned` | Clean, non-empty, write-eligible plan | None |
| `draft_created` | One future draft exists | Draft only |
| `draft_exists` | Idempotent replay found the expected draft | None |
| `retryable_partial_write` | Branch exists but PR creation is incomplete | Never overwrite; reconcile |
| `cancelled` | Label removed before draft creation or source closed unmerged | None |

Every result includes the source PR and repository, train, destination ref and
SHA, changeset representation, source commit or range, proof strategy,
qualification evidence, and an optional generated/covering PR URL.

## Draft PR quality

A future generated PR must:

- Be created with `draft: true` and remain draft until a person acts.
- Target the exact configured destination branch.
- Include source PR/SHAs, train, Jira evidence, application strategy and
  provenance, containment/conflict result, dependency status, local preflight,
  testing guidance, and an operator-review warning.
- Preserve the original source PR description below the generated metadata.
- Use deterministic identity metadata without claiming that destination CI has
  passed.

## Safety requirements

- The codebase exposes no ready-for-review, approve, merge, auto-merge,
  force-push, remote-delete, or draft-close operation.
- Planning and shadow operation cannot construct write credentials.
- The local-review build cannot construct a network-capable writer at all.
- Privileged event workflows never check out or execute PR-head code.
- Canonical labels and permissions are revalidated during reconciliation.
- API enumeration is fully paginated; retryable failures use bounded backoff.
- Concurrent events serialize by source repository, PR number, and train, while
  the writer still checks races at transaction boundaries.
- Existing operator-modified branches are blocked and never overwritten.

## Success criteria

- All planned remediation tests are committed locally in a demonstrated red
  state before product implementation changes.
- All old and new tests finish green, with at least 90% line and branch coverage
  for `scripts/cherry_pick`.
- TheRock, rocm-systems, and rocm-libraries callers are thin, pinned, formatted,
  SPDX-compliant, and covered by repository-local tests.
- Squash, merge-commit, and rebase fixtures prove correct complete changesets.
- Fresh-runner and post-push recovery are deterministic and idempotent.
- Already-contained decisions require positive complete-change proof.
- No test or implementation command contacts or mutates a real remote service.
- The final handoff is a local draft diff and evidence bundle. Remote actions
  remain an unexecuted operator TODO.

## Future rollout, outside this phase

After separate approval, each train progresses from `validate` to `shadow` and
then a reviewed `create-draft` pilot. Applying a qualifying label may then
create a draft automatically. This future approval never authorizes automatic
readiness or merge.
