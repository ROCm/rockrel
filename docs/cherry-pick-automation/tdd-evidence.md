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

Pending. Record the exact command, failing test names, and intended missing
behavior after all remediation tests have been written and before product code
changes.

## Green implementation

Pending.

## Final local gates

Pending.
