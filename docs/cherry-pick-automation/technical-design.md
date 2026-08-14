# Express Train cherry-pick automation: technical design

## Architecture

The system uses thin event workflows in TheRock, rocm-systems, and
rocm-libraries and a reusable implementation workflow in rockrel. Source
workflows listen to `pull_request_target` events `labeled`, `unlabeled`, and
`closed`. They pass immutable event metadata to a rockrel workflow referenced by
a full commit SHA.

The reusable workflow checks out rockrel itself at the same explicitly supplied
SHA and invokes a standard-library Python CLI. It does not check out an
unmerged pull-request head. A separate scheduled workflow reconciles merged,
labeled PRs after missed deliveries or abandoned covering PRs.

Planning and reconciliation authenticate with a GitHub App installation token
whose requested permissions are explicitly narrowed to read-only access. The
workflow's built-in `GITHUB_TOKEN` is not used for cross-repository API calls.
Event feedback and draft creation are separate jobs with separate, narrowly
scoped tokens and mode gates.

## Components

- `scripts/express_train/`: policy, GitHub/Jira clients, git decision engine,
  orchestration, and CLI.
- `config/express-trains.json`: version-controlled train definitions.
- `.github/workflows/express_train_cherry_pick.yml`: reusable and manual entry
  point.
- `.github/workflows/express_train_reconcile.yml`: scheduled/manual recovery.
- `.github/workflows/express_train_sync_labels.yml`: label provisioning.
- A small `.github/workflows/express_train_request.yml` caller in each source
  repository.

## Configuration contract

The root JSON object has `schema_version` and `trains`. Each train has:

```json
{
  "id": "10.1-20260811",
  "jira_fix_version": "10.1.0a20260811",
  "state": "active",
  "mode": "validate",
  "repositories": {
    "ROCm/TheRock": {
      "source_branch": "main",
      "target_branch": "release/bkc/therock-10.1-20260811"
    }
  }
}
```

Repository keys must be unique and drawn from the allowlist. Train IDs must
match `[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}`. Target branches must begin with
`release/`. Duplicate labels or repository mappings are rejected at load time.

## Command contract

The module exposes:

```text
python -m scripts.express_train plan --source-pr URL --train ID --repo-dir PATH
python -m scripts.express_train create-draft --source-pr URL --train ID --repo-dir PATH
python -m scripts.express_train reconcile --train ID --repo-dir OWNER/REPO=PATH
python -m scripts.express_train reconcile --train ID --repo-dir OWNER/REPO=PATH --create-drafts
python -m scripts.express_train sync-labels --train ID
python -m scripts.express_train publish-result --result-file FILE
```

Commands write one JSON result to stdout and diagnostics to stderr. `plan` is
read-only. `create-draft` checks the configured mode and refuses to write unless
it is `create-draft`. Reconciliation delegates each discovered request through
the same planner and, only in its separately gated write phase, the same draft
writer; it has no separate decision logic. `publish-result` validates a trusted
plan artifact before updating the sticky source-PR comment.

The result contains `status`, `reason_code`, source and target identifiers,
fresh ref SHAs, Jira evidence, containment evidence, optional covering or
created PR URL, and a workflow correlation ID.

## Event handling

1. Parse the repository, PR number, action, label, and sender from the event.
2. Ignore labels outside the `express-train:` namespace.
3. Resolve the named train from the pinned configuration.
4. For `unlabeled` or an unmerged `closed`, update the sticky status to
   `cancelled`.
5. Validate the label actor from the PR timeline and current repository
   permission.
6. Validate source base, Jira Fix Version, and exact target branch.
7. If open, report `waiting_for_merge`; if merged, build a plan.
8. In `validate` or `shadow`, report the plan without obtaining write tokens.
9. In `create-draft`, execute only a `cherry_pick_required` plan.

Deterministic validation failures may remove the train label. Transport errors,
rate limits, timeouts, and unavailable evidence return `blocked` and retain it.

## Git decision engine

All Git operations use argument arrays with `shell=False`. The engine creates a
disposable clone or worktree and fetches the exact canonical source commit and
target ref.

Decision order:

1. Verify the source PR is merged and identify its aggregate merge commit.
2. Fetch the exact target head.
3. Search target PRs for an idempotency marker or deterministic automation
   branch.
4. Test whether the source is already reachable from the target.
5. For ordinary commits, test a no-commit cherry-pick in a disposable worktree.
6. For TheRock gitlink changes, compare old, desired, and target component pins
   directionally using component-repository ancestry.
7. Search open target PR heads for exact source markers and proven coverage.
8. Return `cherry_pick_required` only when applying the aggregate change is
   clean and non-empty.

An empty trial application is supporting evidence but does not by itself equate
arbitrary commits. A conflict returns `manual_resolution_required`. The engine
never modifies the operator's checkout.

## Idempotency and writes

The identity key is source repository, source PR number, and train ID. Generated
branches use:

```text
shared/cherry-pick/<train-id>/<source-pr-number>
```

Generated PR bodies include an HTML marker containing the identity key and
source merge SHA. Before push, the writer refetches the target and compares it
with the planned head. It recomputes once on movement and stops on a second
race.

The writer uses `git cherry-pick -x`, pushes with an expected old-object lease,
and creates a draft PR. If push succeeds but PR creation fails, replay reuses the
same branch. Existing marker, branch, draft, merged PR, or proven covering PR
prevents a duplicate.

## GitHub and Jira access

A dedicated GitHub App is installed only on rockrel, TheRock, rocm-systems, and
rocm-libraries with maximum repository permissions:

- Metadata: read
- Contents: write
- Pull requests: write
- Issues: write
- Administration: read

Jira credentials and the GitHub App Client ID/private key are organization
Actions secrets restricted to those repositories and passed by explicit name.
Every token request narrows the installation maximum further:

| Job | Token permissions |
| --- | --- |
| Plan and reconcile | Administration read, contents read, issues read, pull requests read |
| Event feedback | Issues write only |
| Draft creation | Administration read, contents write, issues write, pull requests write |
| Label synchronization | Issues write only |

The event-feedback and draft jobs are gated on the configured train mode
`create-draft`. Manual plans, `validate`, and `shadow` do not generate an App
installation token with write permissions. The built-in workflow token remains
contents-read-only in every workflow.

Scheduled reconciliation is also two-phase. Its first matrix plans every active
train with a read-only token. A second matrix contains only trains whose pinned
configuration mode is `create-draft`; it replans before invoking the shared
writer. A failed read phase prevents the write phase from starting.

## Security boundaries

- The privileged event is `pull_request_target`, but source PR code and
  artifacts are never checked out, imported, sourced, or executed.
- Reusable workflow references and the rockrel checkout use the same full SHA.
- Workflow permissions are explicit and default to read-only.
- User-controlled titles, bodies, labels, and branch names are passed as data,
  never interpolated into shell scripts.
- Logs redact Authorization, App private keys, Jira tokens, and installation
  tokens.
- Draft creation is the terminal write operation; the implementation contains
  no ready, review, merge, or auto-merge client method.
- App tokens are repository-scoped and permission-narrowed when minted. The App
  action is pinned to the immutable v3.2.0 commit.

## Testing strategy

Development is red-green-refactor. Unit tests cover configuration, policies,
events, result serialization, and client behavior. Git integration tests create
disposable bare repositories for contained, clean, conflicting, racing, and
gitlink histories. HTTP clients use injectable transports and deterministic
fixtures. Workflow contract tests parse YAML as text and actionlint validates
syntax.

The seven 0811 candidates form a regression corpus. Live validation remains
read-only until the shadow results match expected outcomes.

## Deployment

1. Merge central rockrel automation after operator review.
2. Pin source-repository callers to the immutable merged SHA.
3. Install the App and configure selected-repository secrets.
4. Activate `validate`, then `shadow`, then one `create-draft` pilot.
5. Disable writes by changing train mode; workflow removal is not required for
   rollback.
