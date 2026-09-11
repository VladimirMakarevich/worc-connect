# Phase 03 — The research child and the non-blocking loop

- **Status:** ☐
- **Depends on:** 02
- **Delivers:** FR-R4, FR-R7, FR-R8, NFR-R1, NFR-R2 — the detached `research run` child, the run directory as the state, adoption after a restart, `status`, `research cancel`, the opt-in timeout and the concurrency cap. One agent, no fallback yet.

## Goal

Run an agent beside the connector without anything waiting on it, and survive a restart, a crash and a recycled PID. This is the phase that makes the feature non-blocking; the report is produced but not yet dispatched.

## Steps

1. `research/process.py` — the platform seam: detached spawn (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows), liveness by PID **and** process start time (a recycled PID is not the child), terminate-then-kill of a process group. No signals used for control flow.
2. `research/child.py` — the `worc-connect research run <task_id>` entry point: take the lock, fetch, add the worktree, launch the agent with the worktree as its working directory and the connector's own environment (nothing forwarded, nothing added), stream stdout/stderr to `agent.log`, arm a timer only when `timeout_seconds > 0`, remove the worktree, write `outcome.json` atomically, exit.
3. `research/provider.py` — the `local` provider: `start()` creates the run directory, takes `research.lock` (exclusive create), writes `run.json` and spawns the child detached; `poll()` returns the parsed `outcome.json`, or `None` while the child is alive, or a synthetic `failed` when the child is gone without one; `cancel()` ends the child's group and lets it write `cancelled`, and falls back to the recorded `agent_pid` when the child is already gone.
4. `core/loop.py` — the `researching` branch: start children for `gated` rows while under `max_concurrent`, poll the rest, and **return** — the tick never waits. Dispatch is a no-op placeholder this phase logs; phase 04 fills it in.
5. `core/state.py` — `research_state`, `research_started_at`, and the new phase in the table; the row stays a cache rebuilt from files and labels.
6. `cli.py` — `status` lists running researches (item, task id, agent, elapsed, child PID and agent PID); `research cancel` by `task_id`, by `--item`, or `--all` (D15), including the orphaned-agent case and the nothing-running case; startup adoption of live children and the crash path for the rest.

## Files touched

`src/worc_connect/research/{process,child,provider}.py`, `src/worc_connect/core/{loop,state}.py`, `src/worc_connect/cli.py`, `tests/`.

## Invariants in play

- The tick returns while research runs; worc is handed nothing until a report exists.
- No environment is forwarded to the child beyond the connector's own.
- Files are the state, so a restart neither loses nor duplicates a research.

## Tests

- AC-R2, AC-R6, AC-R7, AC-R8, AC-R14, AC-R15, with a fake agent launcher that sleeps, exits non-zero, or writes a report; both platform branches injected; the process-tree tests marked `slow`.

## Docs to sync in this phase

- `README.md`: `status` output, `research cancel`, and what `timeout_seconds: 0` means in practice.

## Acceptance for this phase

- [ ] A tick with a sleeping agent returns immediately and `tasks/preparing/` stays empty.
- [ ] Killing and restarting the connector adopts the running research rather than restarting it.
- [ ] With no timeout configured, no timer is armed; with one configured, the attempt is killed and recorded.
- [ ] All gates green, both CI families.
