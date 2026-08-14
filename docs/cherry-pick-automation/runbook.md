# Express Train cherry-pick automation runbook

## Operating principles

- Labels request work; they do not approve or merge it.
- Every generated pull request remains a draft until an operator acts.
- Use `plan` before replaying any failed write.
- Do not interpret a conflict as proof that a change is already present.
- Disable a train in configuration to stop new work without deleting evidence.

## Add a train

1. Add a unique train entry to `config/express-trains.json` in `validate` mode.
2. Confirm exact source and target branches for all enabled repositories.
3. Confirm the Jira Fix Version spelling with Jira.
4. Run the configuration and workflow test suites.
5. Review and merge the configuration pull request.
6. Dispatch the label synchronization workflow for the train.
7. Apply the label to a non-production fixture PR and inspect the sticky status.
8. Promote to `shadow`, then `create-draft` only after operator review.

## Install the GitHub App

Use `config/express-train-github-app-manifest.json` as the reviewed permission
source. Create a private organization-owned App with webhooks disabled, install
it only on rockrel, TheRock, rocm-systems, and rocm-libraries, and confirm the
installed permissions exactly match the manifest. Do not add Actions or
Workflows permission.

Store its App ID and private key as selected-repository organization secrets
named `ROCM_CHERRYPICK_APP_ID` and `ROCM_CHERRYPICK_APP_PRIVATE_KEY`. Store the
Jira endpoint and token as `ROCM_CHERRYPICK_JIRA_URL` and
`ROCM_CHERRYPICK_JIRA_TOKEN`. The automation must remain in `validate` mode
until a read-only credential and permission review succeeds.

## Review a generated draft

1. Confirm the source PR URL, aggregate merge SHA, Jira issue, and train ID.
2. Confirm the draft base is the exact configured release branch.
3. Review the cherry-picked diff and provenance marker.
4. Distinguish target-branch CI from checks inherited from the source PR.
5. Resolve dependency ordering with the component owner when Jira records a
   dependency.
6. An operator may mark the draft ready only after completing this review.

The automation never performs step 6.

## Replay

Run the manual workflow in `plan` mode with the source PR URL and train ID. If
the plan is `cherry_pick_required`, review the exact target head and then
dispatch `create-draft`. Replays are idempotent and return an existing branch or
PR when one is already associated with the identity key.

## Conflict

When the status is `manual_resolution_required`:

1. Download the JSON evidence artifact.
2. Reproduce the trial cherry-pick in a disposable worktree.
3. Consult the owning component team for ambiguous or diverged histories.
4. Create a separate manual draft PR when a reviewed resolution is available.
5. Add the source/train provenance marker so reconciliation recognizes it.

Do not force the automation branch or alter the source status to
`already_contained` merely because a conflict occurred.

## Covering PR closes without merge

The scheduled reconciler reevaluates labeled, merged source PRs. If a recorded
covering PR closes without merge and the change remains absent, the next run
will return `cherry_pick_required` and, in write mode, create a draft.

## Disable or roll back

Set the affected train mode to `disabled` for an immediate fail-closed stop.
Retain labels and comments for auditability. If configuration cannot be merged
quickly, disable the GitHub App installation for the affected repositories or
remove access to the write environment. Do not delete generated branches or
draft PRs automatically.

## Credential rotation

1. Generate the replacement GitHub App key or Jira token.
2. Update the selected-repository organization secret.
3. Run a read-only validation workflow.
4. Revoke the previous credential.
5. Verify logs and artifacts contain no secret material.

## Operator handoff checklist

- Product requirements and technical design match deployed behavior.
- Final unit, integration, workflow, and security tests pass.
- Shadow results are attached for representative requests.
- App installation repositories and permissions are recorded.
- Train configuration, Jira Fix Version, and exact target branches are recorded.
- Generated implementation and pilot pull requests are still drafts.
