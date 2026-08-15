# Draft — local review required

# Cherry-pick automation TDD evidence

## Rules

- Documentation is updated before remediation product code.
- The complete remediation suite is added before implementation changes.
- Each red test must fail for the intended missing behavior, not because of a
  syntax error, broken fixture, or accidental network access.
- Product implementation starts only after the red suite and failures are
  recorded below.
- Tests use local filesystem Git repositories and fake transports only.

## Baseline before remediation

Date: 2026-08-15

```text
$ .venv/bin/python -m pytest -q scripts/tests
165 passed in 1.59s

$ git diff --check
passed

$ .venv/bin/python -m black --check scripts/cherry_pick scripts/tests scripts/render_cherry_pick_workflow.py
/home/jusharri/code/label-driven-cherrypick-automation/rockrel/.venv/bin/python: No module named black
```

The missing Black executable is a local tool limitation, not a passing gate. No
network installation is permitted.

## Remediation red suite

Date: 2026-08-15

The complete remediation suite was written before product implementation
changes and run only against local files, temporary Git repositories, and fake
transports.

```text
$ .venv/bin/python -m pytest -q --tb=no scripts/tests
112 failed, 86 passed in 2.41s

$ python3 -m unittest build_tools.tests.cherry_pick_request_test  # TheRock
3 tests run; 2 intended caller-contract failures

$ python3 .github/scripts/tests/cherry_pick_request_test.py       # rocm-systems
4 tests run; 2 intended caller-contract failures

$ python3 .github/scripts/tests/cherry_pick_request_test.py       # rocm-libraries
4 tests run; 3 intended caller/CI-contract failures
```

The failures map to the planned missing behavior:

- schema v3, canonical ref validation, source-branch sets, and mode semantics;
- explicit result/status contract and required identity;
- effective pull-request rules, typed branch/Jira evidence, dependencies;
- complete squash, merge-commit, and rebase-range proof;
- exact/partial containment and conflict classification;
- transport isolation, pagination, bounded retry, and response validation;
- writer capability, Git identity, partial-write and fresh-clone recovery;
- rich draft rendering and idempotent existing-draft behavior;
- centralized label discovery and thin source callers;
- impossible local-review workflow write gates, least privilege, and Python
  setup;
- SPDX/format/coverage infrastructure.

There were no collection, syntax, fixture-setup, or network failures. Existing
unaffected behavior remained green in 86 tests. The caller tests failed on the
intended duplicated discovery logic and stale CI naming.

## First green implementation

Product implementation began only after the red test commit. The first green
controller commit produced:

```text
$ .venv/bin/python -m pytest -q scripts/tests
198 passed
```

Relevant local commits:

- `cbcd6ae` — complete remediation test contract (red);
- `a295011` — central implementation (first green);
- TheRock `cf103047`, rocm-systems `c914f5639b`, and rocm-libraries
  `f874736809` — thin-caller contracts (red).

Two test-fixture corrections were made before the green assertion: an unlabeled
event was exercised in `shadow` because `validate` is manual-only, and the Git
identity assertion checked the committer because cherry-pick intentionally
preserves the source author. These were contract clarifications, not relaxed
product expectations.

## Second red/green slice

Review of the first implementation found five additional fail-closed cases.
They were committed as tests before the corresponding fix:

```text
$ pytest <five selected tests>  # at 7d31b78
5 failed in 0.54s
```

The intended failures proved that the old implementation:

- did not retry a rate-limit `403`;
- treated a patch-equivalent subset of a rebase range as clean planning;
- treated a closed-unmerged PR as an active draft;
- allowed an existing PR to hide a mismatched deterministic branch tree; and
- propagated a PR-lookup API error instead of returning blocked evidence.

Commit `077aba1` implemented those five cases. Pinned Black 25.11.0 then found
and mechanically formatted branch-modified Python in `c53e703`; tests remained
green:

```text
$ .venv/bin/python -m pytest -q scripts/tests
203 passed in 2.25s
```

## Source caller green results

The canonical template was rendered against the full local controller SHA
`c53e703568fe41129abf7139f018ac920bca9c59`. Repository-local results:

```text
TheRock:        3 tests, OK
rocm-systems:   4 tests, OK
rocm-libraries: 4 tests, OK
```

The final local caller commits at evidence time are TheRock `8113258f`,
rocm-systems `ea11e53142`, and rocm-libraries `f3ccfdba38`.

## Final local gates

Passed locally with no network access:

- 203 rockrel pytest tests and 11 source-caller unittest tests;
- Black 25.11.0 on every branch-modified Python file;
- actionlint 1.7.10 on every changed workflow/template;
- mdformat 0.7.21 on the local project Markdown set;
- JSON parsing for train configuration, App manifest, and fixtures;
- SPDX/header and automation-module/test pairing assertions in pytest;
- `git diff --check` in all four repositories.

Not run and not claimed as passing:

```text
$ .venv/bin/python -m coverage --version
No module named coverage
```

Neither `pytest-cov` nor `coverage.py` exists in the local virtual environment,
system Python, pre-commit environments, or package cache. The no-network rule
forbids installing it. `.github/workflows/unit_tests.yml` enforces both line and
branch coverage at 90% for a future separately approved CI run; until then the
coverage success criterion remains explicitly unverified.
