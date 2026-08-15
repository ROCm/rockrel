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
- literal local-review workflow write gates, least privilege, and Python setup;
- SPDX/format/coverage infrastructure.

There were no collection, syntax, fixture-setup, or network failures. Existing
unaffected behavior remained green in 86 tests. The caller tests failed on the
intended duplicated discovery logic and stale CI naming.

## Green implementation

Pending.

## Final local gates

Pending.
