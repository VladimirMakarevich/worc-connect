# Definition of Done — Local research runtime

The finish line for [plan/README.md](plan/README.md). Everything in [acceptance-criteria.md](acceptance-criteria.md) passes, and:

## Before phase 01 starts

- [ ] Every open question is decided and written into the record: [R-17, R-21, R-22, R-23](questions.md#open) — the id a research is keyed by, who renders the prompt, what environment the agent gets, and whether `gate.allow_all` stays legal with `mode: local`. (R-18, R-19 and R-20 were answered on 2026-09-12.) Each one changes code in more than one phase, which is why none of them is a decision to take mid-implementation.

## Code

- [ ] `research.mode` selects the provider; `research.mode: off` (still the default) leaves every line of this runtime unreachable.
- [ ] `worc_connect/research/` is a leaf package: `lint-imports` proves `core` does not import it, and every module is inside the size budget with no new per-file ignore.
- [ ] No budget was raised to fit this work: `core/loop.py` (453/500 before it starts), `core/reconcile.py` (433/500) and `cli.py` (346/500) are split along a real seam where they need it, and `build_from_report` — already at the six-argument ceiling `max-args` sets — takes a parameter object rather than a seventh argument for the producing agent.
- [ ] `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, `pytest`, plus `interrogate src`, `vulture`, `deptry src` — green on Windows and POSIX.
- [ ] `deptry` confirms no new runtime dependency: the runtime is the standard library plus executables the operator already has.

## Tests

- [ ] Every `AC-R*` is covered, driven by fake launchers; no test runs a real agent CLI, real `gh` or real `worc`.
- [ ] The sentinel test of AC-R4 is extended from the existing one, so the untrusted-text boundary is asserted for the research path too.
- [ ] Both platform branches of the process seam are exercised by injection.
- [ ] The suite still passes with `pytest -m "not slow"` in the fast loop, with the heavy process-tree tests marked `slow` — and the marker is registered in `[tool.pytest.ini_options]`, so the fast loop is a selection rather than a warning.

## Docs, in the same change

- [ ] The invariant is rewritten, not quietly contradicted, in **every** place that states it — `grep -rn "agent runtime\|never launches\|model SDK"` comes back with nothing that still denies what the connector now does: [AGENTS.md](../../../AGENTS.md), [.agents/rules/architecture.md](../../../.agents/rules/architecture.md) (both "The model boundary" and "What must not be done", plus the State section's state count), [.agents/rules/security.md](../../../.agents/rules/security.md), [README.md](../../../README.md), [docs/configuration.md](../../configuration.md), `core/items.py`'s docstring, and in the [tracker-connector](../tracker-connector/out-of-scope.md) record its out-of-scope bullet, `design.md`'s D1, `problem.md`, NFR-6, AC-N6 and R-5.
- [ ] [README.md](../../../README.md) documents `research.mode: local`: what it costs, what it does **not** protect against (D12), and that the gate is the perimeter for it.
- [ ] [docs/configuration.md](../../configuration.md) carries every `research.*` key, including the meaning of `timeout_seconds: 0` and of each `retain` value.
- [ ] FU-1 is gone from [follow-ups.md](../follow-ups.md), because phase 04 made the decision it was waiting for.
- [ ] `python tools/mdlint.py` is green.

## The real run

- [ ] One recorded end-to-end run on a throwaway repository with `research.mode: local`: a labelled issue → `worc:researching` → a report from the first agent → an implementation task → a pull request, with `worc watch` busy on an unrelated task throughout and **never** waiting on the research.
- [ ] One recorded run where the first agent is made to fail and the second produces the report.
- [ ] One recorded run of a `needs-info` verdict: no task queued, the question posted as a comment, the author's reply re-triggering with the next sequence.
