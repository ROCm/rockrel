# Draft — local review required

# Label-driven cherry-pick automation: product requirements

## Problem and product boundary

ROCm release operators repeatedly move merged changes from TheRock,
rocm-systems, and rocm-libraries development branches to release branches. A
safe automation must identify the complete merged change, decide against the
exact destination branch whether that change is already present, apply it
without losing provenance, and prepare a draft pull request for normal ROCm
review and CI.

A **train is a configured destination branch per repository**. Express,
nightly, stabilization, and servicing trains use the same engine. The product
has three deliberately separate layers:

1. A repository maintainer or separately approved, allowlisted GitHub-native
   principal applies the request label.
1. GitHub App, Actions, labels, Checks, comments, and draft-PR APIs form the
   production control plane.
1. The core is an offline Git engine. It consumes immutable Git identities and
   local repositories and has no GitHub, Jira, Release Hub, network, credential,
   or LLM dependency.

Jira is not part of the cherry-pick contract, and the offline Git core never
queries Jira or Release Hub or trusts Jira text. The control plane and packaged
Marketplace adapter query Release Hub only for the authenticated, read-only
configuration snapshot described below. Release Hub is not a label, Check,
comment, branch, or PR writer for this product.

The release model is mainline-first. A requested change must already be merged
to the source branch configured for that repository. The current train maps
TheRock to `main` and the component repositories to `develop`; those are data,
not hard-coded engine rules.

## Local-development boundary

This implementation remains a local draft for human review. Ordinary tests
MUST NOT fetch or call remote APIs and use local Git repositories plus fake
transports. The explicitly invoked `local-materialize` operator path MAY perform
read-only GitHub API calls and Git fetches, but it MUST NOT push, apply a public
label, create or update a public Check/comment/PR, dispatch Actions, modify App
permissions, or deploy Release Hub. Every future remote write is listed in
`REMOTE_ACTIONS_TODO.md` and requires separate operator authorization.

The private-sandbox qualification harness MAY be prepared and reviewed locally,
but it MUST NOT be dispatched by this deliverable. A real sandbox run creates
remote branches and draft PRs and therefore remains a separately authorized,
mandatory production-approval gate.

## Local-only operator materialization

A fresh engineer checkout MUST be able to accept a source PR URL, train ID,
local source clone, disk-backed scratch path, absent output path, and local
branch name in one CLI invocation. The controller MAY use the engineer's existing `gh` credential for read-only
GitHub evidence; exact Git refs are fetched from each supplied clone's
configured origin. The
core request, containment decision, dependency order, conflict result, and
materialized tree MUST remain Git-based and must not depend on GitHub App
credentials, Jira, Release Hub, an LLM, or third-party Python packages.

The local materialization path MUST:

- bypass an absent request label only for this non-writable operator action and
  record that distinction in immutable evidence;
- make that evidence categorically ineligible for every remote writer;
- propagate the explicit disk scratch root into every Git trial;
- create a separate checkout from the exact destination SHA, never mutate the
  supplied source clone, and refuse an existing output path;
- disable the output checkout's push URL, Git hooks, prompts, and lazy fetch;
- use the shared ordered `-x` command builder and verify the result tree exactly
  equals the planned tree; and
- return all commands and proof evidence as machine-readable JSON while making
  no CI-success or semantic-readiness claim.

## Definitions

- **Train:** a stable ID and exact label mapped to an exact destination branch
  for each configured repository.
- **Request label:** `cherry-pick:<train-id>`.
- **Authorization envelope:** immutable evidence tying the latest label event to
  an authorized human or allowlisted App plus the reviewed source/graph state.
- **Executor App identity:** exact numeric GitHub App ID trusted to persist the
  label-time authorization fingerprint in a Check Run. It is separate from the
  IDs allowed to apply request labels.
- **Core request:** a versioned manifest of immutable source, typed
  prerequisite, destination, and open-PR coverage identities.
- **Merged changeset:** the complete tree delta introduced by a source PR,
  proven from its squash, merge-commit, or rebase representation.
- **Prerequisite graph:** the additive union of transitive canonical
  `Depends-On:` trailers and reviewed, version-controlled train overrides.
  Nodes are merged PRs or full standalone commit URLs and are evaluated by the
  core as Git changesets.
- **Coverage candidate:** an open same-repository PR targeting the exact
  destination. It suppresses automation only when destination ancestry,
  source attribution, and the final planned tree all match exactly.
- **Contained:** the complete proven changeset is represented in the exact
  configured destination through exact ancestry or a strongly attributed,
  tree-verified destination application. An unattributed patch-equivalent
  no-op requires manual semantic review. Similarity, conflict, a future nightly,
  another branch, Jira, or a gitlink bump is not containment proof.
- **Generated PR:** a draft-only PR based on the exact destination SHA.

## Goals

- Make an authenticated GitHub label the request interface for any train.
- Keep train destinations and rollout modes in version-controlled rockrel
  configuration without encoding branch-name conventions.
- Support source PRs merged by squash, merge commit, rebase, or a single commit.
- Run the Git core deterministically from a CLI without network or an LLM.
- Honor an explicit disk-backed scratch root end to end; embedded callers that
  omit it must use storage beside the local repository, never the process-wide
  temporary directory.
- Resolve bounded cross-repository dependency DAGs and wait until every
  prerequisite is merged and Git-contained in its train destination.
- Do nothing when the exact target already contains the complete source change.
- Do nothing when exactly one open manual or automation PR already carries the
  source-attributed change and exact planned tree.
- Fail closed on conflicts, missing or ambiguous evidence, stale authorization,
  dependency cycles, destination movement, and transaction races.
- Require non-initial events and reconciliation to reproduce the exact
  label-time envelope recorded by the configured executor App; otherwise
  require relabeling.
- Produce no more than one active draft per source repository, PR, and train.
- Preserve repository-native branch rules, reviewers, and CI.
- Recover idempotently from duplicate events and partial writes.
- Keep control flow reviewable through enforced routine-size and decision-point
  limits, strong docstrings, and comments on non-obvious safety invariants.

## Non-goals

- Marking a draft ready, approving, merging, enabling auto-merge, closing a
  draft, deleting a branch, or updating an existing remote branch.
- Automatically resolving conflicts or speculatively creating a later
  dependency/root draft before the prior managed wave is exactly represented.
- Implementing a Zuul-style speculative, atomic multi-repository gate.
- Synthesizing TheRock gitlink rollups for component-repository requests.
- Treating Release Hub build/nightly propagation as target-branch proof.
- Parsing arbitrary prose, Jira links, issue links, bare SHAs, or short SHAs as
  prerequisites. Canonical full ROCm commit URLs are supported deliberately.
- Replacing repository CI, branch protection, or human review.

## Users and principals

- **Release operator:** configures trains and reviews generated drafts.
- **Repository maintainer:** may authorize a request by applying a label and
  later reviews the resulting draft.
- **Source PR author:** receives actionable status but no release authority.
- **Approved label principal:** a repository maintainer or an exact numeric
  GitHub App identity that has been separately reviewed and allowlisted.
- **Cherry-pick executor App:** a separate principal that reads evidence and,
  only in an approved write job, creates a branch, draft, Check, and comment.
- **Local release operator:** an authenticated human who may use `gh`
  credentials for an immediate plan or, with an exact reviewed plan artifact
  and literal write confirmation, the same draft-only writer.

The credential adapter does not change the Git decision. App and local paths
must produce the same immutable request, tree, branch identity, and PR body.
Every generated PR description must record each exact Git cherry-pick command
executed, in order, rather than a synthesized equivalent command.

## Product contract

### Train configuration

Developer Central is the only runtime source for active cherry-pick policy.
The Git-reviewed ROCm Release Hub `config/release-trains.json`, with schema
`release-trains.v5`, owns repository source branches, exact destination
branches, labels, modes, limits, App identities, and reviewed prerequisite
overrides. The API projects that policy as `cherry-pick-config.v1`; clients
MUST validate its schema version, policy
SHA-256, and complete contents and MUST NOT merge it with a bundled catalog,
hard-coded branch rules, string-derived destinations, or a last-known-good
runtime fallback.

The projected catalog contains no Jira field. `executor_app_id` is null during
local validation and must be an exact positive numeric App ID before `shadow`
or `create-draft` can execute. Destination branches are selected only from the
matching reviewed train `branches` records:

```json
{
  "schema_version": 5,
  "authorization": {
    "minimum_human_permission": "write",
    "trusted_app_ids": [],
    "executor_app_id": null
  },
  "dependency_policy": {
    "max_nodes": 64,
    "max_depth": 16
  },
  "coverage_policy": {
    "max_open_pull_requests": 128
  },
  "trains": [
    {
      "id": "10.1-20260811",
      "label": "cherry-pick:10.1-20260811",
      "state": "active",
      "mode": "validate",
      "dependency_mode": "gate",
      "prerequisite_overrides": [],
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

IDs, labels, repositories, branch names, App IDs, limits, and dependency modes
are strictly validated. Unknown fields fail closed. Adding a train is a
Git-reviewed Release Hub configuration change, not an Actions or source-repo
code change.

Local callers authenticate to Developer Central with an owner-bound
`read:evidence` API token. GitHub Actions authenticates to this one read-only
endpoint with a short-lived GitHub OIDC token. GitHub App installation tokens
remain the only credentials used for GitHub reads or writes in the integrated
path. The offline core receives only the resulting immutable manifest and no
credential or network client.

The Actions OIDC audience is exactly
`api://developer-central.amd.com/rocm-cherry-pick-config`. It is not an origin
URL and MUST NOT be replaced by an HTTPS audience. Policy pins owner `ROCm` to
numeric ID `21157610`, `TheRock` to repository ID `765605091`, `rocm-systems`
to `962090208`, `rocm-libraries` to `971570345`, and `rockrel` to
`1071689640`. Each caller MUST match one complete repository, numeric IDs, ref,
event, subject, and workflow tuple; fields from different tuples MUST NOT be
combined.

The three source callers allow only `pull_request_target`: TheRock targets
`main`, while rocm-systems and rocm-libraries target `develop`. For this event,
`base_ref` MUST equal that configured base branch and `ref` MUST equal
`refs/heads/<base_ref>`. The subject MUST be the immutable
`repo:ROCm@21157610/REPOSITORY@repository_id:pull_request` form. Each source
caller MUST identify
`ROCm/rockrel/.github/workflows/cherry_pick.yml@<sha>` through exact
`job_workflow_ref` and `job_workflow_sha` claims, where `<sha>` is the same full
lowercase 40-character SHA used by its `uses:` reference and `automation_ref`.

Direct rockrel callers use `refs/heads/main` and subject
`repo:ROCm@21157610/rockrel@1071689640:ref:refs/heads/main`. Reconciliation
allows only `schedule` and `workflow_dispatch`; direct `cherry_pick.yml` allows
only `workflow_dispatch`. Direct callers MUST match exact `workflow_ref` and
`workflow_sha` claims. `job_workflow_*` is not a substitute because GitHub emits
those claims for reusable jobs, not direct workflows. Missing claims, mutable
references, unlisted events, mismatches, and every legacy name-only
`repo:ROCm/REPOSITORY:*` subject fail closed with no fallback.

[GitHub's official OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
states that repositories created before 2026-07-15 require authorized opt-in to
immutable repository identities in the subject. Local code and configuration
cannot prove or enable that GitHub setting. Production therefore remains
blocked on a separately authorized remote verification and, where required,
opt-in for all four repositories; this review performs neither action.

The repositories share one versioned `rocm-cherry-pick-integration.v2`
manifest. It records the configuration endpoint, exact OIDC issuer and audience,
canonical numeric IDs, per-caller event/ref/workflow tuple, reviewed rockrel
SHA, and supported callers. A local cross-repo checker MUST validate that
manifest against rockrel, Release Hub, TheRock, rocm-systems, and rocm-libraries
and reject any tuple or SHA mismatch. This checker proves configured cross-file
trust-anchor and immutable-pin equality only. Token exchange, signature
verification, claim validation, configuration authorization, and workflow
behavior remain the responsibility of behavioral suites at their owning
boundaries. Thin caller workflows are generated from this contract; they do not
duplicate policy or Git decisions.

### Request authorization

The control plane re-fetches the current PR and the complete paginated issue
timeline. A request is authorized only when:

1. The configured label is currently present and its latest transition is a
   `labeled` event.
1. That event was performed by either a human whose current repository
   permission is at least `write`, or an exact numeric GitHub App ID in the
   train catalog's allowlist.
1. The source head, PR-body digest, dependency graph snapshot, and rockrel
   configuration revision still match the authorization envelope.

The event actor alone is not trusted. A bot login without an authenticated
`performed_via_github_app.id` is not trusted. Missing history, pagination, or
permission evidence returns `blocked_authorization`.

A label applied before merge produces `awaiting_merge`. Head, body, or
dependency-declaration changes require the label to be removed and reapplied.
Removing the label cancels future work but never closes or deletes an existing
draft or branch.

### Dependencies

Prerequisites may be repeated footer trailers in a PR description:

```text
Depends-On: https://github.com/ROCm/<repository>/pull/<number>
Depends-On: https://github.com/ROCm/<repository>/commit/<full-lowercase-sha>
```

- Trailer syntax follows `git interpret-trailers --parse` behavior.
- Only canonical PR URLs and canonical full commit URLs in configured ROCm
  repositories are accepted. Standalone commits must be single-parent leaves.
- A reviewed per-train override may add edges for one exact source PR. It must
  include a rationale, is additive only, and cannot delete trailer-declared
  edges. Comments, PR prose, and Jira are never override inputs.
- Duplicate edges are normalized; self-edges and cycles block.
- The graph is bounded to 64 nodes and depth 16.
- Each node is resolved to its immutable head/merge/commit identities and that
  repository's destination in the same train.
- A prerequisite is satisfied only when it is merged, its complete changeset is
  proven, and that changeset is Git-contained in its exact destination.
- Every train chooses one reviewed dependency mode. `gate` returns
  `awaiting_dependencies` until every prerequisite is exactly contained.
  `managed_stack` materializes only the currently unblocked topological
  frontier, waits for exact destination containment, and then advances the next
  wave. Existing exact draft PRs are reused; multiple exact candidates block.
  Invalid or ambiguous graphs return `blocked_dependency`.
- Managed stacks support only explicit canonical PR and full commit nodes. They
  do not synthesize TheRock gitlink bumps or infer dependencies from Jira,
  titles, comments, newer nightlies, or repository layout.
- The product never marks ready, updates, merges, closes, deletes, or resolves
  a prerequisite draft. Cross-repository waves are ordered but not atomic.

### Git core request and decision

The GitHub adapter produces a versioned immutable manifest. The core receives
that manifest plus explicit local repository paths. It validates that every
referenced commit exists locally, proves the source representation and complete
changeset, evaluates all dependencies, and evaluates the root against the exact
destination SHA.

| Exact evidence                                                                         | Result                                                             | Write                                                                      |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| Source PR is eligible but not merged                                                   | `awaiting_merge`                                                   | None                                                                       |
| A `gate` prerequisite is not contained                                                 | `awaiting_dependencies`                                            | None                                                                       |
| `managed_stack` has a nonempty safe frontier                                           | `awaiting_dependencies / managed_dependency_frontier`              | Frontier drafts only after exact write-time revalidation in `create-draft` |
| Dependency graph is invalid or ambiguous                                               | `blocked_dependency`                                               | None                                                                       |
| Required Git or authorization evidence is unavailable                                  | `blocked_evidence` / `blocked_authorization`                       | None                                                                       |
| A promisor checkout lacks a required object                                           | `blocked_evidence / local_objects_incomplete`                        | None; preserve bounded Git stderr                                          |
| Complete source changeset cannot be proven                                             | `blocked_ambiguous_changeset`                                      | None                                                                       |
| Exact ancestry or an attributed, tree-verified application proves the complete source  | `already_contained`                                                | None                                                                       |
| Complete application is empty only by unattributed patch equivalence                   | `blocked_ambiguous_changeset` / `patch_equivalent_review_required` | None                                                                       |
| Complete application conflicts                                                         | `blocked_conflict`                                                 | None                                                                       |
| Complete application is clean and non-empty, with no covering open PR                  | `draft_planned`                                                    | None                                                                       |
| One open PR has destination ancestry, exact source attribution, and exact planned tree | `covered_by_existing_pr`                                           | None                                                                       |
| Exact tree lacks attribution, attributed tree differs, or multiple exact covers exist  | `blocked_ambiguous_changeset`                                      | None                                                                       |
| Exact automation draft already exists                                                  | `draft_exists`                                                     | None                                                                       |
| Branch/PR identity exists with a different tree                                        | fail closed                                                        | None                                                                       |
| Branch exists after a recoverable partial transaction                                  | `retryable_partial_write`                                          | Reconcile only                                                             |

Every result records stable status/reason codes, source and destination SHAs,
changeset proof, ordered commits, dependency evidence, conflict paths/stages,
expected tree SHA, complete open-PR snapshot digest, a Git-only assurance
statement, and a canonical plan fingerprint. `covered_by_existing_pr` is a Git
coverage decision, not a claim that CI passed or that the change is semantically
ready to merge.

### Modes and GitHub feedback

| Mode           | Behavior                                                                |
| -------------- | ----------------------------------------------------------------------- |
| `disabled`     | No request processing or feedback                                       |
| `validate`     | Explicit local/manual planning only                                     |
| `shadow`       | Event/reconciliation planning and approved Check feedback; no branch/PR |
| `create-draft` | Separately authorized write job may create one draft                    |

One Check Run named `ROCm Cherry-Pick / <train-id>` is the canonical GitHub
state. Success means contained or an exact draft exists; neutral means waiting
or shadow-planned; action-required means human intervention; cancelled means
the request was removed or disabled. One marker-delimited comment is upserted
only for a draft link or concrete operator action.

### Draft quality and invariants

A generated pull request must:

- Always be created with `draft: true`.
- Target the configured destination and preserve `-x` provenance.
- Include immutable source/destination identities, proof method, ordered
  commits, dependency graph status, expected tree, preflight result, normal CI
  reminder, and an operator checklist.
- Preserve the source description below generated metadata without interpreting
  its Jira text.
- Never claim destination CI passed.

Immediately before writing, the control plane revalidates authorization,
source/graph snapshots, configuration, destination head, branch state, and
the complete open-PR coverage snapshot. Planning and writing use the same validated cherry-pick
command builder and application contract. The writer independently
rematerializes from the exact destination and requires the planned tree. It may
atomically create an absent ref or reuse an exact tree, but cannot update or
delete an existing ref.

### Dry-run acceptance case: rocm-systems #9716 / #10153

The frozen regression requires the configured prerequisite order
`3a3fb3206000a3b47e953fd6613571ae6ca0edb4`, PR #8221, PR #9480, then PR #9716.
For destination `800045c8ab865991f4cec1549de2bb44e76b9904`, applying #9716 must plan
tree `2b7467c293ea312349db32372bdc51a495fd419d`. Open PR #10153 at
`411a04e98648ef442751e8e219ab9fa1cfb228bf` must be classified as
`covered_by_existing_pr` only because it descends from that destination,
contains exact `(cherry picked from commit b3252f...)` attribution, and has the
same tree. Its CI state remains advisory and outside the Git core. No branch or
new PR may be created for this case. The acceptance evidence MUST include a
complete current-schema `CoreRequest` that can be passed verbatim to the
standalone offline CLI; a summary fixture or obsolete-schema request is not
sufficient.

## Developer Central configuration boundary

ROCm Release Hub owns the Git-reviewed release-train configuration and exposes
one authenticated, read-only `GET /api/v1/cherry-pick/config` projection through
Developer Central. The endpoint is protected by a safe-default-off feature flag
and accepts either an owner-bound `read:evidence` token or a policy-bound GitHub
Actions OIDC identity. It returns a standard request envelope, the source
policy digest, an ETag, `Cache-Control: no-store`, and no secret material.

Release Hub remains outside every GitHub mutation path. It MUST NOT apply or
remove labels, publish Checks/comments, mint GitHub App tokens, push branches,
or create/update pull requests. GitHub Actions plus the executor App are the
production glue; the local skill uses `gh` credentials only for GitHub reads
and local Git fetches. If Developer Central or its current policy is
unavailable, both paths fail closed before planning or writing.

## Safety requirements

- Privileged workflows use trusted `pull_request_target` definitions and never
  execute PR-head code.
- Every automation revision is a full immutable SHA; manual dispatch must use
  the exact workflow commit, validated before checkout or App authentication.
- The Release Hub OIDC verifier must bind `job_workflow_ref` and
  `job_workflow_sha` to the same reviewed 40-character rockrel SHA; repository,
  owner, ref, subject, issuer, audience, time, signature, and key evidence also
  fail closed when missing, malformed, stale, or inconsistent.
- Exact PR head, merge, original commit, dependency, and destination objects are
  hydrated before the offline core runs, including fork PR refs.
- Read and write jobs mint distinct short-lived, repository-scoped installation
  tokens with reduced permissions.
- Planning cannot construct write credentials. The write job exists only for a
  fresh `draft_planned` result and revalidates every boundary.
- Continuations accept authorization state only from a Check Run whose name,
  source head, external fingerprint, and numeric creator App ID all match.
- API pagination never truncates silently; retryable failures use bounded
  backoff, Search incomplete/cap signals block, PR commit pages must equal the
  API-declared count, and all unknown evidence blocks.
- Credential-bearing GitHub and Release Hub HTTP transports MUST refuse every
  redirect and read at most 2 MiB plus one detection byte from success and error
  bodies. Oversized or malformed responses fail closed without echoing bodies.
- A standalone commit has the canonical digest of an empty open-PR coverage
  snapshot. Unrelated open PRs do not enter that identity; any non-canonical
  snapshot blocks before a branch or pull-request write.
- Workflow concurrency is advisory; writer compare-and-create and exact-tree
  checks provide transaction safety.
- The writer rechecks the exact destination after materialization and before
  any branch push; after the creation-only push it rereads the remote ref and
  tree, and after draft creation it rereads draft/head/base/identity. Any
  mismatch stops as a recoverable partial transaction and never updates or
  deletes remote state.
- Scratch paths must be disk-backed. Explicit scratch roots are propagated
  through every containment and trial application; the safe embedded fallback
  is the local repository filesystem. Git prompts, hooks, and lazy network
  fetch are disabled.
- The codebase exposes no ready, approve, merge, auto-merge, branch update,
  remote delete, or draft-close operation.

## Simplicity, readability, and documentation requirements

Production code MUST remain intentionally straightforward:

- `main`, `Planner.plan`, and `DraftWriter.create` are each limited to 80
  logical lines and 15 decision points.
- Every other production callable is limited to 150 logical lines and 25
  decision points. Generated files and test fixtures are excluded; replay,
  simulation, and packaging code are not.
- The limits are enforced from syntax trees so formatting changes cannot evade
  them. An exception requires a documented architecture-review decision; an
  inline suppression alone is insufficient.
- Every production Python module, class, function, and method, including a
  private helper, has a substantive docstring. Public APIs describe purpose,
  parameters, return values, and non-obvious exceptions. Authorization, token,
  Git mutation, transaction, and recovery APIs also state their security or
  invariants contract.
- Exported TypeScript APIs in the Release Hub cherry-pick surface have
  equivalent TSDoc. Inline comments explain rationale, race protection, or a
  safety invariant; they do not narrate syntax.
- Packaged runtime modules are the transitive local import closure of the
  reviewed Marketplace entrypoint. An unreachable module is removed from the
  bundle or retained only with a documented runtime or qualification owner.

## Historical replay qualification

Historical validation is independent of discovery and never uses engine output
as its own oracle.

- Every strict case starts at the destination parent immediately before the
  known-good historical cherry-pick.
- The core applies the proven source changeset and its tree must exactly equal
  the known-good result tree.
- Reruns against the known-good result and pinned target tip must return
  `already_contained` with positive proof.
- Expected conflicts must report exact paths/stages and perform no write.
- Inventory-only, release-native, multi-source, manual adaptation, and missing
  provenance rows are reported separately and never counted as strict passes.
- Replays are offline, deterministic, parallel by isolated repository lane, and
  use disk-backed reusable worktrees with verified rollback/index snapshots.
- Serial and parallel canonical reports must be byte-identical.

The baseline corpus has 77 rows: 31 strict exact passes/containment reruns,
three conflict diagnostics, five adaptations/evidence gaps, and 38
inventory-only rows. All 31 strict cases and three expected conflicts are
non-regression gates. Every reconstructable inventory case is promoted only
after a human-reviewed oracle is recorded.

## SLAI Marketplace distribution and first login

The local operator capability MUST be distributable as the standard SLAI skill
`rocm-cherry-pick` without requiring a rockrel checkout or third-party Python
packages. The generated asset MUST carry the reviewed Git core and adapters,
Marketplace metadata, a source/hash manifest, and concise references. rockrel
remains the implementation source of truth; a staging copy in the SLAI skills
workspace is generated and MUST NOT be edited independently.

Every build MUST produce an explicitly unvalidated pre-scan bundle whose
manifest schema is `rocm-cherry-pick-bundle.v2`. Its `source_provenance` MUST
record the exact Git `base_revision`, a `clean_commit` or
`dirty_worktree_review` state, and `source_content_sha256`, the deterministic
digest of the exact packaged source closure. Dirty input MUST fail unless the
operator supplies `--allow-dirty-review`; that review bundle is nonpublishable.
A publishable bundle requires a rebuild from a clean reviewed commit.

The builder MUST remove any scanner-owned `metadata.compliance_scan` block,
`COMPLIANCE_SCAN_WAIVERS.yaml`, `COMPLIANCE_FINDINGS.json`, and
`COMPLIANCE_SCAN.md` before recomputing the source/hash manifest. Unsafe
non-file validation paths MUST fail before any partial mutation. Author/NTID
validation and security scanning MUST run only after the exact final build; any
later rebuild invalidates those results and MUST require a fresh scan.

Release Hub is mandatory for destination discovery. The skill MUST require one
exact Release Hub train ID and MUST fail closed when the train, repository
branch, configuration hash, or confirmed branch record is unavailable. Release
Hub supplies release configuration; Git supplies current branch heads,
containment, conflicts, application, and resulting trees. Jira and LLMs remain
outside the core and local CLI.

On first use, an engineer without a Release Hub token MUST be directed to
`https://developer-central.amd.com/settings/api-tokens`. The instructions MUST
name the **ROCm Cherry-Pick CLI** preset, exact `read:evidence` scope, one-time
copy behavior, and hidden CLI prompt. Tokens MUST NOT be accepted in argv or
printed. The CLI MUST interoperate with `rrh-auth.v1`, validate the token with
`GET /api/v1/auth/session`, and warn before expiry.

The v1 Marketplace asset MUST NOT expose draft creation or contain a remote
writer. It accepts only `auth`, `plan`, and `materialize` operations. Any future
remote capability requires a new product/design/security review, separately
reviewed exact-train policy, identical write-time replan, explicit authority,
and a new TDD cycle. No Marketplace or GitHub write is part of packaging
validation.

## TDD and acceptance criteria

The mandatory implementation sequence is PRD/design, complete failing tests,
recorded red run, implementation to green, then broad verification. Every code,
configuration, workflow, parser, permission, and failure-path change requires a
unit test.

Before any activation:

- All old and new tests pass.
- The complete Python suite runs on Python 3.10, 3.11, and 3.12. Overall
  coverage is at least 95% lines and 90% branches; no changed
  safety-critical core, configuration, adapter, dependency, or writer module is
  below 90% for either measure.
- Release Hub's complete root `npm run verify` command is green. Its OIDC
  verifier has 100% line/function and at least 95% branch coverage; other new
  cherry-pick backend modules have at least 95% line/function and 90% branch
  coverage; and the API-token settings surface has at least 95% line/function
  and 90% branch coverage. Tests cover malformed and mismatched OIDC signatures,
  keys, algorithms, issuer, audience, repository, ref, subject,
  `job_workflow_ref`, `job_workflow_sha`, and time boundaries.
- All supported strict historical replays reproduce exact known-good trees;
  expected conflicts and negative cases match their exact contracts.
- Offline core output is byte-identical across repeated, serial, and parallel
  runs and succeeds without tokens or network.
- Workflow/static tests prove immutable pins, least privilege, no Jira
  secrets, no PR-head execution, and disabled local write paths.
- Static and repository-native tests prove Release Hub is used only for the
  authenticated configuration read and is never granted a GitHub mutation
  role by this product.
- The v2 cross-repository integration checker proves configured cross-file
  equality for the exact audience, numeric owner/repository IDs, immutable-only
  subject policy, per-caller event/ref/workflow tuple, full-SHA pin, and caller
  set across all five repositories. It does not prove runtime token or verifier
  semantics; Release Hub OIDC/configuration tests and rockrel workflow/adapter
  tests prove those behaviors separately.
- Every Mermaid block is parsed and rendered to SVG by an immutable-digest
  Mermaid CLI container with rendering network access disabled. Duplicate
  diagram identifiers, missing accessibility metadata, and broken local links
  in either source or bundled-skill context fail verification.
- No numeric test-count target is imposed. A test may be removed as redundant
  only when it covers the same requirement and fault model, adds no unique
  assertion or boundary, runs in the same relevant layer, and catches no unique
  injected failure or mutant. Intentional unit, integration, replay, and
  end-to-end overlap remains when the layers use distinct oracles.
- Documentation reports measured evidence and limitations. It must never call
  the product error-free or count inventory/adaptations as strict passes.

## Rollout and rollback

The current deliverable is a local, uncommitted draft diff and a rebuilt
review bundle. Its v2 provenance is deliberately `dirty_worktree_review` at base
`8432a05b8c081df871d426525728de39569ff3cb`, with source-closure digest
`ce9fcd327f311357359bf8f86db88971caf733a042a90e95682ef38b56c1159b`;
it is not publishable. After separate approval, rockrel is reviewed and a clean
commit bundle is rebuilt before callers pin the reviewed immutable SHA. App creation, installation, secret setup, label
provisioning, caller PRs, workflow activation, and a private sandbox test are
separate operator actions.

Rollout proceeds with the Developer Central endpoint flag off, then a private
OIDC/API-token contract exercise, `validate`, `shadow`, and one-train
`create-draft` pilot. Production remains draft-only. Rollback suspends the
executor App or revokes its credentials and commits a durable disabled train
configuration; caller pins can also be restored. Existing drafts and branches
are left untouched for human disposition. Release Hub remains read-only with
respect to GitHub throughout rollout and rollback.

The private-sandbox harness is a locally prepared artifact, not completed
evidence. Its separately authorized run MUST use only explicitly allowlisted
private repositories with a sandbox sentinel and sandbox-only branch prefixes.
The manifest's production denylist MUST contain the real repository IDs TheRock
`765605091`, rocm-systems `962090208`, rocm-libraries `971570345`, and rockrel
`1071689640`; placeholder IDs are forbidden.
A production-parity sandbox executor adapter is not implemented in this review
copy. That adapter requires its own design and tests to preserve production
configuration, authorization, Git, transaction, and draft-only semantics while
substituting only an approved private repository identity and branch namespace.
It must exercise OIDC exchange, installation-token reads and writes, branch
creation, draft creation, duplicate delivery, deliberately induced
branch-created/PR-failed recovery, stale-evidence rejection, conflicts,
dependency ordering, and branch protection. Production approval remains blocked
until redacted evidence from that run receives DevOps, repository-owner, and
security review.
