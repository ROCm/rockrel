# ROCm Cherry-Pick operator guide

## Result contract

`plan` returns structured JSON even when Git says no cherry-pick should be
created. Review `status`, `reason_code`, `destination_branch`, and `evidence`.

- `draft_planned`: the ordered changes apply cleanly to the exact current
  destination head and produce `planned_tree`.
- `already_contained`: exact ancestry or attributed tree evidence proves the
  complete change is already represented in the destination branch.
- `covered_by_existing_pr`: exactly one attributed open destination PR has
  the planned tree, so review that PR and create no output checkout or
  duplicate.
- `blocked_conflict`: the disposable trial reports conflicted paths and stages;
  no output checkout is created.
- `awaiting_dependencies` with `managed_dependency_frontier`: inspect
  `managed_frontier_results`; only that next dependency wave is eligible.
- `blocked_dependency`: the prerequisite graph is incomplete, ambiguous,
  cyclic, too large, or not proven contained/applied in order.
- `blocked_evidence`: current refs, merge identity, branch protection, covering
  PR state, or Release Hub train facts could not be proven.
- `ineligible_source`: the source is unmerged, unsupported, or was merged into
  a branch outside the reviewed source-branch policy.

An invocation error exits 2. A blocked `materialize` exits 1 after printing the
result. A successful local checkout exits 0 with `status=local_materialized`.

## Dependencies and ordering

The engine parses typed dependency trailers and applies only the dependency
overrides present in the exact Developer Central configuration snapshot for
the source PR and train. It topologically orders prerequisites before the
source change, including standalone Git commits and cross-repository edges.
Provide a local checkout mapping for every repository named by the graph. Do
not infer an order from PR numbers, merge dates, branch names, or repository
names.

If a dependency is already contained, it remains in the proof but is not
reapplied. If a prerequisite conflicts, the source is not tried out of order.
Update the reviewed Developer Central configuration source for a missing
dependency edge; do not edit the generated skill bundle or supply an ad hoc
destination.

`dependency_mode=gate` reports an incomplete prerequisite stack and will not
plan a root draft. `dependency_mode=managed_stack` evaluates all destinations
at their current immutable heads and returns only the next topologically
unblocked `managed_frontier_results`. Each item carries its own source
identity, destination, ordered commits, planned tree, coverage digest, request
manifest, and plan fingerprint. Materialize/test one item at a time. After the
approved wave is actually represented in its configured destination, rerun the
original root plan; never treat a locally materialized wave as proof that later
nodes are contained.

The local package never creates drafts. The separately authorized GitHub
Actions integration may create draft PRs wave-by-wave, but it must revalidate
the exact frontier authority immediately before each write.

## Credentials

```bash
python3 scripts/rocm_cherry_pick.py auth status
python3 scripts/rocm_cherry_pick.py auth login
python3 scripts/rocm_cherry_pick.py auth logout
```

Login uses a hidden prompt by default. `--stdin` is suitable for an already
protected pipe. `--token-file` accepts only a private, regular, non-symlink
file. The default credential is
`~/.config/rocm-cherry-pick/auth.json`; set `ROCM_CHERRY_PICK_AUTH_FILE` to
choose another private path. Set `ROCM_RELEASE_HUB_API` only for a reviewed
alternate origin; plain HTTP is accepted only on loopback.

Rotate before the expiry warning becomes urgent: create and validate the new
token, then revoke the old token in Developer Central. Never store a token in
shell history, Git configuration, a PR, logs, or a JSON plan.

If `/api/v1/cherry-pick/config` is unavailable, stale, malformed, incomplete,
not `cherry-pick-config.v1`, not sourced from `release-trains.v5`, or has an
invalid SHA-256, stop. The package deliberately has no embedded configuration,
cache, branch-name inference, or destination override.

## Local checkout review

The output repository is cloned with no hardlinks, starts at the immutable
destination SHA, has hooks disabled during application, uses a local-only
committer identity, and has `origin` push disabled. Review:

```bash
git -C /absolute/output status --short
git -C /absolute/output log --oneline --decorate -n 12
git -C /absolute/output diff <destination-sha>..HEAD --stat
git -C /absolute/output remote -v
```

Then run the source repository's native unit, build, and integration tests.
Never treat `draft_planned` alone as semantic release approval.

## Troubleshooting

- Missing token: create **ROCm Cherry-Pick CLI** with `read:evidence` at
  `https://developer-central.amd.com/settings/api-tokens`, then run `auth login`.
- `gh` failure: run `gh auth status` and confirm the active host is
  `github.com`.
- Train unavailable: verify the exact train ID in Developer Central. Planned,
  invalid, disabled, unconfirmed, or ambiguous branches fail closed.
- Missing local repository: add the exact `OWNER/REPO=PATH` mapping and rerun.
- Stale result: rerun. Source heads, dependency bodies, open covering PRs,
  configuration hash, or destination head changes produce a different plan
  fingerprint.
