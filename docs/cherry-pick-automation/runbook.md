# Draft — local review required

# Label-driven cherry-pick automation runbook

## Current operating boundary

This runbook is design documentation only. The automation is not deployed.
During ordinary local tests, do not fetch, push, call GitHub/Jira/Release Hub,
dispatch workflows, or mutate any remote state. Use filesystem repositories and
fake transports. The explicit operator-invoked `local-materialize` flow below
may perform read-only GitHub REST calls and Git fetches; it has no remote writer.
A future corpus refresh still requires separate approval. Queue all remote
actions in `REMOTE_ACTIONS_TODO.md`.

## Operating principles

- A label requests evaluation; it never approves or merges a change.
- Every future generated pull request starts and remains a draft until a person
  acts.
- Only the exact configured destination branch is authoritative.
- A nightly/build occurrence is not destination containment evidence.
- A conflict, partial match, or ambiguous history is never containment.
- Exact `Depends-On:` PR/full-commit trailers plus additive reviewed train
  overrides form a bounded DAG. Standalone commits are leaf nodes. A valid
  unmet prerequisite waits; a malformed, cyclic, unsupported, or ambiguous
  graph blocks.
- One exact open manual or automation PR suppresses duplicate work only after
  Git proves destination ancestry, source attribution, and the planned tree.
  CI and semantic readiness remain human review inputs.
- Jira is not part of the core or GitHub executor. The pure Git core has no
  Release Hub dependency; the surrounding control plane and Marketplace adapter
  use only its authenticated read-only configuration endpoint. Release Hub never
  applies or removes a label for this product.
- The automation never force-pushes, deletes branches, closes drafts, marks
  ready, approves, merges, or enables auto-merge.

## Local review procedure

1. Inspect the PRD, technical design, audit, and complete local diff.
1. Confirm the TDD evidence shows the complete remediation suite failing before
   implementation and passing afterward.
1. Run unit and integration tests with local filesystem repositories and fake
   API transports.
1. Run repository-native formatting, pre-commit, actionlint, JSON/Markdown, SPDX,
   coverage, and diff checks using already available local tooling.
1. Inspect rendered source callers without publishing them.
1. Confirm initial train configuration is `validate` and the local safety gate
   cannot construct a real writer.
1. Record missing tools or unavailable gates as limitations; do not download or
   invoke a remote service to hide them.

## Local-only materialization from a fresh checkout

The packaged `plan` and `materialize` commands are the supported exception
to the fake-transport-only development rule above. They authenticate to
Developer Central for one complete read-only configuration snapshot, perform
read-only GitHub REST requests through the operator's existing `gh` session,
and fetch exact refs from the supplied clones. They cannot push, label,
comment, dispatch a workflow, or create a pull request. The engine and
materializer remain pure Git and standard-library Python; `gh` is only the
read adapter that resolves a PR URL to immutable metadata.

Prerequisites are Python 3.10 or newer, Git, GitHub CLI authenticated to
`github.com`, the reviewed installed skill/bundle, a Developer Central
`read:evidence` token, and one fresh clone per repository named by the
request's prerequisite graph. No rockrel checkout, virtual environment,
third-party Python package, GitHub App credential, Jira client, or LLM is
required. Complete the skill's first-login flow before this quick start. From
an empty parent directory:

```bash
git clone https://github.com/ROCm/rocm-systems.git
gh auth status
mkdir -p /absolute/path/to/disk-backed/cherry-pick-scratch

python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth status

python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py plan \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/path/to/disk-backed/cherry-pick-scratch \
  > /absolute/path/to/reviewed-plan.json

python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py materialize \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/path/to/disk-backed/cherry-pick-scratch \
  --output-repo /absolute/path/to/rocm-systems-pr-10031-local \
  --branch local/cherry-pick/10.1-20260811/10031
```

The output repository must not already exist. On success, the JSON status is
`local_materialized`, the final `tree` equals `planned_tree`, and `commands`
contains the exact cherry-pick transcript. The output is an independent local
clone whose push URL is `disabled://local-only`. This command never creates a
PR body; if a separately authorized future `local-create-draft` or App writer
is used, the shared renderer places the same executed command list in the draft
PR description.

## Future local gh operator path (remote writes require explicit approval)

This future rockrel operator path is excluded from the SLAI Marketplace source
closure. The Marketplace package contains only read-only Developer Central
HTTPS, GitHub metadata through `gh`, Git fetch, and local Git operations, and
exposes only `auth`, `plan`, and `materialize`.

The rockrel CLI separates credential source from the Git core. `--auth gh` resolves a
github.com REST token with `gh auth token` and configures Git to invoke
`gh auth git-credential`; it never writes the token into a command, artifact,
PR body, or process environment used by Git. It is rejected inside GitHub
Actions and for non-github.com hosts.

For any production-like exercise, an approved control-plane step must fetch the
complete Developer Central snapshot and record its returned SHA-256. Never
substitute the source catalog, a cached response, or a hand-written branch.
The project CLI validates the snapshot and digest before reading GitHub.

Create a read-only plan artifact first:

```bash
python3 -m scripts.cherry_pick \
  --config-snapshot /absolute/path/to/developer-central-config.json \
  --expected-config-sha256 CONFIG_SHA256 \
  --auth gh \
  plan \
  --source-pr https://github.com/ROCm/OWNER/pull/NUMBER \
  --train TRAIN_ID \
  --repo-dir ROCm/OWNER=/absolute/path/to/repository \
  > /absolute/path/to/reviewed-plan.json
```

Do not continue unless the user separately authorizes remote writes, the exact
artifact has been reviewed, and the train is in reviewed `create-draft` mode.
The write invocation replans and requires exact identity and critical evidence
before issuing a one-plan local capability:

```bash
python3 -m scripts.cherry_pick \
  --config-snapshot /absolute/path/to/developer-central-config.json \
  --expected-config-sha256 CONFIG_SHA256 \
  --auth gh \
  local-create-draft \
  --source-pr https://github.com/ROCm/OWNER/pull/NUMBER \
  --train TRAIN_ID \
  --repo-dir ROCm/OWNER=/absolute/path/to/repository \
  --expected-result-file /absolute/path/to/reviewed-plan.json \
  --scratch-root /absolute/path/to/disk-backed/scratch \
  --confirm-remote-write CREATE_DRAFT
```

Both this path and the App path use `DraftWriter`. Every generated description
contains a fenced `Commands executed to create the cherry-pick` transcript
with the exact ordered commands used in the disposable worktree.

## Future train setup (requires separate approval)

1. Add a unique schema-v5 train in `validate` mode.
1. Confirm every source branch and exact destination branch.
1. Confirm effective destination rules require a pull request.
1. Confirm the human permission threshold, numeric GitHub-App allowlist,
   bounded prerequisite policy, and open-PR coverage cap.
1. Review each `prerequisite_overrides` entry as code: exact source PR,
   rationale, additive edges, canonical URLs, reachability, acyclicity, and
   commit-leaf constraints.
1. In any automated mode, configure and independently verify the executor
   App's exact numeric ID; do not use its slug, login, or display name.
1. Review and merge the configuration through normal repository review.
1. Provision labels and the executor App only after reviewing the exact
   mutations and permissions. Keep `trusted_app_ids` empty unless a separate
   label-writing principal is explicitly approved.
1. Run `validate`, then `shadow`, then a separately approved low-risk
   `create-draft` pilot.

## Review a future generated draft

1. Confirm source PR, canonical head, merged commit/range, and changeset proof.
1. Confirm train, exact base branch, and planned destination SHA.
1. Confirm the label authorization envelope and complete dependency graph.
1. Inspect every open destination PR classification. `covered_by_existing_pr`
   requires one exact covering PR; ambiguity or multiple covers must block.
1. For a continuation, confirm the exact trusted executor-App Check snapshot
   predates and matches the current head/body/graph; otherwise relabel.
1. Reproduce the application strategy and inspect `-x` provenance.
1. Review the complete diff and repository-native CI.
1. Confirm the PR remains a draft.
1. Only a human may decide to mark the PR ready.

## Replay and partial transaction

Run read-only planning first. A future retry may repair a branch-pushed/PR-missing
state only when the branch tree and identity exactly match the recomputed plan.
An existing expected draft yields `draft_exists`. Any operator modification or
tree mismatch blocks; never overwrite it.

Immediately before creating an absent branch, the writer re-lists every open
same-repository PR targeting the destination and compares its canonical digest
with the plan. Any opened, closed, retargeted, or moved PR blocks with
`coverage_snapshot_moved_during_write`; recompute instead of pushing.

## Frozen #10153 local regression

The checked-in schema-v3 core request records the complete #9716 prerequisite
sequence and exact #10153 Git identities. Replay it through the same standalone
offline CLI used by Actions:

```bash
python3 -m scripts.cherry_pick.core_cli plan \
  --manifest scripts/tests/fixtures/cherry_pick_10153_core_request.json \
  --repo ROCm/rocm-systems=/path/to/rocm-systems \
  --scratch-root /path/to/disk-backed/cherry-pick-scratch
```

The local clone must contain every commit, tree, and blob named by the
manifest. A partial clone with unhydrated promised objects must fail closed;
hydrate it through the trusted checkout/ref layer before retrying. The
local-object validation must report:

```text
root plan:       draft_planned / clean_trial_application
planned tree:    2b7467c293ea312349db32372bdc51a495fd419d
coverage:        covered_by_existing_pr / exact_existing_pull_coverage
source proof:    complete_changeset_application_ancestor
```

The three prerequisite objects before #9716 must be ancestors of destination
`800045c8ab865991f4cec1549de2bb44e76b9904`. This is a local Git proof only. Do
not query GitHub, push, or create another PR while running the regression.

## Local architecture and integration qualification

Build into a new, nonexistent directory. A publishable build requires a clean
reviewed commit and therefore omits `--allow-dirty-review`:

```bash
.venv/bin/python scripts/build_cherry_pick_skill.py \
  --root /absolute/path/to/rockrel \
  --output /absolute/path/to/new/rocm-cherry-pick-clean
```

For local review of dirty source bytes only, use a different new output path:

```bash
.venv/bin/python scripts/build_cherry_pick_skill.py \
  --root /absolute/path/to/rockrel \
  --output /absolute/path/to/new/rocm-cherry-pick-dirty-review \
  --allow-dirty-review
# warning: ... dirty_worktree_review bundle; do not publish it
```

Both commands emit manifest v2 with the exact source-closure digest. The second
is never publishable; after review, commit the approved source and rebuild clean.
Do not run either command during a docs-only pass.

Render every design diagram with the immutable Mermaid CLI image and disabled
container networking:

```bash
.venv/bin/python scripts/render_cherry_pick_mermaid.py
# rendered 14 Mermaid diagrams into .../.tmp/mermaid-render
```

Validate the configured trust anchors and immutable caller pin across the five
local repositories:

```bash
.venv/bin/python scripts/check_cherry_pick_integration.py \
  --manifest config/cherry-pick-integration.json \
  --workspace-root /absolute/path/to/label-driven-cherrypick-automation \
  --release-hub-root /absolute/path/to/rocm-release-hub \
  --format json
```

This v2 checker proves cross-file equality for the configured endpoint,
numeric owner/repository IDs, immutable-only subjects, per-caller event/ref/
workflow tuple, and canonical workflow SHA. The current canonical pin is
`c53e703568fe41129abf7139f018ac920bca9c59`; it is intentionally stale relative
to the dirty rockrel working-tree HEAD. The rockrel and Release Hub behavior
suites separately prove OIDC token-request and verifier claim semantics.
GitHub immutable-subject opt-in remains an unchecked remote TODO.

Inspect the private-sandbox plan without executing a scenario:

```bash
.venv/bin/python scripts/run_cherry_pick_private_sandbox.py \
  --manifest config/cherry-pick-private-sandbox.json
# "remote_execution_enabled": false
```

The public harness CLI is prepare-only and contains no remote executor. A real
private-sandbox run remains a separately authorized action after review of the
repository allowlist, sentinel, sandbox-only branch prefix, and excluded
production repository IDs: TheRock `765605091`, rocm-systems `962090208`,
rocm-libraries `971570345`, and rockrel `1071689640`. The focused security
contract passes 26/26, but no production-parity executor or remote run exists.

## Historical replay suite

Corpus refresh is a separately approved future network-read operation. It may
write only dedicated local bare mirrors and an unreviewed candidate inventory
outside the repository; it never writes to GitHub or an existing checkout.
Ordinary replay runs only against already-hydrated local mirrors with lazy
fetching disabled.

The operator must review the inventory totals, ensure no case is unresolved,
and distinguish strict exact replays from bundles, manual resolutions, reverts,
release-native changes, and gitlink adaptations. A conflict or missing object is
never accepted as proof that the source change was already present.

Run the complete unit suite before treating the named synthetic coverage
registry as evidence:

```bash
.venv/bin/python -m pytest -q scripts/tests \
  --basetemp=/path/to/disk-backed/pytest/cherry-pick \
  --cov=scripts.cherry_pick \
  --cov=scripts.build_cherry_pick_skill \
  --cov=scripts.check_cherry_pick_integration \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=json:coverage.json \
  --cov-fail-under=90
.venv/bin/python scripts/check_cherry_pick_coverage.py coverage.json \
  --minimum-lines 95 \
  --minimum-branches 90 \
  --minimum-module-lines 90 \
  --minimum-module-branches 90 \
  --critical-module scripts/cherry_pick/__main__.py \
  --critical-module scripts/cherry_pick/action_runtime.py \
  --critical-module scripts/cherry_pick/authorization.py \
  --critical-module scripts/cherry_pick/clients.py \
  --critical-module scripts/cherry_pick/config.py \
  --critical-module scripts/cherry_pick/control_plane.py \
  --critical-module scripts/cherry_pick/control_plane_cli.py \
  --critical-module scripts/cherry_pick/core.py \
  --critical-module scripts/cherry_pick/core_cli.py \
  --critical-module scripts/cherry_pick/dependencies.py \
  --critical-module scripts/cherry_pick/feedback.py \
  --critical-module scripts/cherry_pick/git.py \
  --critical-module scripts/cherry_pick/git_auth.py \
  --critical-module scripts/cherry_pick/github_read.py \
  --critical-module scripts/cherry_pick/local_runtime.py \
  --critical-module scripts/cherry_pick/managed_stack.py \
  --critical-module scripts/cherry_pick/marketplace_cli.py \
  --critical-module scripts/cherry_pick/orchestrator.py \
  --critical-module scripts/cherry_pick/refs.py \
  --critical-module scripts/cherry_pick/release_hub.py \
  --critical-module scripts/cherry_pick/release_hub_auth.py \
  --critical-module scripts/cherry_pick/write_authority.py \
  --critical-module scripts/cherry_pick/writer.py \
  --critical-module scripts/build_cherry_pick_skill.py \
  --critical-module scripts/check_cherry_pick_integration.py
```

Use a workspace or other confirmed disk-backed path for `--basetemp`; do not
use a memory-mounted system temporary directory. The offline core's
`--scratch-root` follows the same rule and is honored for both direct and
attributed-containment trials.

The second command is required: pytest-cov's single threshold is a combined,
display-rounded metric and does not independently enforce the two PRD
thresholds.

Generate a candidate inventory from already hydrated mirrors without network
access. This command refuses to overwrite the tracked golden:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py inventory \
  --data-root /path/to/replay-data \
  --candidate-out /path/to/replay-data/candidates/historical-candidate.json
```

Compare the candidate with the reviewed golden. Added, removed, reclassified,
or changed cases are blocking until the JSON diff is reviewed and the golden is
edited deliberately:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py compare \
  --candidate /path/to/replay-data/candidates/historical-candidate.json \
  --golden scripts/tests/fixtures/historical_cherry_picks.json
```

Run the full standalone regression suite. `--jobs` bounds concurrent repository
lanes; cases in the same repository remain serialized and reuse one index:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py run \
  --data-root /path/to/replay-data \
  --manifest scripts/tests/fixtures/historical_cherry_picks.json \
  --report-dir /path/to/replay-data/reports \
  --tier deep \
  --jobs 4
```

The tracked synthetic registry is used by default; pass
`--synthetic-coverage /reviewed/path.json` only when deliberately reviewing a
different registry. `--tier fast` runs 17 minimized/representative rows. `deep`
runs all 77 reviewed transitions. An inventory-only case is reported as such
and does not count toward changeset/outcome/file/recovery engine coverage.

The schema-v3 JSON and Markdown reports list both historical-only gaps and
required cells lacking any evidence. The latter produce exit code 2. Named
synthetic tests can close a combined gap, but remain visibly separate from
historical counts. See `historical-replay-analysis.md` for the reviewed current
result and limitations.

To verify scheduling determinism, run the same deep corpus once with `--jobs 1`
and once with `--jobs 4`, then compare both report files byte-for-byte:

```bash
cmp serial/historical-replay.json parallel/historical-replay.json
cmp serial/historical-replay.md parallel/historical-replay.md
```

After interruption—or whenever an operator wants a known clean cache—run:

```bash
.venv/bin/python scripts/replay_cherry_pick_history.py rollback \
  --data-root /path/to/replay-data
```

Rollback does not delete or recreate worktrees. It validates ownership, repairs
an invalid index from an atomic snapshot or local HEAD, clears sequencer state,
resets the cached worktree, removes trial-only untracked files, and verifies its
HEAD/status/tree. A nonzero result is blocking; do not reuse that cache until it
is understood.

The persistent worktrees can be large, particularly for rocm-libraries. Do not
delete them during normal regression work: deletion discards the warm-index
benefit. Any eventual cache deletion is a separate, local, destructive cleanup
decision and is not part of replay rollback.

## Conflict or ambiguity

For `blocked_conflict` or `blocked_ambiguous_changeset`:

1. Preserve the JSON evidence.
1. Reproduce the full proven changeset in a disposable worktree.
1. Consult the owning component team.
1. Use a separately reviewed manual draft for any resolution.
1. Never change the result to contained merely because application conflicted.

## Disable or roll back after future deployment

Set the affected train to `disabled` through a reviewed configuration change.
Retain labels, drafts, branches, and evidence for operator disposition. Do not
perform destructive cleanup automatically.

## Human handoff checklist

- Product requirements and design match the reviewed implementation.
- Red/green evidence is complete: the post-bundle matrix passes 1,036/1,036 on
  Python 3.10.20, 3.11.15, and 3.12.13 with the exact coverage gate green.
- All source callers are thin, pinned, formatted, and locally tested.
- Executor App permissions are limited to metadata read plus contents, pull
  requests, issues, and Checks; per-job tokens are further reduced.
- Release Hub remains read-only and has no label, Check, comment, branch, or PR
  write path for this product.
- Initial modes are non-writing.
- Every remote action has separate approval and remains queued until granted.
