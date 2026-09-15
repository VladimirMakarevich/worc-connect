# Phase 03 — The research child and the non-blocking loop

- **Status:** ☐
- **Depends on:** 02
- **Delivers:** FR-R4, FR-R7, FR-R8, NFR-R1, NFR-R2 — the detached `research run` child, the run directory as the state, adoption after a restart, `status`, `research cancel`, the opt-in timeout and the concurrency cap. One agent, no fallback yet.

## Goal

Run an agent beside the connector without anything waiting on it, and survive a restart, a crash and a recycled PID. This is the phase that makes the feature non-blocking; the report is produced but not yet dispatched.

## Steps

1. `research/process.py` — the platform seam: detached spawn (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows), liveness by PID **and** process start time (a recycled PID is not the child) — **three** branches, not two: `/proc/<pid>/stat` on Linux, `ps` or `sysctl` on macOS, `GetProcessTimes` through `ctypes` on Windows, since NFR-R5 rules out `psutil` — and terminate-then-kill of a process group. No signal is used for **control flow**; ending a process is the platform's own kill, which on POSIX is a signal, and the module says so rather than pretending otherwise.
2. `research/child.py` — the `worc-connect research run <task_id>` entry point: take the lock, fetch, add the worktree, copy in the prompt file the parent rendered ([R-21](../questions.md#open) — the child is given a `task_id` and no item text, so it cannot render one itself without reading the tracker again), launch the agent with the worktree as its working directory, stream stdout/stderr to `agent.log`, arm a timer only when `timeout_seconds > 0`, remove the worktree, write `outcome.json` atomically, exit. **The environment the agent inherits is [R-22](../questions.md#open)** — an allow-list, a deny-list, or the connector's own unchanged; the shell that starts `watch` holds the operator's tokens, so this is decided before the first agent is launched, not after.
3. Enough of `research/prompt.py` to give the agent a file at all: this phase needs the rendering, phase 04 settles what goes in it. The parent writes it into the run directory at `start()`; the child copies it per attempt.
4. `research/provider.py` — the `local` provider: `start()` creates the run directory, takes `research.lock` (exclusive create), writes `run.json` and spawns the child detached; `poll()` returns the parsed `outcome.json`, or `None` while the child is alive, or a synthetic `failed` when the child is gone without one; `cancel()` ends the child's group and lets it write `cancelled`, and falls back to the recorded `agent_pid` when the child is already gone.
5. `core/loop.py` — the `researching` branch: start children for `gated` rows while under `max_concurrent`, poll the rest, and **return** — the tick never waits. A tick that starts none because the cap is full logs one line naming how many are waiting. Dispatch is a no-op placeholder this phase logs; phase 04 fills it in. **Which rows the tick reaches was answered by R-18** — it walks the tracker's listing and nothing else today, so "poll the rest" gets its set from the second pass over the store's live rows that phase 04 builds; until then this phase polls only what the listing returned.
6. **The loop's budget.** `core/loop.py` is at 453 lines of 500 before this branch is written. Take the seam here — the research branch as its own module the loop calls — rather than at the commit where `python tools/size_gate.py` fires; the same goes for `cli.py` (346) and the three commands below.
7. `core/state.py` — `research_state`, `research_started_at`, and the new phase in the table; the row stays a cache rebuilt from files and labels.
8. `cli.py` — `status` lists running researches (item, task id, agent, elapsed, child PID and agent PID); `research cancel` by `task_id`, by `--item`, or `--all` (D15), including the orphaned-agent case and the nothing-running case; startup adoption of live children and the crash path for the rest.

## Files touched

`src/worc_connect/research/{process,child,provider}.py`, `src/worc_connect/core/{loop,state}.py`, `src/worc_connect/cli.py`, `tests/`.

## Invariants in play

- The tick returns while research runs; worc is handed nothing until a report exists.
- No environment is forwarded to the child beyond the connector's own.
- Files are the state, so a restart neither loses nor duplicates a research.

## Tests

- AC-R2, AC-R6, AC-R7, AC-R8, AC-R15, and the part of AC-R14 this phase can carry — the child and the agent end, the worktree is removed and `outcome.json` says `cancelled`. The clause "and `on_failure` decides" belongs to phase 04, which is where `on_failure` exists; do not assert it here. Fake agent launchers only (sleeping, exiting non-zero, writing a report); every platform branch injected; the process-tree tests marked `slow`, with the marker registered in `[tool.pytest.ini_options]`.

## Docs to sync in this phase

- `README.md`: `status` output, `research cancel`, and what `timeout_seconds: 0` means in practice.

## Acceptance for this phase

- [ ] A tick with a sleeping agent returns immediately and `tasks/preparing/` stays empty.
- [ ] Killing and restarting the connector adopts the running research rather than restarting it.
- [ ] With no timeout configured, no timer is armed; with one configured, the attempt is killed and recorded.
- [ ] All gates green, both CI families.
