---
name: rocm-cherry-pick
description: Deterministically evaluate and locally materialize a merged ROCm pull request for one exact Release Hub train. Use for ROCm/TheRock, ROCm/rocm-systems, or ROCm/rocm-libraries cherry-pick readiness, dependency ordering, containment, conflict diagnosis, and a reviewable local checkout. The packaged command performs no remote writes.
license: MIT
metadata:
  author: jusharri
  version: 1.0.0
  category: development
  universal: true
  tags:
    - rocm
    - git
    - cherry-pick
    - release
    - devops
---

# ROCm Cherry-Pick

Use the packaged command as the only decision path. It combines an exact,
complete configuration snapshot from Developer Central's
`/api/v1/cherry-pick/config` endpoint with current GitHub metadata and the
network-free Git core. It works without a rockrel checkout and has no remote
branch, pull-request, label, comment, approval, or merge operation.

## Safety boundary

- Never substitute a nightly-build result for the exact destination branch.
- Never call `gh api` or assemble a manual `git cherry-pick` as a second
  decision path. The command already uses the engineer's `gh` credential for
  read-only PR metadata and exact Git refs.
- Use an existing disk-backed scratch directory, not a memory-mounted temp
  filesystem.
- Developer Central is the sole runtime train/configuration authority. Require
  `cherry-pick-config.v1`, its `release-trains.v5` source, and the returned
  SHA-256. Never use a bundled catalog, cached last-known-good configuration,
  string-derived branch, or manually supplied destination as a fallback.
- A `blocked_*`, `already_contained`, `covered_by_existing_pr`,
  dependency-gap, ambiguous-coverage, or conflict result is final for that run.
  Do not create the output checkout.
- This package cannot create or update a remote branch or pull request. Any
  later remote action requires a separate reviewed workflow and explicit
  authority outside this skill.

## First login

If `auth status` says a Release Hub token is missing:

1. Direct the engineer to
   `https://developer-central.amd.com/settings/api-tokens`.
1. Have them create the **ROCm Cherry-Pick CLI** preset with exactly
   `read:evidence` and an appropriate short expiry.
1. The page shows the raw token once. Do not ask the engineer to paste it into
   chat, a command line, an issue, or a pull request.
1. Have the engineer run the hidden-prompt login locally:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py auth login
```

The command validates `/api/v1/auth/session` before saving an `rrh-auth.v1`
credential with private permissions. `ROCM_RELEASE_HUB_TOKEN` is supported for
ephemeral automation and takes precedence over the file, but tokens are never
accepted in argv or printed.

## Prerequisites

- Python 3.10 or newer, Git, and GitHub CLI.
- `gh auth status` succeeds for `github.com`.
- A fresh local checkout of every repository named by the source or its
  dependency graph. No rockrel checkout is needed.
- An existing absolute disk-backed scratch directory and a non-existing output
  checkout path.

## Plan first

Run from any directory, using the installed skill path:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py plan \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/path/to/disk-backed/cherry-pick-scratch \
  > /absolute/path/to/reviewed-plan.json
```

Supply another `--repo-dir OWNER/REPO=PATH` for every cross-repository
prerequisite reported by the command. Review the exact train source hash,
destination head, ordered prerequisites, containment proof, conflict paths,
planned tree, and commands in the JSON. The Release Hub branch-creation SHA is
provenance only; the Git adapter independently fetches the current branch head.

The train's reviewed `dependency_mode` controls an incomplete stack:
`gate` stops at the root with `awaiting_dependencies`; `managed_stack`
returns `managed_frontier_results` for only the next topologically unblocked
wave. Each frontier item is a complete, independently fingerprinted draft plan.
Materialize and test frontier items individually, then rerun `plan` after
their known-good changes reach the configured destinations. Never skip ahead
or infer ordering from PR numbers, timestamps, or repository names.

## Materialize locally

After reviewing the plan, rerun through the same engine:

```bash
python3 /path/to/rocm-cherry-pick/scripts/rocm_cherry_pick.py materialize \
  --source-pr https://github.com/ROCm/rocm-systems/pull/10031 \
  --train 10.1-20260811 \
  --repo-dir ROCm/rocm-systems=/absolute/path/to/rocm-systems \
  --scratch-root /absolute/path/to/disk-backed/cherry-pick-scratch \
  --output-repo /absolute/path/to/rocm-systems-pr-10031-local \
  --branch local/cherry-pick/10.1-20260811/10031
```

The command creates an independent checkout only for `draft_planned`, disables
its origin push URL, applies the reported `git cherry-pick -x` sequence with
hooks and prompts disabled, and requires the final tree to equal
`planned_tree`. Its JSON contains the exact commands executed, including the
selected mainline only when the Git object is actually a merge commit. Preserve
those commands verbatim in any later pull-request description.

## Agent interaction contract

When the user invokes `$rocm-cherry-pick`:

1. Echo the exact packaged CLI command before executing it.
1. Run `plan` first and summarize the status, destination branch and head,
   complete changeset, ordered dependencies, containment or existing-PR
   coverage, conflicts, planned tree, and next action.
1. Stop for human review. Do not infer permission to materialize from the
   planning request.
1. Run `materialize` only after the user confirms the reviewed
   `draft_planned` result and supplies a new output path and local branch.
1. Return the exact JSON status and commands, explicitly verify
   `tree == planned_tree`, and name the repository-native checks still needed.
1. Never ask for a token in chat. Direct first-time users to Developer Central
   and have them run the hidden-prompt login on their own terminal.

The short user-facing prompts and command sequence are in the
[ROCm cherry-pick user guide](../../docs/cherry-pick-automation/README.md).

## Interpret and hand off

Run repository-native tests in the output checkout, inspect the diff and commit
trailers, and retain both JSON artifacts for human review. This proves Git
applicability and exact tree identity; it does not prove semantic correctness,
CI success, dependency ownership, or release approval.

Read [the operator guide](references/operator-guide.md) for statuses,
dependencies, rotation, and troubleshooting. Read
[the threat model](references/threat-model.md) before changing authentication,
network, Git, or packaging behavior.
