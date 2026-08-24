# Draft — local review required

# ROCm cherry-pick CLI user manual

## Purpose

For the shortest setup and command sequence, start with the
[ROCm cherry-pick user guide](README.md). This manual provides the complete
decision model, dependency handling, and troubleshooting reference.

This manual explains when to use a direct `git cherry-pick`, when to use the
ROCm cherry-pick CLI, and how to create a verified local checkout for human
review. It applies to configured destination trains in ROCm/TheRock,
ROCm/rocm-systems, and ROCm/rocm-libraries.

A train is simply a configured destination release branch for each repository.
The CLI is not Express-Train-specific.

The CLI does not replace Git. Its final content operation is still an ordered
`git cherry-pick -x`. Its value is establishing that the selected changeset,
destination, dependency order, and outcome are correct before that operation is
accepted.

## Choose direct Git or the CLI

Use direct Git when all of the following are already known and independently
trusted:

- the exact commit or complete ordered commit sequence;
- the source change is merged to the configured `main` or `develop` branch;
- the exact destination branch and current destination head;
- every prerequisite and its required order;
- the destination does not already contain the complete change;
- no open destination PR already carries the same attributed change and final
  tree; and
- an isolated checkout, structured evidence, and reproducible audit record are
  unnecessary.

For that narrow case, direct Git is sufficient:

```bash
git switch --create local/cherry-pick/CHANGE origin/DESTINATION_BRANCH
git cherry-pick -x COMMIT_SHA
```

Use the CLI whenever one or more of those facts must be discovered or proven.
This is the recommended ROCm release workflow because a source PR URL alone
does not establish which Git objects represent its complete merged change.

| Concern                 | Direct `git cherry-pick`           | ROCm cherry-pick CLI                                                                         |
| ----------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------- |
| PR representation       | Operator selects commits           | Proves squash, merge-commit, rebase, or single-commit representation                         |
| Source eligibility      | Assumed                            | Verifies merged state and configured source branch                                           |
| Destination             | Operator selects a ref             | Binds evaluation to the configured branch and exact head SHA                                 |
| Prerequisites           | Operator discovers and orders them | Builds a bounded dependency DAG and evaluates it in order                                    |
| Already present         | Operator investigates history      | Proves complete destination containment or stops for ambiguity                               |
| Existing destination PR | Not considered                     | Evaluates open candidates for exact attributed tree coverage                                 |
| Conflict                | Git stops in the working checkout  | Trial application returns structured conflict evidence without modifying the source checkout |
| Result verification     | Operator compares the result       | Requires the final tree to equal `planned_tree`                                              |
| Provenance              | Optional                           | Uses `-x` for every applied commit                                                           |
| Audit output            | Terminal history                   | Emits stable JSON with identities, proofs, commands, and assurance limits                    |
| Accidental publication  | Normal remote remains writable     | Creates a separate checkout whose push URL is `disabled://local-only`                        |

## Prerequisites

Runtime use requires:

- the installed `rocm-cherry-pick` SLAI skill, or its reviewed local bundle;
- Python 3.10 or newer;
- Git;
- GitHub CLI authenticated to `github.com`;
- a Release Hub API token with only `read:evidence`;
- a normal clone of each source repository involved in the dependency graph;
  and
- a disk-backed scratch directory.

No rockrel checkout, virtual environment, third-party Python package, GitHub
App credential, Jira client, or LLM is required. Release Hub supplies only the
exact train configuration and confirmed destination branch. The PR-URL wrapper
uses the existing `gh` credential for read-only GitHub metadata; the core engine
and materializer remain pure Git and operate on immutable local objects.

On first use, open
<https://developer-central.amd.com/settings/api-tokens>, create the **ROCm
Cherry-Pick CLI** preset with exactly `read:evidence`, copy its one-time value,
and enter it through the hidden prompt:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth login
```

Never put the Release Hub token in argv, chat, an issue, or a pull request.
Then confirm GitHub authentication without extracting or printing that token:

```bash
gh auth status
```

The supplied repository must have a normal origin capable of fetching GitHub
pull-request refs and the configured source and destination branches. A replay
mirror with a restricted ref namespace is not a substitute for a normal fresh
clone.

## Fresh-checkout quick start

Until this change is reviewed and published, `/path/to/rocm-cherry-pick` means
the generated local-review bundle under `skills/rocm-cherry-pick`. After SLAI
publication it means the installed Marketplace skill. An engineer needs only a
fresh source-repository checkout:

```bash
git clone https://github.com/ROCm/rocm-systems.git
gh auth status
mkdir -p /absolute/disk/path/cherry-pick-scratch
```

Plan PR #10031 against the exact Release Hub train first:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py plan \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/disk/path/cherry-pick-scratch \
  > /absolute/path/to/reviewed-plan.json
```

Review that JSON. Only when it reports `draft_planned`, rerun the same inputs to
materialize the independently verified local checkout:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py materialize \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/disk/path/cherry-pick-scratch \
  --output-repo /absolute/path/to/rocm-systems-pr-10031-local \
  --branch local/cherry-pick/10.1-20260811/10031
```

Replace the source PR, train, repository mapping, scratch path, output path, and
local branch for another request. Requirements for those arguments are:

- `--source-pr` is one canonical `https://github.com/OWNER/REPO/pull/NUMBER`
  URL.
- `--train` is one exact configured Release Hub train ID. The CLI rejects
  planned, disabled, invalid, unconfirmed, or ambiguous destinations.
- `--repo-dir` uses `OWNER/REPO=/absolute/path` and may be repeated for
  cross-repository prerequisites.
- `--scratch-root` is a disk-backed location. The planner cleans disposable
  worktrees but retains the directory.
- `--output-repo` is an absolute path whose parent exists and whose final path
  does not exist.
- `--branch` is a valid, new local branch name.

The two commands use the same engine and perform this sequence:

1. Validate the private Release Hub token and require `read:evidence`.
1. Validate the complete `cherry-pick-config.v1` response, its
   `release-trains.v5` SHA-256, and one confirmed destination branch.
1. Read the source PR, its complete declared commit list, merge state, configured
   source branch, and current destination evidence.
1. Read declared prerequisites and configured reviewed prerequisite overrides.
1. Read the bounded set of open PRs targeting the destination.
1. Fetch the exact PR, source, prerequisite, coverage-candidate, and destination
   Git refs into the supplied local repositories.
1. Build an immutable core request.
1. Prove the complete changeset representation and dependency order.
1. Check destination containment and exact existing-PR coverage.
1. Trial-apply the ordered changes in disk-backed scratch space.
1. If and only if the result is `draft_planned`, create an independent local
   checkout at the exact destination SHA.
1. Execute every reported `git cherry-pick -x` command with Git hooks disabled.
1. Require the resulting tree to equal `planned_tree`.
1. Disable the output checkout's push URL as `disabled://local-only` and return
   machine-readable JSON.

No remote branch or pull request is created by `plan` or `materialize`. The
packaged command has no writer or status-publication option.

### Rockrel contributor entry point

Contributors changing the engine may run the project-local
`python3 -m scripts.cherry_pick ... local-materialize` interface with a
complete Developer Central snapshot supplied through `--config-snapshot` and
bound through `--expected-config-sha256`. The direct `--config` input exists
only for source parser/unit-test fixtures; it is not a production runtime
catalog and cannot authorize a destination fallback. This is a
source-development interface, not an end-user installation requirement. Its
output and safety contract are the same, but it does not replace the packaged
adapter's authenticated configuration lookup.

## Agent skill workflow

The installed agent skill is a guided wrapper around the same packaged CLI; it
is not a second implementation. Invoke it explicitly as `$rocm-cherry-pick`.
A good planning request supplies the canonical PR URL, exact train ID,
`OWNER/REPO=/absolute/path` mappings, and a disk-backed scratch directory:

```text
Use $rocm-cherry-pick to plan
https://github.com/ROCm/rocm-systems/pull/10031 for train 10.1-20260811.
Use ROCm/rocm-systems=/absolute/path/to/rocm-systems and
/absolute/disk/path/cherry-pick-scratch. Perform no remote writes. Show the
exact CLI command and summarize the status, destination, dependencies,
containment, conflicts, planned tree, and next action.
```

The agent must run `plan` first and stop for human review. Only after you
confirm a `draft_planned` result should you ask it to run `materialize`,
naming a new absolute output path and local branch. It must show the exact CLI
command and JSON result, verify `tree == planned_tree`, and preserve the
reported command list for any later pull-request description.

If Release Hub authentication is missing, the agent directs you to Developer
Central and asks you to run the hidden-prompt `auth login` locally. Never send
a token through chat. The skill cannot perform remote writes and must not bypass
a blocked, contained, covered, or dependency result with manual GitHub or Git
commands.

See the packaged [skill instructions](../../skills/rocm-cherry-pick/SKILL.md)
and [operator guide](../../skills/rocm-cherry-pick/references/operator-guide.md)
for the agent's exact behavioral contract.

## Understand the result

Successful local materialization returns a JSON object shaped like this:

```json
{
  "status": "local_materialized",
  "reason_code": "local_checkout_created",
  "destination_branch": "release/bkc/therock-10.1-20260811",
  "destination_head": "b6cf6ab7abab454a7c4a7e7d37cda7c99736ef3e",
  "local_branch": "local/cherry-pick/10.1-20260811/10031",
  "local_path": "/absolute/path/to/rocm-systems-pr-10031-local",
  "tree": "8621c291ae78d9affc518b57bbb0498a60facba9",
  "planned_tree": "8621c291ae78d9affc518b57bbb0498a60facba9",
  "commands": [
    "git -c core.hooksPath=/dev/null cherry-pick -x 6691fe3e61967465422ed5b974e494f5520dbfe6"
  ]
}
```

Before reviewing code, require all of the following:

- `status` is `local_materialized`;
- `reason_code` is `local_checkout_created`;
- `tree` exactly equals `planned_tree`;
- `destination_branch` and `destination_head` are expected;
- `commands` contains every expected ordered operation; and
- the nested assurance record says `scope=git_only`,
  `ci_checks=not_evaluated`, and
  `semantic_readiness=human_review_required`.

Planner results that intentionally create no output include:

| Status                        | Meaning                                                                                       | Operator action                                                                               |
| ----------------------------- | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `already_contained`           | The complete proven changeset is already represented in the destination                       | Do nothing                                                                                    |
| `covered_by_existing_pr`      | One exact open destination PR already carries the attributed change and planned tree          | Review the existing PR; do not create another                                                 |
| `awaiting_merge`              | The source PR is not merged                                                                   | Wait for source merge                                                                         |
| `awaiting_dependencies`       | A gate is waiting, or managed mode has produced the next safe `managed_frontier_results` wave | Wait in gate mode; materialize/review only the reported managed wave                          |
| `blocked_conflict`            | Trial application produced conflicts                                                          | Review `conflict_paths` and `conflict_stages`; resolve manually outside automation            |
| `blocked_ambiguous_changeset` | The engine cannot prove one safe interpretation or exact coverage                             | Investigate; do not guess                                                                     |
| `blocked_dependency`          | The dependency graph is invalid, incomplete, cyclic, or blocked                               | Correct reviewed dependency evidence                                                          |
| `blocked_evidence`            | Required API or Git evidence is missing, stale, truncated, or inconsistent                    | Correct the checkout/origin or retry after evidence is available                              |
| `blocked_authorization`       | The ordinary label-authorized path lacks valid authority                                      | Correct the request label or permission; local-only evidence cannot authorize a remote writer |
| `ineligible_source`           | Repository, train, source branch, or source state is not eligible                             | Correct configuration or request                                                              |

Treat `reason_code` and `message` as the precise explanation. Do not collapse
all non-success statuses into “conflict.”

## Review the local checkout

After `local_materialized`, enter the returned `local_path` and perform normal
repository review:

```bash
cd /absolute/path/to/rocm-systems-pr-10031-local
git status --short
git log --oneline --decorate -n 5
git diff HEAD^ HEAD
```

For a multi-commit changeset, compare the complete range against the destination
head reported by the CLI rather than only `HEAD^`.

Then run the repository's native formatting, unit, integration, and hardware
checks. The core proves Git structure; it does not prove runtime correctness.
Do not restore the output's push URL during local review.

## Handle dependencies and conflicts

Dependencies must be explicit. The controller recognizes canonical
`Depends-On:` pull-request or full-commit URLs and additive, reviewed train
configuration overrides. It builds a bounded acyclic graph, evaluates
prerequisites before the requested change, and reports the resulting
`prerequisite_order` and `prerequisite_results`.

Each train selects one reviewed dependency mode. In `gate`, any unmet
prerequisite stops the root. In `managed_stack`, the result includes complete,
independently fingerprinted `managed_frontier_results` for only the next
topologically unblocked wave. Materialize and run native tests for each
frontier item separately. Rerun the root plan only after the approved wave is
actually represented in its configured destination. A local checkout is not
containment proof, and the tool never skips ahead, resolves conflicts, or
promises atomic cross-repository publication.

For cross-repository dependencies, supply every repository mapping:

```bash
--repo-dir ROCm/TheRock=/absolute/path/to/TheRock \
--repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
--repo-dir ROCm/rocm-libraries=/absolute/path/to/rocm-libraries
```

The CLI does not infer dependencies from titles, Jira text, chat guidance,
nightly-build propagation, or similar patches. If operational guidance names a
required sequence that is absent from the PR's canonical trailers, update and
review the train override before relying on automation.

On `blocked_conflict`:

1. Read `evidence.conflict_paths` and `evidence.conflict_stages`.
1. Confirm the correct source changeset, dependency order, and destination head.
1. Reproduce and resolve the conflict in a separate human-owned checkout.
1. Run native tests and obtain normal code review.
1. Do not teach the engine to silently select conflict resolutions from an
   unrelated historical change.

The local materializer leaves no partial output checkout after a conflict.

## Direct Git checklist

If you deliberately choose direct Git, complete this checklist first:

- [ ] Record the canonical source PR URL.
- [ ] Prove the PR is merged to the configured source branch.
- [ ] Identify the complete merged changeset, not merely the PR head.
- [ ] Record the exact destination head before applying anything.
- [ ] Verify every prerequisite and its order.
- [ ] Prove the destination does not already contain the complete change.
- [ ] Inspect open PRs targeting the destination for equivalent work.
- [ ] Create a separate local branch from the destination.
- [ ] Use `git cherry-pick -x` for every commit.
- [ ] Inspect the complete resulting diff and tree.
- [ ] Run repository-native tests.
- [ ] Keep any future pull request in draft until operator review is complete.

If any item cannot be proven quickly, use the CLI.

## Remote draft boundary

`local-materialize` is non-writable. It records
`local_only_operator_request`, which every remote writer rejects. It cannot
push, label, comment, publish status, dispatch a workflow, or create a PR.

A future remote draft requires a separate command, a reviewed plan artifact, a
train explicitly reviewed in `create-draft` mode, exact write-time replanning,
and literal operator authorization. That action is outside this local-review
procedure.

When separately authorized, both the GitHub App and local-gh writers render a
draft description containing `Commands executed to create the cherry-pick` and
the exact command list used for materialization. Neither writer can mark the PR
ready, approve it, merge it, enable auto-merge, force-update an existing branch,
or delete remote state.

## Troubleshooting

### `ref_fetch_failed` or missing pull ref

Confirm the supplied clone's `origin` is the normal repository capable of
fetching `refs/pull/NUMBER/head`, the configured source branch, and the
configured destination branch. Restricted replay mirrors commonly omit pull
refs and correctly fail closed.

### Output repository already exists

Choose a new absent `--output-repo` path. The CLI never overwrites or repairs an
existing local directory because it may contain operator work.

### Invalid repository mapping

Use the exact configured repository identity:

```text
ROCm/TheRock=/absolute/path/to/TheRock
ROCm/rocm-systems=/absolute/path/to/rocm-systems
ROCm/rocm-libraries=/absolute/path/to/rocm-libraries
```

Do not mix an unqualified path with multiple `OWNER/REPO=PATH` mappings.

### Authentication unavailable

#### First Release Hub login

The packaged skill requires an exact train record from Release Hub. If no API
token is configured, open:

<https://developer-central.amd.com/settings/api-tokens>

Sign in with AMD SSO, create the **ROCm Cherry-Pick CLI** preset with only
`read:evidence`, copy the token from its one-time display, and return to the
CLI's hidden prompt:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth login
```

Do not put the token on the command line. Non-interactive automation may use a
mode-0600 token file with `--token-file` or the
`ROCM_RELEASE_HUB_TOKEN` environment variable. Login validates the token
against `/api/v1/auth/session` before storing it in the same API-keyed private
credential file used by `rrh`.

Run `gh auth status` and confirm the active host is `github.com`. The local
adapter refuses GitHub Actions, another host, missing credentials, or a token
containing invalid whitespace.

### Already contained or covered by an existing PR

Do nothing. These are successful suppression decisions, not failures. Review
the containment or coverage evidence if the result is surprising; do not create
a duplicate branch.

### CI or semantic readiness is unknown

That is expected. The result intentionally reports `not_evaluated` and
`human_review_required`. Run repository-native CI and obtain human review.

## What the CLI does not prove

The CLI does not guarantee that a change is bug-free, appropriate for the
release, ABI-compatible, performant, or validated on required hardware. It does
not run repository-native CI, resolve conflicts, infer undocumented ordering,
query Jira, trust Release Hub propagation as containment, or decide that a
draft is ready to merge.

For a known single commit with fully trusted surrounding facts, direct Git is
faster. The CLI is valuable when those surrounding facts need to be established
deterministically and retained for review.

## Related documentation

- [Product requirements](product-requirements.md)
- [Technical design](technical-design.md)
- [Operator runbook](runbook.md)
- [Implementation audit](implementation-report.md)
- [TDD evidence](tdd-evidence.md)
- [Remote actions TODO](REMOTE_ACTIONS_TODO.md)
