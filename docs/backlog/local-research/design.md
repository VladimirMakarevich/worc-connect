# Design — Local research runtime

Requirements: [requirements.md](requirements.md). Criteria: [acceptance-criteria.md](acceptance-criteria.md). Decisions are numbered `D1…D12`, independently of the [tracker-connector](../tracker-connector/design.md) record's own `D1…D16`.

## Overview

With `research.mode: local`, a gated item does not become a worc task straight away. The tick starts a **detached child process** — the connector's own `research run` subcommand — and moves on. The child fetches the connector's clone, adds a detached worktree, writes the prompt file into it, runs the configured agent there (falling back to the next agent if that one fails), copies the report out, removes the worktree and writes an outcome file. A later tick sees the outcome file and hands the report to the code phase 07 already built: parse the verdict, and either run the deterministic builder or write back `needs-info` / `duplicate` / `declined`.

```text
worc-connect watch (tick, never blocks)
  ├─ gate passes ──▶ label worc:researching ──▶ spawn detached:
  │                                              worc-connect research run <task_id>
  │                                                ├─ git -C .worc-connect/repo.git fetch
  │                                                ├─ git worktree add --detach .worc-connect/worktrees/<task_id> <base_ref>
  │                                                ├─ write RESEARCH_TASK.md (item body lives here, and nowhere else)
  │                                                ├─ agent[0] argv, cwd = worktree ──fails──▶ fresh worktree ──▶ agent[1] argv
  │                                                ├─ copy report.md ──▶ .worc-connect/research/<task_id>/report.md
  │                                                ├─ git worktree remove --force
  │                                                └─ write outcome.json (atomic), exit
  └─ next tick: outcome.json present ──▶ shared report reader ──▶ builder ──▶ tasks/preparing/<id>.md + worc promote
```

worc sees nothing until the last arrow. That is the whole point of the record.

## Decisions

### D1 — The connector gets an agent runtime, deliberately

**Decision.** `research.mode: local` launches an agent CLI from the connector process tree. The invariant "No agent runtime" is removed, not bent: phase 01 rewrites it in [AGENTS.md](../../../AGENTS.md), [.agents/rules/architecture.md](../../../.agents/rules/architecture.md) ("The model boundary"), and marks the "A second agent runtime" bullet in [tracker-connector/out-of-scope.md](../tracker-connector/out-of-scope.md) as superseded by this record, together with NFR-6 and R-5 of that folder. The replacement wording is narrow: _the connector launches an agent only for research, only behind `research.mode: local`, only in a disposable worktree of its own clone, and never to author a task._

**Why.** The alternatives keep the invariant but pay elsewhere: a second worc instance is a second daemon and a second clone for the operator to run, and a worc-side queue lane is a change in another repository that this repository cannot land. The operator chose throughput and a single process tree with the trade-off stated out loud.

**What the invariant change does _not_ license.** The model still only proposes (D5); untrusted text still never becomes an argument (D6); the connector still holds no credentials, forwards no environment, and writes into worc exactly one file.

### D2 — One switch, three modes, two providers

**Decision.** `core/research.py` declares the protocol and the outcome shape:

```python
class ResearchProvider(Protocol):
    def start(self, item: WorkItem, task_id: str) -> None: ...
    def poll(self, task_id: str) -> ResearchOutcome | None: ...  # None -> still running
    def cancel(self, task_id: str) -> None: ...
```

`worc` (tracker-connector phase 07) and `local` (this record) implement it; `cli.py`, the composition root, picks one from `research.mode` and injects it, and constructs **neither** under `off` — the loop's research branch is not entered at all, so the `off` connector is the one phases 03–06 built, not a research connector with a flag turned down. `core/` never imports `worc_connect.research`, enforced by a new `import-linter` contract, exactly as it never imports a tracker adapter.

**Why.** The operator asked for all three to be independent and switchable in one line: no research at all, research through worc, or native research. The worc-side path keeps its sandbox for anyone who wants it, the local path keeps the queue free, `off` keeps the connector as simple as it is today, and the half after the report stays shared by all of them. **Rejected.** Replacing phase 07 (it is designed, it is the safer path, and it is the fallback on a host with no agent CLI); and a pair of keys (`enabled` + `mode`), which spells the same three modes with four combinations, one of them meaningless.

### D3 — The connector's own clone, never worc's

**Decision.** `.worc-connect/repo.git` is a bare clone of the same origin, created on first use (`git clone --bare`, push URL disabled — D12), fetched before every research. Worktrees live at `.worc-connect/worktrees/<task_id>`, detached at `research.base_ref`, which defaults to the clone's own default branch — `origin/HEAD`, set by `git clone --bare` and refreshed with `git remote set-head origin -a` on each fetch, so a repository on `master` or `develop` needs no configuration and no API call is made to learn it (R-11). `git worktree remove --force` on the way out; `git worktree prune` plus a sweep of orphaned directories at startup.

**Why.** Two reasons, both practical. worc runs `git` in its clone constantly — branches, checkouts, commits, pushes — and a second process adding worktrees there would race it on `.git` metadata for no benefit. And the tracker-connector invariant "no `git` in worc's clone" survives untouched, so nothing about worc's working tree can be disturbed by a research run. **Rejected.** A worktree of worc's clone (races, and it puts the agent one `cd ..` away from the tree worc is mid-commit on).

### D4 — A child process per research, files as the state

**Decision.** The tick spawns `worc-connect research run <task_id>` detached (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows) and never waits on it. The child owns the whole research: worktree, agent chain, timeout, cleanup, outcome. State lives in `.worc-connect/research/<task_id>/`: `run.json` (pid, started_at, item, agent chain), `RESEARCH_TASK.md` (a copy of what the agent was given), `agent.log`, `report.md`, `outcome.json`. `outcome.json` is written atomically and is the only "done" signal.

**Why.** The loop stays a loop: start, poll, dispatch. A restart re-adopts by reading `run.json` and checking liveness (PID plus `started_at` against the process's own start time, so a recycled PID is not mistaken for the child). Timeout and fallback logic lives where it can act — inside the child — instead of inside a tick that must return in milliseconds. **Rejected.** Threads in the connector process (a restart loses the work and kills the agent mid-run) and a single research queue in the parent (one slow agent stalls everything).

### D5 — The model proposes, the builder still decides

**Decision.** Unchanged from tracker-connector D12: the agent's only deliverable is `report.md` with a machine-readable verdict block; `core/` parses it and the deterministic builder assembles the task. The report's `failing_test` block, when present, travels as text into the task body under a fixed heading. Nothing the agent wrote is executed by the connector, and nothing it wrote reaches worc except through the builder.

### D6 — The prompt is a file; argv is a constant

**Decision.** The item's title, body, URL and author go into `RESEARCH_TASK.md` in the worktree root. The agent argv is a fixed sentence from the shipped profile — `claude -p "Read RESEARCH_TASK.md in the repository root and follow it" --output-format json`, `codex exec "Read RESEARCH_TASK.md in the repository root and follow it"` — plus whatever constant flags the operator configured. No item-derived value is ever an argument, an environment value or a log line.

**Why.** It is the tracker-connector invariant, and independently it is the only thing that works: issue bodies carry newlines, quotes, backticks and non-ASCII, and Windows caps a command line at ~32k characters.

### D6a — The shipped profiles are constants, and `doctor` shows them in full

**Decision.** `research/agents.py` holds two pinned profiles — the launcher name, the fixed instruction sentence, the output flag, and the CLI's **non-interactive / auto-approval flag**, without which an unattended agent stops at its first permission question. The exact flag spelling of each CLI is verified against the installed tools when phase 05 is implemented, and pinned then; it is never derived at run time. `doctor` prints, per configured agent, the resolved absolute launcher, its reported version and the complete argv the connector would run — so the operator reads what is being granted instead of inferring it. Any other CLI, or a flag that moved in a new release, is the table form (`name`, `command`, `args`) in the configuration, which wins over the profile.

**Why.** The auto-approval flag is the single most consequential thing in this feature's configuration (it is what [D12](#d12--the-blast-radius-is-the-worktree-and-that-is-the-whole-of-it) is about), so it belongs in a constant an operator can read and in `doctor` output they will see — not in a heuristic that parses someone else's `--help` text and changes behaviour on an upgrade.

### D7 — The agent chain falls back on failure, not on a verdict

**Decision.** `research.agents` is ordered. Attempt _n+1_ runs when attempt _n_ **fails**: launcher unresolved, non-zero exit, no `report.md`, or a `report.md` without a parsable verdict block (a configured timeout is a failure too). A parsed verdict — including `needs-info`, `duplicate`, `declined` — ends the chain. Each attempt starts from a fresh worktree (the previous one is removed first), so the second agent never inherits a half-written tree. `outcome.json` records `attempts: [{name, exit_code, reason}]` and `produced_by`, and the built task's provenance line names the agent that produced the report.

**Why.** The operator asked for Claude Code and Codex with a fallback to the spare. Falling back on a _verdict_ would be shopping for the answer the operator likes, which is exactly the failure mode the "deterministic code decides" rule exists to prevent.

### D8 — No timeout unless the operator asks for one

**Decision.** `timeout_seconds: 0` or the key absent means the attempt is never killed by the clock. A positive value arms a timer in the child: terminate, grace period, kill the process group, record the attempt as failed, continue the chain.

**Why (and what replaces the timer).** The operator's call: a research that is genuinely working should not be shot at an arbitrary minute. The bound moves to the operator instead of the clock — `worc-connect status` lists every running research with agent, item, elapsed time and PID, and `worc-connect research cancel <task_id>` terminates one (the child cleans its worktree and writes an outcome of `cancelled`, which follows `on_failure`). `max_concurrent` caps how much can be in flight regardless.

### D9 — `worc:researching` is a real state

**Decision.** A seventh connector state, `researching`, between `gated` and `staged`, mapped by the GitHub adapter to `worc:researching`. Applied when the child starts, replaced when the report is dispatched. One state label at a time, as everywhere else.

**Why.** Without it a gated issue sits with `worc:queued` and no task in `worc list` for as long as the research runs — which, with no timeout, can be a while. The operator asked for the state to be visible.

### D10 — Failure is loud and the default is `proceed`

**Decision.** `on_failure: proceed` (default) builds the implementation task from the raw item, exactly as `research.mode: off` would, and the comment says research was skipped and names the reason. `on_failure: fail` creates no task and labels the item `worc:failed` with the reason. Infrastructure preconditions are not part of this: with `research.mode: local` and no resolvable `git` or agent, `watch` refuses to start (D11).

**Why.** Research is an enhancement to the task body, not the gate; a broken agent install should not stop the pipeline silently. But an operator who wants triage to be a real gate can have that with one key.

### D11 — Preconditions refuse at startup, not at the item

**Decision.** `worc-connect doctor` checks and prints: `git` resolution and version, each configured agent's resolution, the connector clone (present, fetchable), the home's writability, the worktree root, and any adopted or orphaned research. `watch` runs the same checks when `research.mode: local` and exits non-zero with the failing line when one does not pass.

**Why.** The alternative is discovering it per item, at which point every gated issue has already been labelled and commented on.

### D12 — The blast radius is the worktree, and that is the whole of it

**Decision.** No sandbox, by the operator's explicit choice. What is in place instead costs nothing and is kept: the agent runs in a disposable detached worktree of a clone whose **push URL is disabled** (`git remote set-url --push origin DISABLED` at creation), with the connector's own environment and no forwarded secrets, on a prompt template that states the research is read-only; the worktree is removed at the end of every attempt. What is _not_ in place, stated plainly so nobody is surprised later: the agent may run arbitrary commands on the operator's machine with the operator's own permissions, on text written by strangers on the internet. The gate (an explicit maintainer label or an author allow-list) is the only filter between an issue author and that agent, which makes it the security perimeter of this feature, not just of the queue.

**Why.** The operator weighed it and chose the lightweight runtime; the record's job is to state the trade rather than re-litigate it. The two cheap guards (no push, disposable tree) are in because they cost a line each.

### D13 — One quoted paragraph reaches the item, defused

**Decision.** Exactly one field of the report is published: `reason`, one paragraph, inside the connector's own comment template, on every verdict. It is capped (a few hundred characters, with an ellipsis and no second comment), stripped of anything that would act rather than inform on the tracker — `@mention` and `#123` defused, autolinks broken, the whole quote rendered as a blockquote so it cannot be mistaken for the connector speaking — and it is the only agent-written text that ever leaves the connector's home. The full `report.md` stays under `.worc-connect/research/<task_id>/`.

**Why.** The author of a `declined` or `duplicate` issue is owed a reason, and the author of an `actionable` one is owed the news that it was reproduced; one deterministic field gives both without turning a public tracker into a publishing channel for model output written from untrusted input. A `reason` that needs more than a paragraph is a sign the verdict is wrong, not that the cap is too small. **Rejected.** The full report in a `<details>` block; publishing nothing at all.

**Boundary note.** This is the one place where agent text reaches the tracker, and it is why the field is single, capped, quoted and defused. The item's own body still reaches nothing but the task file and the prompt file (FR-R5), which is untouched.

**What it changes in the shipped code.** Phase 07 (landed 2026-09-11) publishes the `reason` unchanged, on the no-task verdicts only, and wrote that choice down as **FU-1** in [follow-ups.md](../follow-ups.md). This decision is the revisit that entry asked for, in the shape it predicted; it applies to both producers, because both go through the one `_comment_body` in `core/writeback.py`, and it extends the paragraph to the `actionable` verdict as well. Phase 04 of this record makes the change and removes FU-1 from that page.

### D14 — A research finishes on the text it started with

**Decision.** The run directory records the item's `updated_at` when the child starts. An edit, a comment or a re-applied label while the research runs neither cancels nor restarts it. At dispatch the recorded value is compared with the current one: if the item moved, the built task's provenance block and the item comment carry one line — _the item was edited after this research started_ — and nothing else changes. The existing re-trigger rules decide what happens next: an edit alone triggers nothing, a re-applied trigger label or a reopen allocates `seq + 1` and a fresh cycle, which then researches the new text.

**Why.** Cancelling on every edit hands an issue author a way to starve their own item and to burn tokens with a series of typo fixes; finishing silently hides that the report describes text nobody can see any more. One recorded timestamp and one line of prose cover both.

### D15 — Killing a research is an operator command, and it works on every platform

**Decision.** `worc-connect research cancel` is a documented command, not a debugging aid: it accepts a `task_id`, `--item <n>`, or `--all`, and it ends **both** processes — the research child and the agent the child launched. Their PIDs are recorded side by side in `run.json` (`pid`, `agent_pid`), so an agent orphaned by a dead child is still killable, and a cancel that finds nothing alive says so and cleans up instead of failing. The child writes `outcome.json` with `cancelled`, its worktree is removed, and `on_failure` decides what the item gets.

The mechanism is the same on Windows, Linux and macOS and uses **no signals**: the agent is started in its own process group (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows), cancel terminates the group and escalates to a kill after a grace period, and liveness is a PID plus start-time check rather than `signal(0)` semantics. The whole seam lives in one module (`research/process.py`) and both platform branches are exercised by injection, never by the host OS the tests happen to run on.

**Why.** Without a clock bound, the operator is the bound — so ending a run has to be one obvious command that always works, including after the connector itself was restarted or crashed. And this is the piece of the runtime most likely to behave differently on Windows, which is where the repository's cross-platform rule bites hardest.

### D16 — Retention is the operator's setting, with a cap that always applies

**Decision.** `research.retain` decides what survives a finished research: `on_failure` (the default — a success keeps `report.md` and `outcome.json` and drops `RESEARCH_TASK.md`, `agent.log`, `run.json` and the lock; a failure or a cancel keeps all of it, because that log is the only diagnosis there is), `always` (nothing is deleted), `never` (only `outcome.json` survives). Independently, `research.retain_max_runs` prunes the oldest retained run directories beyond N (0 = no cap), at startup and after every dispatch — so no setting, `always` included, can grow the connector's home without bound.

**A note on what is in these files.** `agent.log` is the agent's own stdout captured to a file inside the connector's gitignored home, and it will contain the item's text, because the agent read it. That is not a breach of the "item text never becomes a log line" invariant, which is about the **connector's** log stream: the connector's own logging still names ids and URLs only, and nothing from `agent.log` is ever published, commented, or passed as an argument. `never` exists for an operator who does not want that file on disk at all.

## Configuration

```yaml
research:
  mode: local # off | worc | local — the one switch (D2); default: off
  flow: issue_triage # `worc` only: the flow install-flow installs
  agents: [claude, codex] # `local` only: ordered, shipped profile by name, or the table form below
  # - name: claude-custom
  #   command: claude                     # resolved with shutil.which
  #   args: ["-p", "Read RESEARCH_TASK.md in the repository root and follow it"]
  base_ref: origin/HEAD # default: the clone's own default branch (R-11)
  timeout_seconds: 0 # 0 or absent = no timeout at all (D8)
  max_concurrent: 2
  on_failure: proceed # proceed | fail
  retain: on_failure # on_failure | always | never — what survives a finished research (D16)
  retain_max_runs: 20 # prune the oldest retained runs beyond this; 0 = no cap
  keep_worktree: false # debugging aid; leaves the tree behind
```

The three modes are independent and each is a complete configuration on its own: `off` ignores every other key in the block, `worc` reads `flow` and nothing local, `local` reads the local keys and never installs a flow. A key belonging to another mode is ignored, never silently half-applied; a key **required** by the selected mode and missing is a refusal that names it (`mode: local` with no `agents`, `mode: worc` with no `flow`). Switching modes is one line and needs no other edit, and `off` is the default.

## Data & stored shapes

```text
.worc-connect/
  repo.git/                      bare clone the connector owns (push URL disabled)
  worktrees/<task_id>/           detached worktree, removed when the attempt ends
  research/<task_id>/
    run.json                     {pid, agent_pid, started_at, item_id, item_updated_at, task_id, agents, attempt}
    RESEARCH_TASK.md             what the agent was given, kept for audit
    agent.log                    child stdout/stderr, per attempt
    report.md                    the agent's report (phase 07 format, unchanged)
    outcome.json                 {state, produced_by, attempts[], finished_at}
    research.lock                exclusive create; one research per task_id
```

`outcome.json.state` is one of `report` (a report exists and parses), `failed`, `cancelled`. The connector's SQLite row gains `research_state` and `research_started_at`; like every other column it is a cache — deleting `state.db` is still safe, because a row is rebuilt from the label, `worc list` and these files.

## Control flow & state

The per-item phase becomes `seen → gated → researching → staged → queued → running → pr-open → done | failed`, with `researching` entered only when `research.mode: local`.

Each tick, for rows in `researching`:

1. `outcome.json` present → read it. `state: report` → hand the report to the shared reader; `actionable` runs the builder (body = report draft + provenance naming the agent), any other verdict writes back per phase 07. `failed` / `cancelled` → follow `on_failure` (D10). Then clear the run directory except the report and the outcome, and drop the label.
2. No outcome, child alive → nothing; the tick returns.
3. No outcome, child dead → a crashed research; record it as `failed` and follow `on_failure`.

For rows in `gated` with a free slot under `max_concurrent`: create the run directory, take `research.lock`, label `worc:researching`, spawn the child, write `run.json`.

At startup: `git worktree prune`, adopt every `run.json` whose process is alive, and treat the rest as crashed.

## Module layout

| Module | Responsibility |
| --- | --- |
| `core/research.py` | the `ResearchProvider` protocol, `ResearchOutcome`, the phase transitions |
| `core/triage.py` | the verdict block reader phase 07 shipped — called by both providers, not copied |
| `research/provider.py` | the `local` provider: start / poll / cancel over the run directory |
| `research/child.py` | the `research run` entry point: orchestrates one research end to end |
| `research/worktree.py` | clone creation, fetch, `worktree add/remove/prune`, orphan sweep |
| `research/agents.py` | shipped profiles, argv assembly, launch, timeout, the fallback chain |
| `research/prompt.py` | `RESEARCH_TASK.md` rendering |
| `research/process.py` | detached spawn, liveness, terminate/kill — the platform seam |

`import-linter` gains: `worc_connect.core` must not import `worc_connect.research`; `worc_connect.research` must not import `worc_connect.cli` or `worc_connect.config` (it takes shapes, not files).

## What changes in existing code

| File | Change |
| --- | --- |
| `config.py` | the `research` block, `research.mode`, fail-closed validation (unknown agent name, `max_concurrent < 1`, negative timeout, `on_failure` value) |
| `cli.py` | `doctor`; `research run` (internal) and `research cancel`; provider resolution; `status` gains the running-research lines |
| `core/loop.py` | the `researching` branch: start under the cap, poll, dispatch |
| `core/state.py` | two columns and the new phase |
| `core/writeback.py`, `trackers/github/adapter.py` | the `researching` state and its label |
| `core/builder.py` | provenance naming the producing agent; otherwise the phase 07 report path unchanged |
| `core/writeback.py` | the `researching` state, and the bound on the published `reason` (D13) for both producers |
| `AGENTS.md`, `.agents/rules/architecture.md`, `.agents/rules/security.md`, `README.md` | D1: the invariant rewritten, the new posture (D12) documented for the operator |
