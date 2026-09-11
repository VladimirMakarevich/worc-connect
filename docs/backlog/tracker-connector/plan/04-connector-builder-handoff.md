# Phase 04 — Task builder and handoff

- **Status:** ☑ code complete on `feat/connector-v1` — the acceptance line below that needs a real repository stays open until an operator has run it
- **Depends on:** 03
- **Delivers:** FR-C3, FR-C4, FR-C5, FR-C6, FR-C7 — a gated item becomes a worc task file in `tasks/preparing/`, promoted with `worc promote`, exactly once, and the file passes worc's gate unchanged.

## Goal

Cross the boundary into worc through its public ingress and nothing else. Moves AC-3, AC-4, AC-5, AC-6, AC-7, AC-E2, AC-E8, AC-N2, AC-N4, AC-N7.

## Steps

1. `core/builder.py` — id allocation (`<id_prefix>-<number>[.<seq>]`, worc grammar), branch allocation (`<branch_prefix>/<task-id>-<slug>` ≤ 50 chars, valid ref, ≠ base branch), title sanitizer (D8), body assembly (provenance line + verbatim body), truncation to worc's three limits with a marker and the URL, front matter emitted only for configured keys, `commit_type_by_label`.
2. `core/handoff.py` — take `tasks_dir` and the three `validation.max_*` limits from the connector's own configuration, defaulted to worc's defaults (`tasks`, 262 144 / 5 000 / 8 192). They are **not** read from worc's `config.yaml`: that file lives in `.worc/`, and both "out of worc, exactly one read" and "never `.worc/`" are absolute invariants. Ensure `tasks/preparing/` exists; atomic write (temp + `os.replace`, UTF-8, `newline=""`); run `worc promote <id>` as argv (`shutil.which("worc")`). `cmd_promote` prints each outcome to **stdout** as `promote: …` and exits 1 on any error — treat exit 1 with a stdout line containing `already in pending` as success (the file is worc's), any other non-zero exit as "keep `staged`, retry".
3. `core/state.py` — phases `gated → staged → queued`; re-trigger allocates `seq + 1`.
4. `core/reconcile.py` (first half) — on tick, a `staged` row re-runs promote; a file missing from both folders is resolved through `worc list --format json --all` (row present → queued; absent → failed — this is also what a gate reject looks like from outside `.worc/`; the `rejected` section of the same listing names the reason once phase 06 adopts FR-W3).
5. Fake `worc` executable for tests: `promote` moves the file (refusing an existing target), `list --format json` returns a fixture.
6. Add worc as a **test** dependency so AC-4 can run the generated file through the real `task.validation_gate`.

## Files touched

- connector: `src/worc_connect/core/{builder,handoff,reconcile,state}.py`, `tests/` (fake `worc`, builder and handoff suites).

## Invariants in play

- The only path written in the clone is `tasks/preparing/<id>.md`; never `tasks/pending/`, never `.worc/`, never `git`.
- Item text reaches only the task file body; ids and URLs are the only item-derived values in logs.
- The builder's key set is a strict subset of worc's `ALLOWED_TASK_KEYS` and never emits `nodes`, `subtasks`, `decomposition`, `trust_level`, `prompt_audit`.
- Cross-platform file writing (`newline=""`, atomic replace).

## Tests

- Sanitizer: each rejected token, the `Issue #n` fallback, whitespace collapse, length cap.
- Id/branch: grammar, 50-char cap, base-branch clash, sequence suffix.
- Truncation: each of the three worc limits.
- Handoff: crash between write and promote (AC-3), "already in pending" (AC-E2), missing `preparing/` (AC-E8), argv recording (AC-7), sentinel string never leaves the task file (AC-N2).
- Real-gate test: the generated file passes worc's Phase A (AC-4).

## Docs to sync in this phase

- Connector configuration reference (`task.*` keys, what is emitted when).

## Acceptance for this phase

- [ ] A labelled issue on a throwaway repository becomes a task in `tasks/pending/` that `worc watch` claims and runs to a PR, with no connector write-back yet.
- [ ] AC-3 … AC-7, AC-E2, AC-E8, AC-N2, AC-N4, AC-N7 pass on both CI families — green locally on macOS; the matrix runs when the branch is pushed.

## What the phase actually delivers

- A gated item is handed over in the tick that admits it: the task file is composed, written atomically into `tasks/preparing/<id>.md`, and promoted. The only worc invocation is `promote <id>`; a recording `git` on `PATH` proves none was ever launched, and the only file left under the clone is the task worc now holds.
- `promote` reporting "already in pending" is a success, which is also what a crash between the write and the promote looks like on the next tick. Any other failure leaves the row `staged` and the next tick re-runs promote, which never overwrites a queued task.
- A staged file that is gone from both lifecycle folders is resolved through `worc list --format json --all`: a row there means worc has it, no row means it is gone — which is what worc's gate rejecting the task looks like from outside `.worc/` — and the row becomes `failed`. The comment that makes that visible on the item is phase 05.
- A re-trigger is armed by an observable event (the trigger label taken off, and from phase 05 the item closed by the connector) and then allocates the next sequence suffix, so `gh-142` is followed by `gh-142.2` and never reused. The state store carries one new column for that and its schema version is bumped; a database written by the previous version is discarded, which costs one tick.
- The generated file is judged by worc's **real** validation gate in the suite: worc is a test-only dependency, installed from its repository because it is not on any package index, and the test skips where it is absent.
- Two modules are not in the design's layout sketch: `core/naming.py` and `core/sanitize.py`, split out of the builder so the two rules that have to be provable on their own — the names worc's gate accepts, and the text its injection scan accepts — are not read through the assembly code; and `core/worc_cli.py`, which owns launching worc so that both of the connector's worc surfaces are visible in one file.
