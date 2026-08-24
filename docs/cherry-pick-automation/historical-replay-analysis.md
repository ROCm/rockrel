# Draft — local review required

# Historical replay results and gap analysis

## Verdict

The current Git changeset/application layer reproduces every reviewed, strictly
eligible historical cherry-pick in the pinned corpus. It also returns the
expected non-writing result for every reviewed negative or non-applicable row.
This is strong local evidence for that layer; it is not an end-to-end replay of
historical GitHub authorization/workflow facts and is not evidence that the
automation is ready for public activation.

The deep run contains 77 first-parent release transitions across TheRock,
rocm-systems, and rocm-libraries on `release/therock-7.12`,
`release/therock-7.14`, and `release/therock-10.0`:

| Classification        | Rows | Execution            | Result                                                                                        |
| --------------------- | ---: | -------------------- | --------------------------------------------------------------------------------------------- |
| Strict exact          |   31 | Core plus post-merge | 31 exact historical trees; 31 contained reruns                                                |
| Historical adaptation |    5 | Core diagnostic      | Five clean plans: four differ from the known-good tree; one lacks qualifying merge provenance |
| Manual resolution     |    3 | Core diagnostic      | Expected conflicts with exact conflicted paths                                                |
| Gitlink rollup        |   15 | Inventory only       | No single canonical source changeset; not counted as engine coverage                          |
| Release-native        |   13 | Inventory only       | Negative control; no source request                                                           |
| Multi-source bundle   |    6 | Inventory only       | Unsupported by v1                                                                             |
| Target-only revert    |    4 | Inventory only       | Negative control; no source request                                                           |

The 39 core rows produced 36 clean `draft_planned` results and three expected
`blocked_conflict` results. The 31 strict rows reproduced the exact known-good
tree from the historical pre-merge parent. Re-evaluation at both the known-good
commit and pinned release tip returned `already_contained` in all 31 cases: 20
through complete changeset patch identity and 11 through a reachable,
source-identified destination application whose first-parent replay exactly
matched its commit tree.

Those 20 patch-identity reruns are useful historical oracle evidence, but they
do not weaken the production decision policy. `CorePlanner` converts an empty
application without exact ancestry or strongly attributed destination
provenance to `blocked_ambiguous_changeset / patch_equivalent_review_required`. Newly generated drafts retain `-x` identity;
unattributed historical equivalents require human semantic review.

There were zero reviewed-expectation mismatches and zero combined coverage
gaps. The report therefore exits zero. It separately retains 21
historical-only gaps; named deterministic tests cover those cells without
pretending that they occurred in the historical corpus.

The independently enforced post-rebuild matrix passes all 1,036 tests with no
deselections on Python 3.10.20, 3.11.15, and 3.12.13. All three reports are
identical: 5,470/5,703 lines (95.9144%) and 1,818/1,982 branches (91.7255%)
across `scripts/cherry_pick`, the skill builder, and the integration checker.
The exact gate also passes all 25 named critical modules at or above 90% in both
dimensions without relying on pytest-cov's whole-number rounding. Hosted matrix
evidence from the eventual clean reviewed revision remains pending and does not
alter the historical replay classification above.

## Expected negative behavior

The three conflicts are successful fail-closed outcomes, not replay failures:

| Repository/line     | Conflict paths                                                                                                  |
| ------------------- | --------------------------------------------------------------------------------------------------------------- |
| TheRock 7.12        | `.github/workflows/release_portable_linux_packages.yml`                                                         |
| rocm-libraries 7.14 | `projects/miopen/src/hip/handlehip.cpp`                                                                         |
| rocm-libraries 10.0 | `projects/hipcub/CHANGELOG.md`, `projects/hipcub/test/hipcub/test_utils.hpp`, `projects/rocthrust/CHANGELOG.md` |

The five historical adaptations are deliberately not promoted to strict
successes. All five source changesets apply cleanly, but four planned trees
differ from the tree that was actually merged; that is evidence of manual or
follow-up adaptation, not success. The fifth reproduces the historical tree but
its PR head is not canonically merged into the pinned source snapshot. These
results are useful diagnostic evidence, but none can authorize a draft.

The 38 inventory-only rows are likewise not engine passes. They prove that the
inventory is exhaustive and that unsupported or non-applicable history remains
visible. In particular, the 15 TheRock gitlink rollups do not contain enough
single-source component evidence for a deterministic end-to-end replay. They
remain an explicit gap rather than being inferred from commit titles or patch
similarity.

## Coverage findings

Historical Git-application evidence covers:

- all three supported repositories;
- single and squash changesets;
- clean planning, exact post-merge containment, and expected conflict results;
- add, modify, delete, and rename operations;
- small, medium, and large textual changes; and
- warm persistent-worktree execution.

The 21 historical-only gaps are:

- destination families: BKC, `release/rocm-rel-*`, release staging, and an
  arbitrary valid branch;
- phases: component, planner, and writer;
- changesets: merge commit and rebase range;
- outcomes: ambiguous changeset, blocked evidence, draft creation, and
  retryable partial write;
- file operations: executable mode, symlink, binary, and gitlink; and
- recovery: fresh, interrupted, corrupt-index, and partial-write paths.

Every cell above maps to one or more concrete pytest node IDs in
`scripts/tests/fixtures/replay_synthetic_coverage.json`. The report keeps those
test IDs separate from historical counts. The full unit suite must still pass;
registration alone is not a substitute for running the tests.

## Fast and deep strategy

The fast gate contains 17 reviewed rows: all five adaptations, all three
historical conflicts, and representative strict/inventory rows spanning every
repository, release line, classification, historical changeset kind, file
shape, and change-size bucket. The current warm parallel rerun produced 17/17
expected results and zero combined coverage gaps.

The deep gate contains all 77 rows. The current schema-v3 parallel and serial
runs both passed and their reports are byte-identical. Both JSON files have SHA-256
`c35e36ed21cc4ff7843a1303ada3c89b7903e7cd513cff912a401af8733848e5`,
and both Markdown files have SHA-256
`ab0341982950ab45daed29751e6aa96cdad8ea957bf1f05ed192596f3c2f2017`.
These are evidence for the current local draft, not permanent corpus goldens.

## Remaining activation blockers

- Historical Git data does not exercise merge-commit or rebase-range source
  representations, non-TheRock destination families, several file modes, or
  recovery paths; those remain synthetic evidence.
- Historical rows exercise inventory, core application, and post-merge
  containment. Planner/writer behavior is covered by the filesystem-only local
  pipeline simulator, not replayed from historical GitHub authorization and
  workflow events.
- Multi-source bundles and historical multi-component gitlink rollups remain
  unsupported/non-writing. A separately proven single-source PR with a gitlink
  is covered by the production classifier tests.
- No public workflow, App installation, label, branch, draft PR, or CI run has
  been exercised or authorized.
- Bundle equality and the final three-version local matrix are green. The rebuilt
  review bundle remains nonpublishable `dirty_worktree_review`; a clean
  reviewed-commit build and hosted validation remain outstanding.
- No real private-sandbox adapter/run or immutable-OIDC repository opt-in has
  been completed.

These blockers do not invalidate the local core-engine result. They do prevent
claiming production readiness or enabling remote writes.
