# Definition of Done — Tracker connector

Two repositories are involved, so the baseline applies twice: this repository's gates for the worc-side phases, and the connector repository's own gates (to be set up in its first phase, mirroring these) for the rest.

## Project baseline (worc-side phases, always)

- [ ] `ruff check .` and `ruff format --check .` pass.
- [ ] `mypy src` passes.
- [ ] `lint-imports` passes (the architectural import boundaries hold).
- [ ] `pytest` passes, and new or changed behavior ships with tests (`.agents/rules/testing.md`); provider, router and pipeline coverage uses deterministic fake CLIs, never the real binaries.
- [ ] The CI-only gates hold: `interrogate src`, `vulture`, `deptry src`.
- [ ] `python tools/mdlint.py` is green, and Markdown edited on this branch is Prettier-formatted (one paragraph per line).
- [ ] Every document that lives on this branch is updated in the same change — `.agents/rules/`, `README.md`, `docs/backlog/`, and the shipped operator-facing copies under `src/wastech_orchestrator/packaged/` (the guide, the flows and role prompts, `config.example.yaml`).
- [ ] The doc impact on the derived pages that live only on the documentation branch is noted in the PR description as a breadcrumb.
- [ ] No secrets in logs, `state.db` or artifacts; only allowlisted env vars reach a process.
- [ ] The hard invariants are intact (core knows no CLI syntax; no delegated publication; fallback only for provider infrastructure classes; the security envelope is not weakened).
- [ ] Commits and the PR carry **no agent-attribution trailer or footer**.

## Connector baseline (connector phases)

- [x] The connector repository has the same shape of gates from its first commit: lint + format, type check, import-boundary contract (core never imports an adapter), tests on a Windows + Linux matrix, dependency hygiene.
- [x] Every test that touches `gh` or `worc` drives a fake executable; the real binaries are never launched in CI.
- [x] The connector's README carries the auto-merge caveat and the "issue text is untrusted" note in plain words.

## Task-specific

- [x] Every acceptance criterion in [acceptance-criteria.md](acceptance-criteria.md) passes — `AC-W*` here, `AC-C*` in the connector repository. _(the connector's `AC-C*` by its suite, against the fakes and worc's real gate; `AC-W*` by the worc-side phases merged into the orchestrator's `dev`)_
- [x] Everything in [out-of-scope.md](out-of-scope.md) stayed out: no code-host adapter in worc, no tracker knowledge in worc, no connector state in `.worc/` or `state.db`, no new worc config key.
- [x] Every blocking question in [questions.md](questions.md) is resolved (there are none at drafting time; the non-blocking ones have a recorded default).
- [x] One real end-to-end run is recorded: a labelled issue on a throwaway GitHub repository, `worc watch` and `worc-connect watch` side by side, from label to merged PR to closed issue, with `research.mode: off`. _(2026-11-12, on `VladimirMakarevich/wastech-mdlint` rather than a throwaway repository, worc `0.16.0a1`: issue #24 labelled `worc` → task `gh-24` staged and promoted, `worc:queued` and its comment → worc's `implementation` flow → pull request #34 → `worc:pr-open` and its comment → merged → `worc:done`.)_
- [x] `references:` is exercised by that run once phase 06 lands: the PR body ends with `Fixes #<n>` and GitHub closes the issue on merge with `close_on_merge: false`. _(the task file carried `references: ['Fixes #24']`, pull request #34 ended with `Fixes #24`, and the issue's closing event names pull request #34 as its closer.)_
- [ ] A triage run (`research.mode: worc`, phase 07) reads its report from `.worc-connect/triage/<task_id>/report.md` and nothing under `.worc/`; with the connector home deliberately un-ignored the triage task ends `manual_action_required` at publish and the item is labelled `worc:failed`. _(First half recorded 2026-11-13 on `VladimirMakarevich/wastech-mdlint`, worc `0.16.0a1`, the installed flow tuned to `reasoning: medium` with `review` on codex: issue #31 labelled `worc` → triage task `gh-31` (`issue_triage`, `priority: high`) → report at `.worc-connect/triage/gh-31/report.md` with verdict `actionable` and a failing test → implementation task `gh-31.2` built from it, carrying `Fixes #31` → pull request #35 → `worc:pr-open` → merged → issue closed by pull request #35 → `worc:done`. The un-ignored-home half has not been run, so the box stays open.)_
- [ ] A run on Windows completes the same cycle against the fake `gh` and fake `worc` (the CI matrix) — a real Windows run is recorded if a host is available.

## When it lands

- [ ] The connector repository (`VladimirMakarevich/worc-connect`, created 2026-09-11) carries the connector half of this spec (problem, requirements, design, plan phases 03–07) as its own backlog; this folder shrinks to the worc-side items or leaves `docs/backlog/` entirely once phases 01, 02 and 08 have merged, and its row is removed from the backlog index — the code, the tests and the history become its record.
- [ ] A "code host adapters" row is added to the backlog index when this folder leaves it, so the other axis is not lost.

## Sign-off

- [ ] The user reviewed the result against [happy-path.md](happy-path.md).
