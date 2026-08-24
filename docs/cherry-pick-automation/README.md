# ROCm cherry-pick user guide

Use this tool to decide whether a merged ROCm pull request needs a cherry-pick
for one configured release train and, when safe, create a push-disabled local
checkout for review. It supports ROCm/TheRock, ROCm/rocm-systems, and
ROCm/rocm-libraries. A train is a reviewed destination release branch; the tool
is not specific to Express Train.

The packaged interface has three commands: `auth`, `plan`, and
`materialize`. It performs no remote writes: it cannot push a branch or create,
update, approve, or merge a pull request.

## Choose an interface

Use either interface below. Both invoke the same deterministic CLI and core
engine.

- **Local CLI:** best for direct terminal use, scripts, and reproducible JSON.
- **Agent skill:** best when you want an agent to collect the inputs, run the
  CLI, explain its evidence, and guide review. The agent must not replace the
  CLI with manual `gh api` or `git cherry-pick` decisions.

For the full result model, dependency modes, conflict procedure, and direct-Git
comparison, read the [complete user manual](user-manual.md).

## Before you start

You need:

- Python 3.10 or newer, Git, and GitHub CLI;
- `gh auth status` succeeding for `github.com`;
- a normal local clone of every repository involved in the change;
- an existing, disk-backed scratch directory; and
- a Release Hub token containing exactly `read:evidence`.

For first login, open
[Developer Central API tokens](https://developer-central.amd.com/settings/api-tokens),
select the **ROCm Cherry-Pick CLI** preset, choose an appropriate short expiry,
and create the token. The raw token appears once. Do not paste it into chat,
argv, an issue, or a pull request.

Enter it only through the hidden local prompt:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth login
```

Confirm both credentials before planning:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth status
gh auth status
```

## Use the local CLI

The examples below evaluate PR 10031 for train `10.1-20260811`. Replace every
example path with an absolute path on your workstation.

First, plan without creating an output checkout:

```bash
mkdir -p /absolute/disk/path/cherry-pick-scratch

python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py plan \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/disk/path/cherry-pick-scratch \
  > /absolute/path/to/reviewed-plan.json
```

Review `reviewed-plan.json`. Confirm the source representation, configured
destination branch and exact head, dependency order, containment result,
conflict evidence, planned tree, and commands. Continue only when the top-level
status is `draft_planned`.

Then materialize through the same engine:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py materialize \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/disk/path/cherry-pick-scratch \
  --output-repo /absolute/path/to/rocm-systems-pr-10031-local \
  --branch local/cherry-pick/10.1-20260811/10031 \
  > /absolute/path/to/materialized-result.json
```

For cross-repository dependencies, repeat `--repo-dir OWNER/REPO=PATH` for
each repository reported by the plan. The output path must not already exist.

A successful result reports `local_materialized`, an exact destination head,
a final `tree` equal to `planned_tree`, and the ordered
`git cherry-pick -x` commands actually executed. The independent output
checkout has its push URL set to `disabled://local-only`.

Review it normally:

```bash
cd /absolute/path/to/rocm-systems-pr-10031-local
git status --short
git log --oneline --decorate -n 10
git diff <destination-head>..HEAD
```

Run the repository's native tests. The engine proves Git applicability and tree
identity; it does not prove semantic correctness, CI success, hardware
validation, or release approval.

## Use the agent skill

Invoke the installed skill explicitly as `$rocm-cherry-pick`. Give the agent
the PR URL, exact train ID, repository mappings, and a disk-backed scratch path.

Start with a planning request:

```text
Use $rocm-cherry-pick to plan
https://github.com/ROCm/rocm-systems/pull/10031 for train 10.1-20260811.
Use ROCm/rocm-systems=/absolute/path/to/rocm-systems and
/absolute/disk/path/cherry-pick-scratch. Perform no remote writes. Show the
exact CLI command, then summarize the status, destination branch and head,
changeset, dependencies, containment, conflicts, planned tree, and next action.
```

The agent should stop after the plan and ask you to review it. If the result is
`draft_planned`, request local materialization explicitly:

```text
Use $rocm-cherry-pick to materialize the reviewed draft_planned result with the
same PR, train, repository mappings, and scratch directory. Create
/absolute/path/to/rocm-systems-pr-10031-local on local branch
local/cherry-pick/10.1-20260811/10031. Perform no remote writes. Report the exact
CLI command and output, verify tree equals planned_tree, and list the native
tests I should run.
```

If authentication is missing, the agent must direct you to Developer Central;
it must never ask you to paste the token into chat. If the CLI returns a
`blocked_*`, `already_contained`, `covered_by_existing_pr`, or dependency
status, the agent must explain that result and must not create an output
checkout.

The skill instructions are in
[SKILL.md](../../skills/rocm-cherry-pick/SKILL.md), with detailed status and
troubleshooting guidance in the
[operator guide](../../skills/rocm-cherry-pick/references/operator-guide.md).

## Decide what to do with the result

| Result | Next action |
| --- | --- |
| `draft_planned` | Review the plan, then explicitly run or request `materialize`. |
| `local_materialized` | Inspect the checkout, run native tests, and retain both JSON artifacts. |
| `already_contained` | Do nothing; the complete change is already in the destination. |
| `covered_by_existing_pr` | Review the reported existing PR; do not create a duplicate. |
| `awaiting_merge` or `awaiting_dependencies` | Wait for the reported source or prerequisite state. |
| `blocked_conflict` | Inspect the conflict paths and resolve only in a separate human-owned checkout. |
| Any other `blocked_*` or `ineligible_source` | Correct the reported evidence, configuration, or request; do not bypass the decision. |

## Safety and publication boundary

Local use intentionally stops before publication. It does not restore the
output remote, push, create a draft PR, change labels, publish checks, or move a
draft to ready-for-review. Preserve the exact command list from the JSON for any
later, separately authorized pull-request description.

The checked-in skill bundle is currently a local-review artifact until its
clean-commit build, hosted validation, security review, and Marketplace
publication gates are complete. Use only a reviewed bundle or an installed
approved version.

## More documentation

- [Complete user manual](user-manual.md)
- [Technical design](technical-design.md)
- [Threat model](../../skills/rocm-cherry-pick/references/threat-model.md)
- [Operator runbook](runbook.md)
- [Implementation status](implementation-report.md)
