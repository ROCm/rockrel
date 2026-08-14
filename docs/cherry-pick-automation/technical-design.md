# Label-driven cherry-pick automation: technical design

## Architecture

The system is a generic, configuration-driven cherry-pick controller. Thin
event workflows in TheRock, rocm-systems, and rocm-libraries listen for label
changes and call an immutable reusable workflow in rockrel. A train is resolved
from its exact configured label and supplies the destination release branch.
No workflow, module, marker, or App identity is Express Train-specific.

The reusable workflow checks out rockrel at the same full SHA supplied by the
caller and invokes a standard-library Python CLI. It never checks out an
unmerged PR head. Scheduled reconciliation repeats the same planner for missed
events and abandoned generated PRs.

Planning uses a permission-narrowed read-only GitHub App token. Event feedback,
label synchronization, and draft creation use separate tokens with only their
required permissions and mode gates.

## Components

- `scripts/cherry_pick/`: models, configuration, policy, GitHub/Jira clients,
  Git decisions, orchestration, and draft writer.
- `config/cherry-pick-trains.json`: schema-versioned train catalog.
- `config/cherry-pick-github-app-manifest.json`: maximum App permissions.
- `.github/workflows/cherry_pick.yml`: reusable/manual plan and draft entry.
- `.github/workflows/cherry_pick_reconcile.yml`: scheduled/manual recovery.
- `.github/workflows/cherry_pick_sync_labels.yml`: configured-label provisioning.
- `templates/cherry_pick_request.yml`: generated caller used by each source repo.
- `scripts/render_cherry_pick_workflow.py`: immutable caller renderer.

## Configuration contract

Schema version 2 separates train identity, label, destination, and optional
policy:

```json
{
  "schema_version": 2,
  "trains": [
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
        },
        "ROCm/rocm-systems": {
          "source_branch": "develop",
          "destination_branch": "release/bkc/therock-10.1-20260811"
        }
      }
    }
  ]
}
```

Train IDs match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Labels must be unique,
equal `cherry-pick:<train-id>`, and use the reserved namespace. Repository keys
come from the supported allowlist. Source branches and destination branches are
validated as safe Git ref components; destination branches must start with
`release/`. `requirements` is optional. Its currently supported key is
`jira_fix_version`; unknown requirements fail configuration loading.

The catalog resolves both `train(id)` and `train_for_label(label)`. Workflows
may discover namespaced labels from event metadata, but policy always verifies
the exact configured label from the canonical PR response.

## Command contract

```text
python -m scripts.cherry_pick plan --source-pr URL --train ID --repo-dir PATH
python -m scripts.cherry_pick create-draft --source-pr URL --train ID --repo-dir PATH
python -m scripts.cherry_pick reconcile --train ID --repo-dir OWNER/REPO=PATH
python -m scripts.cherry_pick reconcile --train ID --repo-dir OWNER/REPO=PATH --create-drafts
python -m scripts.cherry_pick sync-labels --train ID
python -m scripts.cherry_pick publish-result --result-file FILE
```

Commands emit one JSON result on stdout and diagnostics on stderr. `plan` is
read-only. `create-draft` refuses writes unless both the request and pinned
train mode allow them. `reconcile` uses the same planner/writer. `publish-result`
validates a trusted result artifact before updating source-PR feedback.

The result uses `destination_branch` rather than `target_branch` and includes
status, reason, source/train identifiers, canonical SHAs, optional policy
evidence, coverage evidence, and an optional covering or generated PR URL.

## Event handling

1. Read only event action, label name, PR URL, and current label names.
2. Ignore labels outside `cherry-pick:`.
3. Convert each namespaced label to a train ID and resolve the exact configured
   label in the pinned catalog.
4. Fetch the canonical PR and verify the configured label is still present.
5. Treat `unlabeled` as cancellation only when that specific train label is
   absent.
6. Validate label actor, source branch, optional policy, and destination branch.
7. If merged, evaluate containment, existing coverage, and trial application.
8. In `validate` or `shadow`, publish artifacts without minting write tokens.
9. In `create-draft`, write only a `cherry_pick_required` plan.

Deterministic failures may remove only the affected label. Transport failures,
rate limits, and unavailable evidence return `blocked` and retain it.

## Policy model

Core policy is common to every train: active configuration, supported repo,
configured source, authorized label actor, canonical merge commit, existing
protected destination, and safe evidence.

Optional policy is applied only when declared. For
`requirements.jira_fix_version`, the planner extracts ROCm Jira keys from the
source title/body and requires one exact matching Fix Version. With the
requirement omitted, Jira credentials and Jira calls are not needed for that
decision. Additional policy types require a schema change and tests.

## Git decision engine

All Git operations use argument arrays with `shell=False` and disposable
worktrees. The engine:

1. Resolves the aggregate source commit and exact destination head.
2. Checks exact ancestry.
3. Finds open or merged PRs owning the source/train identity.
4. Proves ordinary patch or gitlink coverage where possible.
5. Runs a no-commit aggregate cherry-pick against the destination.
6. Returns contained, covered, clean-required, conflict, or blocked evidence.

An empty trial application is positive patch-equivalence evidence; a conflict
is never containment. Gitlink coverage requires directional ancestry or common
`cherry picked from` provenance. A closed-unmerged PR is not coverage.

## Idempotency and writes

The identity key remains source repository, source PR number, and train ID:

```text
shared/cherry-pick/<train-id>/<source-pr-number>
```

Generic HTML markers use `cherry-pick`, not `express-train`. Before pushing, the
writer refetches the destination and compares it with the planned head. The
workflow's initial read plan and write-job replan tolerate one movement; a
movement after the write replan stops safely.

The writer uses `git cherry-pick -x`, a creation lease for the deterministic
branch, and GitHub's `draft: true` field. It has no method for ready-for-review,
approval, merge, or auto-merge.

## GitHub and Jira access

The private GitHub App maximum is administration read, contents write, issues
write, and pull requests write. Each token request narrows that maximum:

| Job | Token permissions |
| --- | --- |
| Plan and reconcile-read | Administration read, contents read, issues read, pull requests read |
| Event feedback | Issues write only |
| Draft creation and reconcile-write | Administration read, contents write, issues write, pull requests write |
| Label synchronization | Issues write only |

The built-in workflow token remains contents-read-only. App Client ID/private
key and Jira credentials are explicit selected-repository secrets. A train with
no Jira requirement performs no Jira request, though the first workflow version
may still receive the named secrets until secret inputs are split in a later
compatible revision.

## Security boundaries

- Privileged event workflows never execute PR-head code or artifacts.
- Reusable workflow reference and automation checkout use one full SHA.
- User-controlled strings are data, never shell source.
- Tokens are repository-scoped and permission-narrowed.
- Logs and artifacts do not expose credentials.
- Draft creation is the terminal remote write.
- `validate`, `shadow`, and manual plan cannot mint write tokens.

## Testing strategy

Development remains red-green-refactor. Tests cover schema v2, configurable
labels, optional Jira policy, pure qualification, Git integration, clients,
orchestration, recovery, workflow token boundaries, rendering, and absence of
legacy Express Train identifiers. Each source repository has a local caller
contract test. The seven 0811 cases remain a data fixture for one train.

## Migration

This implementation has not been deployed, so migration is a local atomic
rename rather than a compatibility period:

1. Commit this PRD and design before implementation.
2. Add failing schema/naming/optional-policy tests.
3. Rename central modules, workflows, renderer, template, config, tests, and App
   manifest to generic cherry-pick names.
4. Change configured labels from `express-train:` to `cherry-pick:`.
5. Render and test all three callers at the final local rockrel SHA.
6. Keep all public deployment actions in the operator TODO.

## Deployment

1. Review and merge central rockrel automation after local approval.
2. Mark rockrel `Unit Tests` required through a reviewed ruleset change.
3. Re-render callers at the immutable merged SHA and review draft PRs.
4. Install/configure the App and synchronize configured labels.
5. Validate, shadow, then pilot one destination train in `create-draft` mode.
6. Disable a train through configuration to stop new writes.
