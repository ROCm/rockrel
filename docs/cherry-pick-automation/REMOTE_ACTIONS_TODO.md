# Draft — explicit operator approval required

# Remote actions TODO

Nothing in this file is authorized by the local implementation phase. Do not
execute any item until a human has reviewed the complete local diff and granted
specific approval for a later remote phase.

No remote write is part of the current implementation. The explicitly
requested `local-materialize` validation used read-only GitHub metadata and Git
fetches; it did not push or mutate GitHub. Any future corpus refresh is a
separate reviewed action and does not authorize GitHub mutation.

## Existing stopped attempt

- [ ] Read-only operator verification of the state of the previously stopped
  rockrel, TheRock, rocm-systems, and rocm-libraries public attempts.
- [ ] Decide whether any separate cleanup is required; do not perform cleanup
  from the local implementation workspace.

## Public review

- [ ] Review the rockrel and three caller-repository local diffs, red/green evidence,
  coverage, threat model, permissions, generated workflow diffs, and the
  fresh-checkout `local-materialize` operator contract.
- [ ] Confirm the cherry-pick integration gives Release Hub no writer, queue,
  label, or GitHub App permission; review its read-only train adapter and the
  separate Developer Central self-service-token feature flag.
- [ ] Review the 17-case fast selection, all 77 schema-v2 golden expectations,
  the schema-v3 deep report, 21 historical-only gaps, and every named synthetic
  coverage claim.
- [x] Rebuild the checked-in review bundle and rerun the complete local matrix.
  Python 3.10.20, 3.11.15, and 3.12.13 each pass 1,036/1,036 with bundle equality
  included; bundle/AST/Marketplace qualification passes 37/37 and local
  structural validation reports `Skill is valid!`. The deliberately
  nonpublishable bundle records `dirty_worktree_review` at base
  `8432a05b8c081df871d426525728de39569ff3cb`, source digest
  `ce9fcd327f311357359bf8f86db88971caf733a042a90e95682ef38b56c1159b`.
- [ ] After review and commit, rebuild again from the clean reviewed revision and
  require bundle manifest v2 `source_provenance.state=clean_commit`; a
  `dirty_worktree_review` bundle is nonpublishable.
- [ ] Reproduce the locally passing independent 95% line / 90% branch gate in an
  approved public CI environment on Python 3.10, 3.11, and 3.12; treat any
  difference as blocking.
- [ ] Approve or reject publishing each repository independently.
- [ ] If approved, push new reviewed branches without reusing old temporary
  branches.
- [ ] Open new PRs as drafts and leave them draft through operator and owner
  review.
- [ ] Run public repository CI only after explicit approval.

## GitHub Apps and repository configuration

- [ ] Review the separate executor App maximum of metadata read plus contents,
  pull requests, issues, and Checks write; do not grant administration,
  Actions, Workflows, deployments, members, or secrets permission.
- [ ] Create/install the executor App only on specifically approved repositories.
- [ ] Record the created executor App's exact numeric ID in reviewed train
  configuration; keep it null in local/`validate` review and never substitute a
  slug, login, or label-writer App ID.
- [ ] Install repository/environment secrets after security review; keep read
  and write job tokens short-lived, repository-scoped, and permission-reduced.
- [ ] Add or modify train labels only after reviewing the exact label set.
- [ ] Keep `trusted_app_ids` empty unless an independent GitHub-native label
  principal is separately designed, reviewed, and approved. Release Hub must
  not become that writer.
- [ ] Configure required rockrel checks only through a reviewed ruleset change.
- [ ] Using separately authorized GitHub administration, verify the OIDC subject
  configuration for TheRock, rocm-systems, rocm-libraries, and rockrel. GitHub's
  [official OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
  states that repositories created before 2026-07-15 require opt-in to immutable
  repository identities in the subject.
- [ ] Where required, explicitly enable the immutable subject template and retain
  redacted proof. Pin owner `ROCm` ID `21157610` and repository IDs TheRock
  `765605091`, rocm-systems `962090208`, rocm-libraries `971570345`, and rockrel
  `1071689640`. Reject missing/invalid IDs and every legacy name-only subject;
  do not add a fallback. This unchecked item is a production blocker and is not
  authorized by this TODO.

## Caller and workflow deployment

- [ ] Record the immutable reviewed rockrel SHA.
- [ ] Render and review each caller pinned to that exact SHA.
- [ ] Treat the currently rendered
  `c53e703568fe41129abf7139f018ac920bca9c59` caller SHA as stale test input;
  do not activate or publish callers until the reviewed rockrel commit exists.
- [ ] Review the implemented but disabled remote transaction steps and their
  write-time revalidation evidence before changing any mode or predicate.
- [ ] Merge the central workflow before merging callers that reference it.
- [ ] Dispatch no workflow until configuration and credentials are reviewed.

## Controlled rollout

- [ ] Review the exact private repository allowlist and bind every entry to its
  immutable numeric repository ID. The local 26/26 security contract now uses
  the real production repository IDs in the denylist: TheRock `765605091`, rocm-systems `962090208`,
  rocm-libraries `971570345`, and rockrel `1071689640`—instead of placeholders.
  Prove those IDs cannot pass the live harness gate.
- [ ] Verify the exact sandbox sentinel name and value in each allowlisted repository.
  Restrict every generated head to the reviewed sandbox-only branch prefix.
- [ ] Confirm the checked-in harness CLI remains prepare-only and contains no
  remote scenario executor. A production-parity sandbox executor adapter is not
  implemented; design and test it without weakening production semantics before
  any real run. Separately authorize the exact injected executor,
  repository, scenarios, credentials, and cleanup window before a real run.
- [ ] Retain a redacted evidence artifact for every scenario and verify cleanup
  without deleting or altering any non-sandbox branch, PR, or repository data.
- [ ] In an approved private sandbox, reproduce the frozen #9716 prerequisite
  chain with a canonical full commit leaf and verify each prerequisite is
  evaluated against the configured destination before the root.
- [ ] Create a ready, human-authored open PR with exact source attribution and
  planned tree; verify the executor returns `covered_by_existing_pr` and creates
  no branch, draft, Check, or comment unless feedback was separately approved.
- [ ] Exercise unrelated, unattributed-equal-tree, attributed-wrong-tree, and
  multiple-exact open PRs; verify unrelated candidates are ignored and every
  ambiguous case blocks without mutation.
- [ ] Change an open destination PR after planning but before the writer's first
  push; verify `coverage_snapshot_moved_during_write` and zero ref/PR writes.
- [ ] Review and approve or remove the local schema-v5 #9716 override, including
  its exact URLs, rationale, train scope, owner, and retirement condition.
- [ ] Run approved manual `validate` cases and retain artifacts.
- [ ] Promote one train to `shadow` through a reviewed change.
- [ ] Confirm shadow produced no branch/PR; separately approve any public Check
  feedback before enabling it.
- [ ] Review representative squash, merge-commit, rebase, dependency, conflict,
  containment, and recovery evidence.
- [ ] Approve one low-risk `create-draft` pilot through a separate reviewed
  configuration change.
- [ ] Confirm the qualifying label creates exactly one draft and does not mark
  it ready or merge it.
- [ ] Require independent operator confirmation of source changeset, label
  authorization, destination, dependencies, diff, and native CI before anyone
  marks it ready.

## Future corpus maintenance

- [ ] Approve any new read-only refresh separately and review the exact official
  repository/branch refspecs before execution.
- [ ] Generate the candidate outside the repository and compare it with the
  tracked golden; never overwrite or auto-promote the golden.
- [ ] Review every added, removed, reclassified, endpoint-changed, expectation,
  and fast/deep-tier change before committing an updated corpus.
- [ ] Re-run unit, fast, deep parallel, deep serial, byte-comparison, and
  rollback gates before accepting a refreshed corpus.

## SLAI and Developer Central follow-up

- [x] Reconcile the separate Release Watch strategy/navigation diff without
  weakening its assertions. The 2026-08-21 complete `npm run verify` rerun exits
  zero.
- [x] Recheck the earlier dependency findings without running a bulk audit fix.
  The 2026-08-21 rootless rebuild reports zero npm vulnerabilities in both
  production dependency installations.
- [x] Run the repository-local structural validator. It reports
  `Skill is valid!` for the generated bundle.
- [x] Remove the stale August 20 compliance metadata and report from the rebuilt
  August 21 bundle. The builder now strips all scanner-owned results before
  rehashing; do not restore or reuse an older `PASSED` result.
- [ ] Run the hosted SLAI author validator on an approved AMD host that provides
  `/tool/sysadmin/scripts/query_ad`, and verify the declared author NTID without
  using `--bypass-author-check`.
- [ ] Configure `AMD_LLM_API_KEY` through the approved secret path, rerun the
  mandatory SLAI security scan, review every finding individually, and commit
  no secret or generated credential. Do not use a scan bypass.
- [ ] After both prerequisites pass, rerun the SLAI submission command with
  `--dry-run --no-version-bump` and review the exact package inventory. A dry
  run still does not authorize submission.
- [ ] Review and merge the Developer Central API-token page and capability
  contract.
- [ ] Deploy only after separate authorization; verify the production token
  pepper, migration, signed identity, feature flag off state, and readiness.
- [ ] Preview `developer-central.api-tokens` for named reviewers, then perform
  the approved rollout and rollback checks.
- [ ] Review the clean-commit `rocm-cherry-pick` SLAI bundle, exact source
  closure digest, generated-file hashes, tests, security findings, and submission
  dry-run. Reject `dirty_worktree_review` provenance.
- [ ] Confirm the submitted v1 package contains only `auth`, `plan`, and
  `materialize`; reject any remote writer or draft-creation command.
- [ ] Submit v1.0.0 through the hosted SLAI Asset Submission flow only after
  explicit approval. Do not use local GitHub credentials to open the
  Marketplace PR.
