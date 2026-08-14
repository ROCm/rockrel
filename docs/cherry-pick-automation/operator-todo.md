# Express Train automation: operator GitHub TODO

This is the complete queue of public GitHub actions. None of these actions are
authorized during local review. Check off an item only after an operator has
reviewed the local commits, test evidence, and exact target repository.

## Audit of the stopped public attempt

- [ ] Confirm ROCm/rockrel#93 is closed and its temporary remote branch is gone.
- [ ] Confirm ROCm/TheRock#7382 is closed, remains a draft, and its temporary
  remote branch is gone.
- [ ] Confirm ROCm/rocm-systems#10185 is closed, remains a draft, and its
  temporary remote branch is gone.
- [ ] Confirm ROCm/rocm-libraries#10824 is closed, remains a draft, and its
  temporary remote branch is gone.

These checks are read-only. Do not reopen or reuse those PRs during review.

## Review and central deployment

- [ ] Review the local rockrel branch, red/green commit history, threat model,
  App manifest, workflow permissions, and final test report.
- [ ] Decide whether the implementation is approved for a public review branch.
- [ ] If approved, push a new reviewed rockrel branch; do not reuse a deleted
  temporary branch.
- [ ] Open a draft rockrel PR and keep it draft through operator review.
- [ ] Run repository CI and security review on the draft.
- [ ] Merge the central PR only after normal rockrel approval.
- [ ] Record the immutable merged rockrel commit SHA.
- [ ] After the `Unit Tests` check exists on rockrel's default branch, configure
  branch protection/rulesets to make `Unit Tests` a required status check.
- [ ] Verify branch protection rejects a fixture PR whose `Unit Tests` check
  fails before relying on the requirement for Express Train changes.

## GitHub App and credentials

- [ ] Create the private, webhook-disabled GitHub App from
  `config/express-train-github-app-manifest.json`.
- [ ] Install it only on ROCm/rockrel, ROCm/TheRock, ROCm/rocm-systems, and
  ROCm/rocm-libraries.
- [ ] Verify the installation maximum is exactly administration read, contents
  write, issues write, and pull requests write; do not grant Actions or
  Workflows permission.
- [ ] Create selected-repository organization secrets
  `ROCM_CHERRYPICK_APP_CLIENT_ID` and
  `ROCM_CHERRYPICK_APP_PRIVATE_KEY` for those four repositories.
- [ ] Create selected-repository organization secrets
  `ROCM_CHERRYPICK_JIRA_URL` and `ROCM_CHERRYPICK_JIRA_TOKEN` for those four
  repositories.
- [ ] Verify a read-only token request before enabling any write-mode train.

## Source-repository callers

- [ ] Render each caller with the recorded immutable merged rockrel SHA.
- [ ] Review the rendered TheRock caller locally, then push a new branch and
  open a draft PR if approved.
- [ ] Review the rendered rocm-systems caller locally, then push a new branch
  and open a draft PR if approved.
- [ ] Review the rendered rocm-libraries caller locally, then push a new branch
  and open a draft PR if approved.
- [ ] Keep all three PRs draft until operator and repository-owner approval.
- [ ] Merge callers only after the central workflow SHA is reachable publicly.

## Controlled rollout

- [ ] Dispatch label synchronization and review the exact three label changes.
- [ ] Run manual `plan` in `validate` mode on approved fixture PRs and retain
  the JSON artifacts.
- [ ] Compare fixture outcomes with the seven-case 0811 regression report.
- [ ] Promote the train to `shadow` through a reviewed configuration PR.
- [ ] Review shadow evidence; confirm it minted no write token.
- [ ] Select one low-risk pilot and promote only that reviewed train to
  `create-draft` through a configuration PR.
- [ ] Apply the train label to the approved pilot source PR.
- [ ] Confirm exactly one target PR is created and that it remains a draft.
- [ ] Do not mark any generated draft ready until an operator separately
  confirms the source SHA, Jira Fix Version, exact base branch, diff, CI, and
  dependency ordering.
- [ ] Do not enable auto-merge; the automation has no merge authority.
