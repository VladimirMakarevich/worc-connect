# Phase 01 — Provider seam, configuration, preconditions

- **Status:** ☐
- **Depends on:** — (tracker-connector phases 03–07 are code complete as of `ca9db3b`, 2026-09-11; this phase touches the loop only at the seam)
- **Delivers:** FR-R1, FR-R2, FR-R12, NFR-R4, NFR-R5 — `research.mode`, the `ResearchProvider` protocol in `core/`, the `research` configuration block with fail-closed validation, `worc-connect doctor`, the new import contract, and the invariant rewrite D1 calls for.

## Goal

Make the second provider **possible** and the invariant change **explicit**, before a single subprocess is launched. After this phase the connector knows what `research.mode: local` means, refuses to start when it cannot honour it, and says in its own rules why an agent runtime now exists.

## Steps

1. **The invariant, rewritten (D1).** [AGENTS.md](../../../../AGENTS.md) and [.agents/rules/architecture.md](../../../../.agents/rules/architecture.md) — "The model boundary": replace "No agent runtime" with the narrow form (research only, behind `research.mode: local`, in a disposable worktree of the connector's own clone, never authoring a task). [.agents/rules/security.md](../../../../.agents/rules/security.md) gains the posture of [D12](../design.md#d12--the-blast-radius-is-the-worktree-and-that-is-the-whole-of-it): the gate is this feature's security perimeter. In [tracker-connector/out-of-scope.md](../../tracker-connector/out-of-scope.md), mark "A second agent runtime" as superseded by this record, and do the same for NFR-6 and R-5 there. Nothing is left saying two different things — and "nothing" is a longer list than the three files above: `README.md`'s own invariant bullet, `.agents/rules/architecture.md`'s "What must not be done" line **and** its State section (the state count, and "one worc task (two with triage on)"), `docs/configuration.md`'s line saying this build refuses `mode: local` by name, `core/items.py`'s docstring, and in the tracker-connector folder `design.md`'s D1, `problem.md`, NFR-6, AC-N6 and R-5. `grep -rn "agent runtime\|never launches\|model SDK"` is the check.
2. `core/research.py` — `ResearchProvider` protocol (`start` / `poll` / `cancel`), `ResearchOutcome` and `ResearchState` (`report` / `failed` / `cancelled`), and the `researching` phase constant. Pure shapes; no imports outside `core`.
3. `config.py` — the vocabulary already exists: `ResearchMode` carries `OFF` / `WORC` / `LOCAL`, and `LOCAL` is deliberately absent from `IMPLEMENTED_RESEARCH_MODES` so the loader refuses it by name. This phase adds it to that list and widens the block: `_research`'s `section.reject_unknown("mode", "flow")` gains the local keys — `agents`, `base_ref`, `timeout_seconds` (0 or absent = none), `max_concurrent`, `on_failure`, `retain`, `retain_max_runs`, `keep_worktree`. Fail closed: `research.mode: local` with no `research` block, an unknown agent name, `max_concurrent < 1`, a negative timeout, or an `on_failure` outside `proceed` / `fail` is a refusal that names the key.
4. `cli.py` — the composition root branches on `research.mode` and injects the local provider where the mode asks for one. **The worc path is not touched (R-19):** it keeps the shape phase 07 shipped, `core/reconcile.py` is not refactored, and the protocol describes the local producer alone — the symmetry the operator asked for is one configuration key, not one interface. A `local` provider stub that reports "not built yet" is acceptable inside phases 01 and 02 and must not survive phase 03.
5. `cli.py doctor` — resolves `git` and every configured agent launcher with `shutil.which`, checks the home's writability and the worktree root, prints one line per check and exits non-zero on the first failure. `watch` runs the same checks when `research.mode: local`, before reading a single item.
6. `watch --once --dry-run` names the path it would take per item (`would research`, `would queue`), so the switch is observable before anything runs.
7. `.importlinter` — a contract forbidding `worc_connect.core` from importing `worc_connect.research`, and one keeping `worc_connect.research` off `cli` and `config`. Because of the second one, the settings the runtime reads are a shape in `core/research.py` built by the composition root, not `config.ResearchConfig` itself.
8. `core/items.py` — the eighth `ItemState` and the correction of the docstring that counts them wrong today ("These six (eight with triage on)"), so the adapter's label map, `ensure_labels` and the rules all agree on one number.
9. **The CLI's own budget.** `cli.py` is at 346 lines of 500 and this phase adds `doctor`; phase 03 adds `research run`, `research cancel` and the `status` lines. Decide the seam here — a `cli/` package, or a `doctor` module the CLI calls — rather than at the commit where the gate fires. The budget is a ratchet and may not be raised.
10. **[R-23](../questions.md#open)** — whether `gate.allow_all: true` stays legal with `research.mode: local`. If the answer is a refusal, it belongs in `_research` / `_gate` in `config.py` and lands with this phase; if it is a warning, it belongs in `doctor` and in `watch`'s startup check.

## Files touched

`AGENTS.md`, `.agents/rules/{architecture,security}.md`, `docs/backlog/tracker-connector/{out-of-scope,design,problem,requirements,acceptance-criteria,questions}.md`, `README.md`, `.importlinter`, `src/worc_connect/{cli,config,home}.py`, `src/worc_connect/core/{research,items,triage,loop}.py`, `docs/configuration.md`, `tests/`.

## Invariants in play

- The gate still runs first; research is a step **after** it, never before.
- The connector still writes into worc exactly one file and reads `worc list --format json`.
- Deterministic code still decides: the protocol returns a report, never a task.

## Tests

- AC-R1 (all three switch states) and AC-R12; every configuration refusal; `lint-imports` proving the new contracts; the dry run's output per mode.

## Docs to sync in this phase

- The rewritten invariant in the three rule files (phase 07 already narrowed `architecture.md` and `security.md` once — this phase rewrites what it left) and the superseded bullets in the tracker-connector record; [docs/configuration.md](../../../configuration.md) gains every `research.*` key; `README.md` gains the `research.mode: local` section with D12's warning in the operator's own terms.

## Acceptance for this phase

- [ ] Every open question this phase depends on is decided first: [R-23](../questions.md#open) (`allow_all`) and [R-17](../questions.md#open) (the id the configuration validates). R-19 is already answered — the protocol covers the local producer only.
- [ ] No document in the repository still claims the connector never launches an agent, by `grep` and not by memory.
- [ ] `research.mode: local` on a host with no agent CLI refuses to start, naming the check.
- [ ] `research.mode: off` behaves exactly as before, byte for byte.
- [ ] All gates green, both CI families.
