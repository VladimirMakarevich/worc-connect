# Acceptance criteria — Local research runtime

Testable form of [requirements.md](requirements.md). Integration criteria are driven by **fake executables** — a fake `git`ish repository built in a temp directory, a fake agent launcher recording its argv, the existing fake `gh` and fake `worc` — never a real agent CLI and never the network. Criteria are numbered `AC-R*`, independently of the [tracker-connector](../tracker-connector/acceptance-criteria.md) set.

### AC-R1 — the switch (FR-R1)

- **Given** `research.mode: off` → **When** a gated item is processed → **Then** no research directory, no worktree, no child process and no `worc:researching` label exist; the behaviour is byte-identical to today's.
- **Given** `research.mode: worc` → **Then** the phase 07 path runs and nothing from this record is reachable.
- **Given** `research.mode: local` → **Then** the research path runs and no triage task is written into `tasks/preparing/`.
- **Given** `mode: off` with a full `agents` / `flow` configuration still present → **Then** it is ignored entirely: no provider is constructed, no precondition check runs, nothing is refused.
- **Given** `mode: worc` with `agents` present, or `mode: local` with `flow` present → **Then** the foreign key is ignored, and a key the mode requires and lacks (`local` without `agents`, `worc` without `flow`) is a refusal naming that key.
- **Given** a configuration switched from one mode to another between two runs → **Then** the second run uses the new mode with no other edit and no state left by the first mode is read as if it belonged to the second.

### AC-R2 — nothing blocks (FR-R4)

- **Given** a fake agent that sleeps → **When** a tick processes the gated item → **Then** the tick returns while the child is still alive, the item carries `worc:researching`, and `tasks/preparing/` is empty; a second tick also returns without waiting.
- **Then** no `worc` process is launched for that item until the outcome exists.

### AC-R3 — the worktree lifecycle (FR-R3)

- **Given** a research → **Then** the worktree is created under `.worc-connect/worktrees/<task_id>`, detached at `base_ref`, and removed when the attempt ends; `keep_worktree: true` leaves it.
- **Given** no `base_ref` configured and an origin whose default branch is `develop` → **Then** the worktree is based on `develop` through `origin/HEAD`, with no `gh` call recorded; a configured `base_ref` wins over it.
- **Then** no `git` process runs with worc's clone as its working directory or `-C` argument, in any recorded launch.
- **Given** a leftover worktree from a killed run → **When** the connector starts → **Then** it is pruned and its directory removed.

### AC-R4 — untrusted text never becomes an argument (FR-R5)

- **Given** an item whose body contains a sentinel string, backticks, `$(…)`, a newline and non-ASCII → **Then** the sentinel appears in `RESEARCH_TASK.md` and in the generated task file, and in **no** recorded argv, environment, log line, label or comment body.

### AC-R5 — the agent chain (FR-R6)

- **Given** `agents: [claude, codex]` and a first launcher that exits non-zero → **Then** the second runs on a **fresh** worktree, `outcome.json` records both attempts, and the built task's provenance names the second agent.
- **Given** a first agent that produces a report with verdict `needs-info` → **Then** the chain stops, the second agent is never launched, and the `needs-info` write-back runs.
- **Given** a first launcher that does not resolve at all → **Then** the attempt is recorded as failed without a process being started, and the chain continues.
- **Given** every agent fails → **Then** `outcome.json.state` is `failed` and `on_failure` decides.

### AC-R5a — the shipped profiles (FR-R6, D6a)

- **Then** each shipped profile is a constant: the argv the connector launches for `claude` / `codex` is asserted exactly, non-interactive flag included, and no `--help` is ever executed to build it.
- **Then** `doctor` prints, per configured agent, the resolved launcher path, its version and the full argv; the table form in the configuration replaces the profile entirely.

### AC-R6 — timeouts are opt-in (FR-R7)

- **Given** `timeout_seconds: 0` (and, separately, the key absent) and an agent that outlives any test-scale timer → **Then** no timer is armed and the child does not terminate it.
- **Given** `timeout_seconds: 2` and an agent that sleeps → **Then** the attempt is terminated, recorded as failed with reason `timeout`, and the chain continues; the worktree is still removed.

### AC-R7 — the concurrency cap and one-per-item (FR-R8)

- **Given** `max_concurrent: 2` and three gated items → **Then** two children exist, the third item stays `gated` and starts on a later tick.
- **Given** a research already running for a `task_id` → **When** a tick runs again → **Then** no second child is started (the lock file holds) and nothing is duplicated.

### AC-R8 — restart safety (FR-R4, NFR-R2)

- **Given** a running research → **When** the connector process is killed and restarted → **Then** the child is adopted from `run.json`, the tick does not restart it, and its outcome is dispatched normally.
- **Given** a `run.json` whose PID belongs to an unrelated live process (recycled PID) → **Then** the start-time check treats the research as crashed rather than adopting it.
- **Given** a `run.json` whose process is gone and no outcome → **Then** the research is recorded as `failed` and `on_failure` decides.

### AC-R9 — the report is dispatched exactly as phase 07 dispatches it (FR-R10)

- **Given** a report per verdict (`actionable`, `needs-info`, `duplicate`, `declined`) → **Then** the resulting task file, labels and comments are identical to the ones the `worc` provider produces from the same report bytes, except for the provenance line naming the local agent.
- **Given** a `report.md` with no parsable verdict block → **Then** the attempt is a failure (AC-R5), not an `actionable` default.

### AC-R9a — what of the report is published (FR-R10a)

- **Given** a report whose `reason` contains `@someone`, `#1`, a long paragraph and a second section → **Then** the comment carries that one paragraph as a blockquote, capped, with the mention and the issue reference defused, and carries nothing from any other section of the report.
- **Then** the same is true for all four verdicts, and `report.md` itself is never uploaded, attached or pasted.

### AC-R9b — the item moves mid-research (FR-R10b)

- **Given** a running research → **When** the item's body is edited → **Then** the child is neither cancelled nor restarted, and the report is dispatched normally with one added line in the task body and the comment saying the item was edited after the research started.
- **Given** the trigger label is removed and re-applied during the research → **Then** the running research still finishes and is dispatched, and the re-trigger produces `seq + 1` with its own fresh cycle afterwards — never two researches for one `task_id`.

### AC-R10 — the visible state (FR-R9)

- **Then** `worc:researching` is applied when the child starts and replaced when the report is dispatched; exactly one `worc:*` state label is present at every step; re-applying it is a no-op.

### AC-R11 — failure policy (FR-R11)

- **Given** `on_failure: proceed` and a failed research → **Then** the implementation task is built from the raw item and the comment names the failure reason.
- **Given** `on_failure: fail` → **Then** no task is written, the item is labelled `worc:failed`, and the comment names the reason.

### AC-R11a — retention (FR-R11a, D16)

- **Given** `retain: on_failure` (the default) and a successful research → **Then** `report.md` and `outcome.json` remain and `RESEARCH_TASK.md`, `agent.log`, `run.json` and the lock are gone; with the same setting and a failed or cancelled research → **Then** all of them remain.
- **Given** `retain: always` → **Then** nothing is deleted; **given** `retain: never` → **Then** only `outcome.json` remains, for both outcomes.
- **Given** `retain_max_runs: 3` and five retained runs → **Then** the two oldest directories are pruned at the next startup and after the next dispatch; `0` prunes nothing.

### AC-R12 — preconditions refuse early (FR-R12)

- **Given** `research.mode: local` and an unresolvable `git`, or no resolvable agent, or an unwritable home → **When** `watch` starts → **Then** it exits non-zero naming the failing check, before a single item is read.
- **Then** `doctor` reports every check, the connector clone's state, and any adopted or orphaned research.

### AC-R13 — the research clone cannot publish (FR-R13)

- **Then** the clone is created with its push URL disabled, and a `git push` from the worktree fails; no branch, commit or pull request originates from a research.

### AC-R14 — cancel is a real command (FR-R7, D15)

- **Given** a running research → **When** `worc-connect research cancel <task_id>` runs → **Then** the child and the agent it launched both end, the worktree is removed, `outcome.json.state` is `cancelled`, and `on_failure` decides; `status` no longer lists it.
- **Given** a child that died leaving its agent alive → **When** cancel runs → **Then** the agent is killed by the recorded `agent_pid` and the run is cleaned up.
- **Given** three running researches → **When** `research cancel --all` runs → **Then** all three end and each item follows `on_failure`.
- **Given** nothing running → **When** cancel runs → **Then** it reports that and exits zero, leaving no half-removed worktree.
- **Then** `status` shows agent, item, elapsed time and both PIDs for every running research.

### AC-R15 — cross-platform (NFR-R1)

- **Then** the detached-spawn, liveness, terminate-and-escalate and cancel seams are exercised on **both** platform branches by injecting the platform, not by depending on the host; no code path uses a signal for control flow.
- **Then** an agent launcher resolves as `claude.cmd` / `codex.cmd` on the Windows branch and as the bare names on POSIX.
- **Then** stored paths compare via `Path.as_posix()`, `outcome.json` is written atomically (temp + `os.replace`), and the whole suite passes on Windows, Linux and macOS.
