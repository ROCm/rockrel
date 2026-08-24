# Threat model

## Protected assets

- Release Hub bearer token and Developer Central identity.
- GitHub CLI credential and current repository refs.
- Exact train, destination branch/head, dependency graph, patch identities,
  plan fingerprint, and resulting tree.
- The engineer's existing checkout and any local output checkout.

## Trust boundaries

Developer Central and Release Hub are the read-only runtime configuration
authority through `/api/v1/cherry-pick/config`. GitHub supplies PR metadata
through the local `gh` credential. Git is the authority for current refs,
patch equivalence, conflicts, application, and tree identity. The SLAI agent
selects inputs and explains results but is not an eligibility authority. Jira
and LLM output are outside the core.

## Controls

- HTTPS is mandatory except explicit loopback development. Redirects are not
  followed with bearer credentials, responses are size bounded, and failures
  are sanitized.
- Tokens are accepted only from an environment variable, hidden prompt,
  private stdin/file, or the private `rrh-auth.v1` store. They never appear in
  argv, repr output, URLs, result JSON, or bundle manifests.
- Train resolution requires an exact ID, a complete
  `cherry-pick-config.v1` response sourced from `release-trains.v5`, a valid
  SHA-256, configured/enabled state, ready branches, and exactly one confirmed
  created release destination for the source repository.
- No catalog or destination is bundled with the package. Missing, stale,
  malformed, or unauthorized configuration fails closed; there is no
  last-known-good cache, string manipulation, caller override, or branch
  inference.
- Release Hub's creation SHA is provenance. The engine fetches and pins the
  current destination head independently.
- The control-plane snapshot is deep-copied into local authorization and bound
  to the plan fingerprint. Any changed source hash or branch changes the plan.
- Git runs with prompts and hooks disabled. Trial worktrees are isolated under
  an explicit disk-backed root. Local materialization uses no hardlinks and a
  disabled push URL.
- The packaged runtime is an explicit allowlist with a SHA-256 manifest. Writer,
  feedback, GitHub Actions, and draft-creation modules are excluded.
- Bundle provenance hashes the exact packaged source closure before scanner
  metadata is stripped or other output transformations occur. A clean-commit
  state requires a clean Git status for that closure; dirty input requires an
  explicit review-only build flag and produces `dirty_worktree_review`, which
  is not publishable evidence.

## Residual risks

Git applicability cannot prove semantic correctness, test success, ABI/API
compatibility, undocumented dependencies, or release approval. A compromised
local machine can read process memory and credentials. A compromised Release
Hub can return a signed-in but wrong train configuration; independent Git
proof limits the effect to the selected branch but cannot establish business
intent. Human review, native tests, short token lifetimes, and separate remote
write authorization remain mandatory.
