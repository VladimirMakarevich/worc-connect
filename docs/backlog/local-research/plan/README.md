# Implementation plan — Local research runtime

Builds [design.md](../design.md) toward [acceptance-criteria.md](../acceptance-criteria.md); the finish line is [definition-of-done.md](../definition-of-done.md).

## Strategy

**Nothing starts until [R-17, R-21, R-22, R-23](../questions.md#open) are decided.** Of the seven questions the review of 2026-09-12 opened, three are answered — only the local producer goes behind the protocol (R-19), a finished research is picked up by a second pass over the store's live rows (R-18), and the clone and its worktrees live in a `research.workspace` outside worc's clone while the run directories stay in the connector's home (R-20). Each of the four still open fixes a shape that more than one phase builds on: the id a research is keyed by (01, 03, 04), who renders the prompt (03, 04), what environment the agent inherits (03), and whether `gate.allow_all` stays legal with `mode: local` (01). Deciding them mid-implementation means redoing a phase that already passed its gates.

One chain, five phases, all in this repository. The order follows the risk: the **seam and the preconditions** first (01), because they decide whether the rest can be reached at all and because they carry the invariant rewrite; then the **worktree** (02) and the **child process** (03), which are the two mechanical pieces and the two that behave differently on Windows; then the **dispatch** (04), which is where this record meets [tracker-connector phase 07](../../tracker-connector/plan/07-connector-triage.md) and the feature becomes usable end to end; and the **agent chain** (05) last, because a fallback is only worth building once a single agent is known to work.

The critical path is 01 → 02 → 03 → 04. Phase 04 is the only phase that depends on anything outside this record: phase 07 must have landed the report contract, the reader and the report → task builder, since this record reuses all three unchanged.

The biggest risk is not the subprocess work, it is **scope creep back into worc's territory**: a research child that starts committing, pushing, or writing into the clone worc owns. Every phase carries that boundary explicitly, and phase 02 makes the clone unable to push at all.

## Phases

| # | Phase | Delivers | Depends on | Status |
| --- | --- | --- | --- | --- |
| 01 | [Provider seam, configuration, preconditions](01-seam-and-preconditions.md) | FR-R1, FR-R2, FR-R12, NFR-R4, NFR-R5: `research.mode`, the `ResearchProvider` protocol, the `research` config block, `doctor`, the import contract, and D1's invariant rewrite | — | ☐ |
| 02 | [The research clone and its worktrees](02-worktree-lifecycle.md) | FR-R3, FR-R13: the connector's own bare clone with its push URL disabled, fetch, detached worktree per research, removal, prune and orphan sweep | 01 | ☐ |
| 03 | [The research child and the non-blocking loop](03-research-child.md) | FR-R4, FR-R7, FR-R8, NFR-R1, NFR-R2: the detached `research run` child, the run directory, adoption after restart, `status`, `research cancel`, the opt-in timeout, the concurrency cap — with one agent and no fallback | 02 | ☐ |
| 04 | [Dispatch: report to task](04-dispatch.md) | FR-R5, FR-R9, FR-R10, FR-R11: the prompt file, `worc:researching`, the shared report reader, the builder path, the four verdicts, `on_failure` — the feature works end to end | 03, tracker-connector 07 | ☐ |
| 05 | [The agent chain and fallback](05-agent-chain.md) | FR-R6: shipped `claude` and `codex` profiles, the ordered chain, fallback on failure only, a fresh worktree per attempt, provenance and the attempt record | 04 | ☐ |

## Cross-cutting

- **Branching** — one branch per phase off `main`, named `feat/research-<phase>`; never commit to `main` directly. See [.agents/rules/git-workflow.md](../../../../.agents/rules/git-workflow.md).
- **Commits** — atomic, imperative subject, scoped staging only (never `git add .`), and **no agent-attribution trailer or footer**.
- **Gates per phase** — `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, `pytest`, plus `python tools/mdlint.py` when Markdown changed.
- **Tests** — every phase ships its own; fake launchers only, never a real agent CLI, `gh` or `worc`, and both platform branches of any `os.name` split exercised by injection.
- **Docs** — phase 01 carries the invariant rewrite and the operator-facing warning; every later phase keeps the README's configuration reference current in the same change.
- **Invariants** — no phase lets the research path write into worc's clone, run `git` there, read `.worc/`, put item text into an argv, or let model output reach the queue without the builder.

## Risks & mitigations

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| An unsandboxed agent runs arbitrary commands on untrusted issue text | certain, accepted (D12) | the gate is the perimeter and fails closed; the worktree is disposable; the clone cannot push; the README says so plainly for the operator |
| The research child outlives the connector, or is orphaned by a crash | medium | files are the state: `run.json` + PID + start time for adoption, `outcome.json` as the only done signal, an orphan sweep and `worktree prune` at startup |
| A research runs forever with no timeout configured | medium, by design (D8) | `status` shows elapsed time per research, `research cancel` ends one, `max_concurrent` bounds how many can be in flight |
| The research path drifts into worc's clone (a commit, a push, a `git -C` in the wrong tree) | medium | the connector's own clone is a separate directory with push disabled; AC-R3 asserts no `git` launch names worc's clone |
| The report contract drifts apart between the two providers | medium | one reader module, shared; AC-R9 asserts byte-identical dispatch from the same report for both providers |
| Windows process control (detach, liveness, terminate) behaves differently and is only found in production | medium | the platform seam is a single module, injected in tests, with both branches asserted; CI runs both families |
| `research.mode: local` silently degrades to "queue it raw" on a host with no agent installed | medium | FR-R12: `watch` refuses to start and `doctor` names the failing check; `on_failure` governs only per-item failures, never a missing install |
| **A finished research is never dispatched**, because the tick walks the tracker's listing and its item has fallen out of the watermark window | high, and the default configuration makes it the common case | **closed by R-18**: the tick gains a second pass over the store's live rows, resolving with `adapter.get_item` only what the listing missed — free on a quiet tick, at most `max_concurrent` calls otherwise. Phase 04 builds it before the dispatch logic |
| The `worc` producer does not fit `ResearchProvider` — `cancel` contradicts an invariant, `poll` cannot express the states it publishes | high | **closed by R-19**: the protocol describes the local producer only and the worc path is left exactly as phase 07 shipped it; phase 01 must not refactor `core/reconcile.py` |
| A worktree inside worc's clone shares its refs, objects and hooks, and hands the agent worc's mid-task `CLAUDE.md` through the ancestor walk | high, as the paths were first written | **closed by R-20**: `research.workspace` resolves outside worc's clone and phase 02 asserts it on absolute paths. The `git clean` half of this risk was withdrawn — worc never runs it |
| A module outgrows its 500-line budget and the ratchet may not be raised | high — `core/loop.py` is at 453, `core/reconcile.py` at 433, `cli.py` at 346 before a line is added | the split is part of the phase that adds the code (03 for the loop and the CLI, 04 for the dispatch), planned in the phase document rather than discovered at commit time |
| `build_from_report` takes a seventh argument for the producing agent and trips `max-args = 6` | certain, in phase 05 | a parameter object for the report-derived inputs; never a per-file ignore |
| Cloning a private origin needs the operator's own credential helper, and a missing one hangs a detached child on a password prompt | medium | `gh auth setup-git` documented as the prerequisite; terminal prompting disabled on every `git` the connector launches, so it fails visibly instead |
| Two hung agents hold both concurrency slots and every later item stops being researched, invisibly | medium, and it is exactly how "no timeout" fails | the tick logs one line naming how many items wait at the cap, and `status` shows it (FR-R8); `research cancel --all` is the recovery |
| The start-time check that defeats a recycled PID has no portable reading, and `psutil` is forbidden | medium | three branches in `research/process.py`, all injected in tests: `/proc` on Linux, `ps` / `sysctl` on macOS, `GetProcessTimes` on Windows |
| An unsandboxed agent is reachable by anyone who can open an issue, because `gate.allow_all: true` is a supported configuration | low likelihood, highest impact | [R-23](../questions.md#open): refuse the combination, warn loudly, or state the exposure — D12 names the gate as the perimeter and this is the key that removes it |
