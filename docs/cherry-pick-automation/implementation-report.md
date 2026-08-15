# Draft — local review required

# Label-driven cherry-pick automation: implementation audit

## Current verdict

The local implementation is a useful prototype, not a production-ready
controller. Its central rockrel ownership, label/train model, injectable REST
transport, disposable Git worktrees, deterministic identity, and draft-only API
are worth retaining. The current documentation and 165 passing tests, however,
do not prove the reliability claims previously made.

No remote deployment or public review action is authorized. This report is the
gap register that must be closed locally before human review.

## Baseline evidence

Recorded before remediation product-code changes:

```text
.venv/bin/python -m pytest -q scripts/tests
165 passed

git diff --check
passed

.venv/bin/python -m black --check ...
not run: Black is not installed in the existing local virtual environment
```

No dependency will be downloaded during the local-only phase. Formatting will
use an already available repository/pre-commit environment if present; otherwise
the missing local tool remains an explicit review limitation.

## Confirmed gaps

| Area | Current behavior | Required behavior | State |
| --- | --- | --- | --- |
| Source merge model | Treats `merge_commit_sha` as one aggregate commit | Prove squash, merge-commit, or full rebase range | Open |
| Git identity | Relies on ambient Git config | Explicit bot name and noreply email | Open |
| Fresh-runner recovery | Fetches a branch but resolves an absent local branch name | Fetch/resolve exact SHA or temporary ref | Open |
| Partial write | PR API failure escapes after branch push | Structured retryable state and safe repair | Open |
| Destination policy | Checks only `protected: true` | Require effective `pull_request` rule evidence | Open |
| Branch names | Requires `release/` | Accept every canonical Git branch ref | Open |
| API enumeration | Several endpoints stop at 100 | Full pagination and deterministic search windows | Open |
| API failures | Mostly next-run recovery | Typed errors and bounded retry/backoff | Open |
| API types | Raw `dict[str, Any]` throughout | Decode typed boundary models | Open |
| Modes | `validate` and `shadow` are effectively identical; disabled may still plan | Four distinct lifecycle behaviors | Open |
| Dependencies | Jira Fix Version only | Block declared dependency/order evidence | Open |
| Containment | Empty single-commit trial can overstate aggregate certainty | Require complete proven changeset | Open |
| Caller logic | Embedded Python repeated in three repositories | Thin caller; central discovery and fan-out | Open |
| Caller tests | Primarily string assertions | Parsed workflows plus event behavior fixtures | Open |
| Style | New callers fail Black; new files lack headers | Repository-native format, typing, and SPDX | Open |
| Draft body | Minimal provenance plus source body | Match established ROCm cherry-pick structure | Open |
| Security | App requests administration read | Metadata read plus contents/issues/PR permissions only | Open |
| Local safety | Real transports are constructible by default CLI | Local build refuses all network/remote writes | Open |
| Coverage | No enforced threshold | At least 90% line and branch coverage | Open |

## Repository evidence reflected in the design

- `rockrel` already centralizes release branch/tag operations and uses typed
  Python, dataclasses, subprocess argument arrays, dry-run defaults, and pytest.
- TheRock requires Black/PEP 8 style, modern specific typing, fail-fast behavior,
  SPDX headers, pre-commit, actionlint, and `*_test.py` for this work.
- rocm-systems and rocm-libraries use their own native validation and component
  CI; the automation must create a normal draft and must not replace those
  checks.
- Current release branches include `release/bkc/therock-*`, `release/therock-*`,
  `release/rocm-rel-*`, and `release-staging/rocm-rel-*` patterns.
- Existing BKC effective rules require pull requests, approvals, and restricted
  merge behavior. A boolean protected flag is not sufficient policy evidence.
- Existing manual cherry-pick drafts include provenance, candidate/destination,
  Jira, technical application details, test plan/results, dependencies/order,
  and an explicit draft warning.

## TDD remediation record

The historical red/green commits remain useful provenance, but they do not
cover the gaps above. The remediation follows a new, stricter sequence:

1. Correct product/design/audit documents.
2. Add the complete remediation tests without product changes.
3. Record the intended failing assertions in `tdd-evidence.md`.
4. Implement until every old and new test passes.
5. Run local repository style, workflow, and coverage gates.

The final report will replace each `Open` state with a test name, implementation
commit/diff reference, and local verification result. A passing test count alone
is not completion evidence.

## Activation boundary

All implementation changes, commits, fixtures, and evidence remain local. No
GitHub/Jira call, remote Git operation, workflow dispatch, public CI run, branch,
label, comment, App setting, secret, or pull request is authorized. Those tasks
are listed—but not executed—in `REMOTE_ACTIONS_TODO.md`.
