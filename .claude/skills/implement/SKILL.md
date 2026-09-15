---
name: implement
description: Implement a task in worc-connect end to end — a phase of a spec folder under docs/backlog/, or a change the user describes — with tests and docs in the same change and the full gate green. Use when the user says "implement X", "do phase NN", "реализуй".
---

# implement

Implement the task in this repository end to end: the code, the tests, the docs it makes stale, and a green gate.

Strictly follow the project rules — they are mandatory, not advisory:

- [architecture](../../../.agents/rules/architecture.md) — the invariants that must not be violated
- [coding-style](../../../.agents/rules/coding-style.md) — Python style, comments, cross-platform
- [security](../../../.agents/rules/security.md) — untrusted issue text, credentials, the worc envelope
- [git-workflow](../../../.agents/rules/git-workflow.md) — branches, commits, PRs
- [testing](../../../.agents/rules/testing.md) — what to test and how

## Before writing code

1. **Find the contract.** If the task is a phase of a spec folder, the phase document (`docs/backlog/<slug>/plan/NN-*.md`) is the contract: its steps, its files, its invariants, its acceptance. Read the folder's `design.md` and `acceptance-criteria.md` with it, and the whole [backlog index](../../../docs/backlog/README.md) if the slug is unclear.
2. **Read the code before changing it** — the modules the phase names, plus their tests. The code is the source of truth; a spec document that disagrees with it is a discrepancy to raise, not a licence.
3. **Branch off `main`** (`feat/…`, `fix/…`, `chore/…`, `docs/…`) — never work on `main` directly.

## While implementing

- Minimal, focused changes in the style of the surrounding code. No drive-by refactors.
- **Modules and functions stay small.** When `python tools/size_gate.py` or a ruff `PLR` ratchet fires, split along a real seam — never raise a number, never add a per-file ignore.
- The invariants win over convenience: the core imports no concrete adapter, the only write into worc is `tasks/preparing/<id>.md` + `worc promote`, no agent runtime, the gate fails closed, item text goes nowhere but the task body, `gh` and `worc` are argv resolved with `shutil.which`, and everything works on Windows and POSIX.
- Behavior change ⇒ tests in the same change; integration coverage drives the fake `gh` / fake `worc` ([/fake-cli](../fake-cli/SKILL.md)), never the real binaries. Run them with [/test](../test/SKILL.md) as you go.
- Docs change with the code, in the same commit: [README.md](../../../README.md), [docs/configuration.md](../../../docs/configuration.md), [.agents/rules/](../../../.agents/rules/architecture.md), `docs/backlog/`, and anything shipped under `src/worc_connect/packaged/`.

## Finish

- [/run-checks](../run-checks/SKILL.md) green — every check, not the convenient ones. Never declare green on a red gate.
- Tick the phase off in its plan document and the folder `README.md` when the work lands.
- Commit only when the user asks, with an imperative subject, scoped staging (never `git add .`), and **no agent-attribution trailer or footer**.
