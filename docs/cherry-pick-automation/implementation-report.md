# Draft — local review required

# Label-driven cherry-pick automation: implementation audit

## Current verdict

The remediation implementation is complete for local human review. It is not
deployed and is deliberately incapable of performing a public write: every
workflow write job has an impossible repository gate, committed train modes are
non-writing, default API transports deny network access, and the normal CLI
cannot construct a writer capability.

The controller and its callers now have 254 rockrel unit tests and 11
source-caller tests passing. The 77-case standalone historical replay also
passes with 31 strict exact trees and 46 reviewed diagnostics. The remaining
activation blocker is measurement of the configured
90% line/branch coverage threshold: `pytest-cov`/`coverage.py` is not installed
in any available local Python environment, and the no-network boundary forbids
downloading it. The unit-test workflow enforces the threshold when a separately
approved public CI run becomes available. This is an explicit unverified gate,
not a claimed pass.

## Baseline evidence

Recorded before remediation product-code changes:

```text
.venv/bin/python -m pytest -q scripts/tests
165 passed

git diff --check
passed

.venv/bin/python -m black --check ...
not run: Black was not installed in the existing local virtual environment
```

No dependency was downloaded. Repository-pinned tools already present in the
pre-commit cache were used for Black 25.11.0, mdformat 0.7.21, and actionlint
1.7.10.

## Remediation results

| Area                  | Implemented behavior and local evidence                                                                                                                                                         | State          |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| Source merge model    | `prove_changeset` proves squash, two-parent merge, and complete ordered rebase ranges; merge and partial-range fixtures pass                                                                    | Closed         |
| Git identity          | Writer configures the explicit ROCm automation committer name and noreply email; writer identity test passes                                                                                    | Closed         |
| Fresh-runner recovery | Existing branches resolve by exact SHA and tree rather than an assumed local branch; fresh-clone recovery test passes                                                                           | Closed         |
| Partial write         | A pushed branch plus failed PR API call returns `retryable_partial_write`; replay repairs only an exact tree/identity                                                                           | Closed         |
| Destination policy    | Planning requires typed effective `pull_request` rule evidence, not only `protected: true`                                                                                                      | Closed         |
| Branch names          | Schema v3 accepts any canonical `git check-ref-format --branch` ref; no release prefix is encoded                                                                                               | Closed         |
| API enumeration       | Timelines, PR commits, destination pulls, comments, and search pages continue until exhaustion                                                                                                  | Closed         |
| API failures          | GitHub retryable/rate-limit responses use bounded injected backoff; other GitHub and Jira failures block as evidence                                                                            | Closed         |
| API types             | Branch, effective-policy, Jira, result, configuration, and changeset boundaries are typed; flexible GitHub PR/compare payloads are shape-checked at their consuming adapter/controller boundary | Closed         |
| Modes                 | `disabled`, `validate`, `shadow`, and `create-draft` have distinct tested behavior                                                                                                              | Closed         |
| Dependencies          | Structured PR trailers and Jira dependency/order evidence block before Git planning                                                                                                             | Closed         |
| Containment           | Only the complete proven changeset can be contained; exact full, patch-equivalent partial, conflict, and gitlink cases pass                                                                     | Closed         |
| Caller logic          | All three source repositories contain one generated, immutable-SHA reusable-workflow call; discovery/fan-out is central                                                                         | Closed         |
| Caller tests          | Repository-local contract tests cover event metadata, pin equality, secrets, and read-only permissions; actionlint parses every changed workflow                                                | Closed         |
| Style                 | Branch-modified Python passes pinned Black; new automation files have SPDX headers; Markdown and workflows use repository tools                                                                 | Closed         |
| Draft body            | Generated body includes source/destination proof, Jira, dependencies, application, tests, checklist, warning, and identity                                                                      | Closed         |
| Security              | App manifest omits administration/Actions/Workflows; privileged event paths never execute PR-head code                                                                                          | Closed         |
| Local safety          | Network-denying defaults, non-writing train modes, impossible write-job gates, and explicit writer capability are tested                                                                        | Closed         |
| Historical replay     | 77/77 transitions classified; 31/31 strict trees pass; 46 diagnostics include conflicts, adaptations, bundles, reverts, gitlinks, and release-native changes                                    | Closed         |
| Replay rollback       | Persistent disk indexes, atomic snapshots, corruption recovery, automatic per-case cleanup, and standalone rollback are unit/integration tested                                                 | Closed         |
| Coverage              | CI enforces 90% line and branch coverage, but the local measurement tool is unavailable and cannot be downloaded                                                                                | **Unverified** |

## Repository evidence reflected in the implementation

- `rockrel` centralizes release branch/tag operations and uses typed Python,
  dataclasses, subprocess argument arrays, dry-run defaults, and pytest.
- TheRock requires Black/PEP 8 style, modern specific typing, fail-fast behavior,
  SPDX headers, pre-commit, actionlint, and `*_test.py` for this work.
- rocm-systems and rocm-libraries keep their native validation and component CI;
  the automation creates only a normal draft and does not replace those checks.
- Local refs confirm the BKC branch name is
  `release/bkc/therock-10.1-20260811` in all three destination repositories;
  other observed release lines include `release/rocm-rel-*` and
  `release-staging/rocm-rel-*`.
- Existing BKC effective rules require pull requests, approvals, and restricted
  merge behavior. A boolean protected flag is not sufficient policy evidence.
- Existing manual cherry-pick drafts include provenance, candidate/destination,
  Jira, technical application details, test plan/results, dependencies/order,
  and an explicit draft warning.

## TDD remediation record

The work followed the required sequence:

1. Correct product/design/audit documents.
1. Add the complete remediation tests without product changes.
1. Record 112 intended failures and 86 unaffected passes.
1. Implement the central controller until 198 tests passed.
1. Add a second red slice for patch-equivalent partial containment, abandoned
   drafts, existing-tree mismatch, lookup failure, and rate-limit `403`.
1. Implement that slice, format with the pinned repository tool, and finish at
   203 passing tests.
1. Generate thin callers and pass all 11 repository-local caller tests.
1. Add the historical replay contract red-first, freeze all 77 transitions,
   and close every evidence gap with positive provenance or reviewed diagnostic
   classification.
1. Add persistent rollback/index-corruption tests red-first, then finish with
   254 unit tests and the standalone historical suite green.

Full commands and commit boundaries are recorded in `tdd-evidence.md`.

## Activation boundary

All implementation changes, local commits, fixtures, and evidence remain only
under `/home/jusharri/code/label-driven-cherrypick-automation`. The sole network
operation was the documented, read-only hydration of exact official Git refs
into dedicated local mirrors. No GitHub/Jira mutation, push, workflow dispatch,
public CI run, public branch, label, comment, App setting, secret, or pull
request was performed. Those tasks are listed—but not executed—in
`REMOTE_ACTIONS_TODO.md`.
