# Express Train cherry-pick automation implementation report

## Delivered

- Product requirements, technical design, and operator runbook were committed
  before production code.
- Train configuration and label parsing are fail-closed.
- Qualification validates repository, base branch, label actor permission, Jira
  Fix Version, target existence, and target protection.
- Git planning uses disposable worktrees for contained, clean, empty, conflict,
  merge-commit, and gitlink cases.
- GitHub and Jira clients use injectable transports and bounded requests.
- Existing identity markers, deterministic branches, ordinary patch coverage,
  and gitlink cherry-pick provenance prevent duplicate PRs.
- The write path uses a target-head lease, `git cherry-pick -x`, a deterministic
  branch, and GitHub's `draft: true` API field.
- The implementation has no ready-for-review, review, merge, or auto-merge API.
- Reusable, reconciliation, label-sync, and source-template workflows pin every
  external action to a full SHA and pass actionlint.
- Cross-repository planning and reconciliation use permission-narrowed,
  read-only App tokens; feedback and draft writes use separate gated tokens.

## Test-first record

The Git history preserves red tests before each implementation slice:

| Test commit | Green implementation |
| --- | --- |
| `324b48e` configuration contract | `cb99967` configuration loader |
| `fae38bb` qualification policy | `bb2eb40` qualification implementation |
| `dafd198` Git decisions | `f0f7dd8` disposable Git planner |
| `3afafc1` API contracts | `1a06ed4` GitHub/Jira clients |
| `32ed8fc` orchestration | `b3938a6` request planner |
| `5f48c8b` write transaction | `3b68e0b` draft writer |
| `6b98342` CLI contract | `14e0b89` CLI |
| `100d24b`, `e5f87a3`, `3fd5865` workflows/reconciliation | `38441df` workflows |
| `a19b8da` unlabeled cancellation | `d3c6ded` event handling |
| `10c4b70`, `1187b5c` covering evidence | `82cefb6` coverage detector |
| `86b2c05` immutable renderer | `66ca3cd` renderer |
| `9fb1439` validation-mode token boundary | `f3438b6` conditional write-token job |
| `7b2e3bc` least-privilege App contract | version-controlled App manifest |
| `d95e31c`, `df3343b` cross-repository token boundaries | `d047503` permission-narrowed workflow tokens and artifact feedback |

Each red state was run locally and failed for the intended missing module,
interface, or workflow behavior. Red commits were not pushed independently.

## Final verification

Run from the rockrel checkout:

```text
.venv/bin/python -m pytest -q scripts/tests
.venv/bin/pre-commit run --all-files
git diff --check
```

Results on 2026-08-14:

- 139 tests passed.
- Trailing whitespace, EOF, YAML, merge-conflict, large-file, line-ending,
  no-tabs, and actionlint hooks passed.
- The seven 0811 requests are captured in
  `scripts/tests/fixtures/express_train_0811.json`.
- Live read-only validation proved all six ordinary cases through an empty
  trial application against their covering PR heads.
- Live read-only validation of TheRock #7282 against #7357 returned
  `gitlink_cherry_pick_provenance`, using desired pin `a01cdbd92d1f`, covering
  pin `d177931e65e6`, and common original commit `1109d68feb1b`.

## Activation prerequisites

Engineering implementation does not itself grant production authority. No
public GitHub action should be taken from this local checkout. The ordered
operator actions are recorded in `operator-todo.md`. Before
write mode can run, an ROCm organization administrator must:

1. Install the dedicated GitHub App on rockrel, TheRock, rocm-systems, and
   rocm-libraries with the permissions in the technical design.
2. Configure the four selected-repository secrets documented in the runbook.
3. Review and merge the central rockrel workflow.
4. Review source-repository caller PRs pinned to the immutable central commit.
5. Run validation and shadow modes before changing a train to `create-draft`.

All initial train configuration remains in `validate` mode. No production
cherry-pick branch can be created from the committed configuration.
