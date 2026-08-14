# Label-driven cherry-pick automation: product requirements

## Problem

ROCm release operators repeatedly move merged changes from development branches
to destination release branches. The mechanics are the same whether the
destination is called an Express Train, a nightly train, a stabilization train,
or another release line: validate a labeled request, determine whether the
change is already present, and create a draft cherry-pick pull request only when
one is needed.

The product must therefore model a **train as a configured destination release
branch**, not as a special Express Train workflow. Express Train policy is one
configuration profile of the generic automation.

## Definitions

- **Train:** a stable ID and label mapped to a destination release branch in one
  or more supported repositories.
- **Request label:** the exact configured label that requests evaluation for a
  train, conventionally `cherry-pick:<train-id>`.
- **Source PR:** a canonical merged PR based on an allowed development branch.
- **Generated PR:** a draft PR containing the aggregate source change and based
  on the train's exact destination branch.
- **Train policy:** optional qualification requirements such as a Jira Fix
  Version. Policies are data; they are not baked into the automation identity.

## Goals

- Make a repository label the GitHub-native request interface for any configured
  destination-branch train.
- Keep train labels, destinations, source branches, lifecycle, rollout mode, and
  optional policy in version-controlled configuration.
- Validate repository, source branch, labeler authority, optional train policy,
  and the exact destination branch before writing anything.
- Produce at most one active draft PR per source PR, repository, and train.
- Detect changes already contained or covered by another PR instead of creating
  duplicates.
- Recover safely from duplicate events, missed deliveries, abandoned generated
  PRs, destination movement, and interrupted runs.
- Keep policy and implementation centralized in ROCm/rockrel while using thin
  callers in TheRock, rocm-systems, and rocm-libraries.

## Non-goals

- Automatically marking generated PRs ready, approving, merging, or enabling
  auto-merge.
- Automatically resolving conflicts or dependency ordering.
- Treating a nightly build that contains a change as proof that a different
  destination branch contains it.
- Replacing Jira, branch protection, CI, or repository reviewers.
- Assuming every train uses Jira or Express Train policy.

## Users

- **Release operator:** defines trains, applies labels, reviews evidence, and
  controls rollout.
- **Source PR author:** receives status and remediation details on the source PR.
- **Repository maintainer:** reviews generated drafts under normal branch rules.
- **Automation administrator:** manages the GitHub App, secrets, required
  checks, and emergency disablement.

## User experience

### Configure a train

An operator adds a train with an explicit label and per-repository destination:

```json
{
  "id": "10.1-20260811",
  "label": "cherry-pick:10.1-20260811",
  "state": "active",
  "mode": "validate",
  "requirements": {
    "jira_fix_version": "10.1.0a20260811"
  },
  "repositories": {
    "ROCm/TheRock": {
      "source_branch": "main",
      "destination_branch": "release/bkc/therock-10.1-20260811"
    }
  }
}
```

Another train may omit `requirements.jira_fix_version`; it then has no Jira Fix
Version gate. Adding a new destination train must require configuration only,
not a new workflow or code path.

### Request a cherry-pick

An authorized operator applies the configured label to a source PR. Labels may
be applied before or after merge. A valid post-merge label runs immediately.
Removing one train label cancels only that train's request; it must not cancel
other labels on the PR.

### Qualification

A request qualifies only when all applicable rules pass:

1. The exact label maps to one active configured train.
2. The source repository is configured for that train.
3. The source PR base equals that repository's configured source branch.
4. The label actor currently has `write`, `maintain`, or `admin` permission.
5. The source PR is merged and its aggregate merge commit is available.
6. If the train configures a Jira Fix Version, at least one referenced ROCm Jira
   issue has that exact Fix Version.
7. The exact destination branch exists and is governed by PR rules.

An open qualifying PR receives `waiting_for_merge`. Closing without merge or
removing the configured label records `cancelled`.

### Outcomes

| Status | Meaning | Write behavior |
| --- | --- | --- |
| `waiting_for_merge` | Valid request; source is open | None |
| `invalid` | A deterministic rule failed | Remove only the invalid train label |
| `blocked` | Required evidence is temporarily unavailable | Retain label; no branch |
| `already_contained` | Exact destination contains the change | None |
| `covered_by_existing_pr` | An active or merged destination PR covers it | None |
| `cherry_pick_required` | Clean non-empty application is proven | None during planning |
| `draft_created` | One draft destination PR exists | Draft only |
| `manual_resolution_required` | Conflict or ambiguous state | None |
| `cancelled` | Request removed or source closed unmerged | None |

## Safety requirements

- Generated PRs always remain drafts until an operator acts.
- No code path or token permission may mark ready, approve, merge, or auto-merge.
- Conflicts are never interpreted as containment.
- Decisions use canonical refs and the exact configured destination branch.
- The destination head is checked again before push; movement causes a full
  replan, and a second movement stops safely.
- Privileged workflows never check out or execute source PR head code.
- Temporary GitHub or Jira failures retain labels and create no branch.
- `validate` and `shadow` modes cannot acquire write credentials.
- A closed, unmerged generated PR does not permanently suppress recovery.

## Configuration and lifecycle

Each train declares an ID, exact label, state, mode, optional requirements, and
one or more repository source/destination mappings. Supported states are
`active` and `inactive`; supported modes are `disabled`, `validate`, `shadow`,
and `create-draft`.

Train labels must use the reserved `cherry-pick:` namespace and be unique.
Destination branches must begin with `release/`. Activating a train synchronizes
its label; inactivating it prevents new requests without deleting history.

## Success criteria

- Adding a train is a configuration-only change.
- Applying its label to a qualifying merged PR creates exactly one draft against
  its exact destination branch when the train is in `create-draft` mode.
- A train with no Jira requirement can qualify without a Jira reference.
- A train with a Jira requirement fails closed on missing or mismatched evidence.
- Replays and scheduled reconciliation have no duplicate write effect.
- Existing, covered, conflicting, and abandoned-PR cases resolve deterministically.
- The seven 0811 cases remain a regression corpus for one configured train.
- Unit, Git integration, workflow, security, and repository-local caller tests pass.

## Rollout

Every train progresses independently through `validate`, `shadow`, and
`create-draft`. The first write for a new train is a reviewed pilot. Expanding
writes requires operator review and never relaxes the draft-only rule.
