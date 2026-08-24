# ROCm Cherry-Pick

ROCm Cherry-Pick is a self-contained, local-only skill for release engineers
who need to determine whether a merged pull request can be safely applied to
one exact train destination branch. It runs without a rockrel checkout and
uses only Python's standard library, Git, GitHub CLI, and local repository
checkouts.

The skill reads the complete `cherry-pick-config.v1` snapshot from Developer
Central's `/api/v1/cherry-pick/config` endpoint, binds its
`release-trains.v5` source hash to the plan, independently fetches the current
destination head with Git, discovers ordered prerequisites, proves patch
containment, performs a disposable conflict trial, and optionally creates a
push-disabled local checkout whose final tree must match the plan. Developer
Central is the sole runtime configuration authority: there is no bundled
catalog, last-known-good fallback, string-derived branch, or caller-supplied
destination. The package does not contain a remote writer or a
draft-pull-request command.

First-time users obtain a short-lived **ROCm Cherry-Pick CLI** token with only
`read:evidence` at
`https://developer-central.amd.com/settings/api-tokens`. The token is entered
through a hidden prompt, validated before storage, never accepted on the
command line, and never printed. GitHub access remains separate and uses the
engineer's existing `gh` authentication for read-only metadata.

Reviewed train policy selects either fail-closed dependency gating or managed
stack waves. Managed mode exposes only the next topologically unblocked
frontier; later prerequisites and the root are replanned after prior waves are
known to be present.

Every result is structured JSON. Successful plans include the exact commands
that would be or were executed, destination and source identities, dependency
ordering, plan fingerprints, and the expected tree. Blocked and contained
results stop before creating an output checkout. Human review and
repository-native tests remain required before any separately authorized
remote workflow.
