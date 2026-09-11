# Definition of Done — Local research runtime

The finish line for [plan/README.md](plan/README.md). Everything in [acceptance-criteria.md](acceptance-criteria.md) passes, and:

## Code

- [ ] `research.mode` selects the provider; `research.mode: off` (still the default) leaves every line of this runtime unreachable.
- [ ] `worc_connect/research/` is a leaf package: `lint-imports` proves `core` does not import it, and every module is inside the size budget with no new per-file ignore.
- [ ] `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, `pytest`, plus `interrogate src`, `vulture`, `deptry src` — green on Windows and POSIX.
- [ ] `deptry` confirms no new runtime dependency: the runtime is the standard library plus executables the operator already has.

## Tests

- [ ] Every `AC-R*` is covered, driven by fake launchers; no test runs a real agent CLI, real `gh` or real `worc`.
- [ ] The sentinel test of AC-R4 is extended from the existing one, so the untrusted-text boundary is asserted for the research path too.
- [ ] Both platform branches of the process seam are exercised by injection.
- [ ] The suite still passes with `pytest -m "not slow"` in the fast loop, with the heavy process-tree tests marked `slow`.

## Docs, in the same change

- [ ] The invariant is rewritten, not quietly contradicted: [AGENTS.md](../../../AGENTS.md), [.agents/rules/architecture.md](../../../.agents/rules/architecture.md), [.agents/rules/security.md](../../../.agents/rules/security.md), and the superseded bullets in the [tracker-connector](../tracker-connector/out-of-scope.md) record all say the same thing (D1).
- [ ] [README.md](../../../README.md) documents `research.mode: local`: what it costs, what it does **not** protect against (D12), and that the gate is the perimeter for it.
- [ ] [docs/configuration.md](../../configuration.md) carries every `research.*` key, including the meaning of `timeout_seconds: 0` and of each `retain` value.
- [ ] FU-1 is gone from [follow-ups.md](../follow-ups.md), because phase 04 made the decision it was waiting for.
- [ ] `python tools/mdlint.py` is green.

## The real run

- [ ] One recorded end-to-end run on a throwaway repository with `research.mode: local`: a labelled issue → `worc:researching` → a report from the first agent → an implementation task → a pull request, with `worc watch` busy on an unrelated task throughout and **never** waiting on the research.
- [ ] One recorded run where the first agent is made to fail and the second produces the report.
- [ ] One recorded run of a `needs-info` verdict: no task queued, the question posted as a comment, the author's reply re-triggering with the next sequence.
