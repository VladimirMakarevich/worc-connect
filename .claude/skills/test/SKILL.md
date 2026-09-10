---
name: test
description: Run and diagnose the worc-connect pytest suite during development — full, targeted, or last-failed — to keep the suite green and find bugs while implementing. Use while writing/changing code (the dev test loop). For the full pre-commit gate use /run-checks instead.
---

# test

Run the project's automated tests to keep a working state and surface bugs _while_ developing. This is the fast inner loop; `/run-checks` is the full quality gate you run before a commit, a PR, or a phase transition. The source of truth for _what_ to test is [.agents/rules/testing.md](../../../.agents/rules/testing.md).

Argument (optional): a path, a `-k` expression, or a flag — e.g. `tests/core`, `-k gate`, `--lf`. With no argument, run the whole suite.

## Steps

1. Ensure the package is installed (`pip install -e ".[dev]"`); install if imports fail.
2. Run the relevant tests with `python -m pytest`:
   - **everything:** `python -m pytest -q`
   - **a slice:** `python -m pytest tests/core -q`
   - **by name:** `python -m pytest -k "gate or sanitizer" -q`
   - **only what failed last:** `python -m pytest --lf -q`
   - **stop at first failure** while bisecting: `python -m pytest -x -q`
   - **fast inner loop** (no fake-executable integration tests): `python -m pytest -m "not slow" -q`
3. On a failure: show the failing test as `file:line` with a one-line cause; fix the **root cause** (in the code, or in the test if the test was wrong), then re-run just that slice, and finally the whole suite. Never weaken an assertion or `xfail`/`skip` a test just to go green.
4. When you add or change behavior, add/adjust a test at the right level (below) in the same change.
5. End with a short verdict: passed/failed counts. Only say the suite is green when `pytest` is green.

## Test levels (from testing.md — match the code under test)

- **Unit** — pure logic, no external processes: configuration rejections, the gate, the title sanitizer, id/branch allocation, body truncation, front-matter emission, state transitions, the status-label parser, the platform seams. Most tests live here.
- **Integration** — the fake `gh` and fake `worc` executables via the `/fake-cli` skill, never the real binaries: a full tick, the crash between write and promote, "already in pending", each tracker infrastructure error, dry run, the comment kinds, close on merge, the rebuild from labels, the owner's PR edits, a gate reject, the untrusted-text sentinel.
- **End-to-end** — one real run recorded in the README, not automated.

## Rules

- Deterministic and isolated: **no network and no real `gh` / `worc`** in unit/integration; mock/inject time and external processes.
- A behavior change without a test is incomplete (testing.md).
- The suite must pass on Windows and POSIX: generate the matching fake launcher, compare paths with `Path.as_posix()`, exercise both platform branches through the injected seam.
- A green `pytest` is mandatory before committing and before moving between implementation phases.
