# Express Train cherry-pick automation: product requirements

## Problem

ROCm Express Train operators currently translate a list of qualifying pull
requests into release-branch cherry-pick pull requests by hand. The work spans
TheRock, rocm-systems, and rocm-libraries, is sensitive to the exact source and
target branches, and is easy to duplicate or apply to the wrong train.

The product will provide a GitHub-native request mechanism: an authorized
operator labels a source pull request with an Express Train identifier. After
the source pull request is merged, automation validates the request and creates
a draft pull request against the configured release branch.

The operator experience is modeled on LinkedIn's label-driven cherry-pick
automation, but ROCm deliberately retains manual review and merge control.

## Goals

- Make a repository label the primary cherry-pick request interface.
- Validate the train, repository, source branch, labeler, Jira Fix Version, and
  target branch before writing anything.
- Produce at most one draft pull request per source PR, repository, and train.
- Detect changes that are already contained or covered by an existing pull
  request instead of creating duplicates.
- Report a durable, understandable status on the source pull request.
- Recover safely from duplicate events, target movement, and interrupted runs.
- Keep policy and implementation centralized in ROCm/rockrel.

## Non-goals

- Automatically marking generated pull requests ready for review.
- Automatically approving, merging, or enabling auto-merge.
- Resolving cherry-pick conflicts automatically.
- Inferring dependency ordering from prose.
- Creating a second TheRock pin update for a component-repository request.
- Replacing Jira, GitHub branch rules, CI, or required reviewers.

## Users

- **Express Train operator:** configures trains, applies labels, reviews results,
  and decides when generated drafts are ready.
- **Source PR author:** receives status and remediation details on the original
  pull request.
- **Repository maintainer:** reviews the generated draft under the target
  repository's normal branch rules.
- **Automation administrator:** installs the GitHub App, manages secrets, and
  disables or replays automation.

## User experience

### Request

An operator applies a repository label with this exact form:

```text
express-train:<train-id>
```

Example:

```text
express-train:10.1-20260811
```

The train ID is stable operator-facing vocabulary. A version-controlled rockrel
configuration maps it to exact repository branches and the Jira Fix Version.

Labels may be applied before merge or after merge. A valid post-merge label is
an explicit recovery request and runs immediately.

### Qualification

A request qualifies only when all of the following are true:

1. The label maps to an active train.
2. The source repository is TheRock, rocm-systems, or rocm-libraries.
3. The source PR base is `main` for TheRock or `develop` for the component
   repositories.
4. The actor that applied the label currently has `write`, `maintain`, or
   `admin` permission in that repository.
5. The source PR is merged and its aggregate merge commit is available from the
   canonical repository.
6. At least one ROCm Jira issue referenced by the PR has the exact Fix Version
   configured for the train.
7. The configured target branch exists and is governed by pull-request rules.

An open qualifying PR receives `waiting_for_merge`. Closing without merge or
removing the label records `cancelled`.

### Outcomes

Each source PR/train pair has exactly one current outcome:

| Status | Meaning | Write behavior |
| --- | --- | --- |
| `waiting_for_merge` | Request is valid but the source is open | None |
| `invalid` | A deterministic policy rule failed | Remove invalid train label |
| `blocked` | Required evidence or a service is temporarily unavailable | None; retain label |
| `already_contained` | Exact target already contains the change | None |
| `covered_by_existing_pr` | An existing target PR covers the change | None |
| `cherry_pick_required` | Read-only planning proved a clean change is needed | None |
| `draft_created` | A draft target PR exists | Draft only |
| `manual_resolution_required` | Conflict or ambiguous repository state | None |
| `cancelled` | Request was removed or PR closed without merge | None |

Automation updates one sticky source-PR comment instead of adding a comment for
every delivery. The comment identifies the train, decision, evidence summary,
workflow run, and generated or covering PR when applicable.

## Safety requirements

- Generated pull requests are always drafts.
- The automation has no code path or permission request for ready-for-review,
  approval, merge, or auto-merge operations.
- A conflict is never treated as evidence that the change is present.
- Every decision uses freshly fetched canonical refs.
- Before push, the target head is fetched again. One target movement causes a
  complete recomputation; a second movement stops the run safely.
- `pull_request_target` workflows never check out or execute the source PR head.
- Temporary GitHub or Jira failures retain the label and create no branch.
- Dry-run and shadow modes cannot acquire write credentials.

## Configuration and lifecycle

Each train declares an ID, Jira Fix Version, state, mode, and per-repository
source and target branches. Supported states are `active` and `inactive`.
Supported modes are `disabled`, `validate`, `shadow`, and `create-draft`.

Activating a train synchronizes its label to the three supported repositories.
Inactivating a train prevents new requests without deleting historical labels
or comments.

## Success criteria

- Applying a valid label to a merged qualifying PR creates exactly one draft
  against the configured target branch.
- Replaying the same request has no additional write effect.
- Invalid requests explain the exact failed rule.
- Existing or covering changes do not produce duplicate PRs.
- Conflicts produce no remote branch.
- All seven 0811 reference cases resolve deterministically, including TheRock
  PR 7282 being associated with its existing covering descendant-pin PR.
- A later nightly or similarly named release never substitutes for the exact
  configured target.
- Unit, Git integration, contract, workflow, and security tests pass.

## Rollout

Rollout progresses through `validate`, `shadow`, and `create-draft`. The first
live write is a controlled pilot. Expanding writes requires operator review of
the pilot result. Production enablement does not relax the draft-only rule.
