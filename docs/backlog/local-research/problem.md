# Problem — Local research runtime

## The problem, as stated

The operator's idea, in their own framing (translated from the conversation of 2026-09-11):

> While the phases are being implemented, I want to think through a small improvement for the next version. I want the connector to be able — with a switch to turn it on and off — to research and reproduce the project **before** it creates a task for the orchestrator, on its own, without involving the orchestrator, so that the orchestrator is not blocked from doing implementation work. The connector starts, sees an issue on GitHub, pulls it in, does the research and maybe even tries to reproduce it if it can, and only then, with the whole context in hand, creates the artefacts and the task for the orchestrator. And it is important that it does not block the orchestrator — it should probably do this in a worktree.

Asked to choose between a worc-side triage lane (a second queue or a second worc instance) and a runtime inside the connector, the operator chose the runtime, explicitly without a sandbox: _"a lightweight runtime that runs an agent for research in a worktree"_. That choice, its cost and the invariant it removes are recorded in [design.md](design.md); it is an input here, not an open question.

## Whose problem it is

- **The operator running `worc` and `worc-connect` side by side** — they pay for triage twice: once in tokens, once in orchestrator throughput.
- **The orchestrator's queue** — every gated issue currently costs a worc task before anyone knows whether the issue is even actionable, and the triage task is queued with `priority: high` (tracker-connector D12), so it is served ahead of the implementation work that is already in flight.

## Current situation (the pain)

With `research.mode: worc` as designed in tracker-connector phase 07:

1. a gated issue becomes a **triage** worc task (`priority: high`);
2. worc claims it, occupies a slot, runs the flow in its own sandbox and writes a report;
3. the connector reads the report and queues a **second** task — the implementation one;
4. only now does the work the operator actually cares about start.

So the first thing a new issue does is push implementation work back in the queue, and a `needs-info` or `duplicate` verdict means the slot was spent to learn that nothing should be built at all. Ten issues labelled in one morning are ten queue-jumping triage tasks ahead of the feature that is mid-flight.

Nothing about the research step needs worc: it reads a repository, runs the project's own commands, and writes a Markdown report. It needs worc's queue only because the connector was forbidden to launch an agent.

## Why it matters now

- Phases 03–06 of the connector are code complete; phase 07 is next, and it is the phase whose shape this changes. Deciding now costs a design document; deciding after 07 ships costs a rewrite of the path it built.
- The operator's own machine already has both agent CLIs logged in (`claude`, `codex`) — the runtime is a subprocess and a worktree, not an infrastructure project.
- Research is the step where cost is smallest and value is highest: a verdict of `duplicate` or `needs-info` saves a whole implementation run.

## Signals & evidence

- `docs/backlog/tracker-connector/design.md`, D12: the triage task is queued with `priority: high` "so it does not wait behind long implementation tasks" — the queue pressure is by design, and it points the wrong way once triage is the common case.
- `docs/backlog/tracker-connector/requirements.md`, NFR-6: "a gated item costs exactly one worc task (two when triage is on)".
- `docs/backlog/tracker-connector/out-of-scope.md`: "A second agent runtime — the connector never launches Claude Code or Codex itself." This record supersedes that line; see [D1](design.md#d1--the-connector-gets-an-agent-runtime-deliberately).
- `src/worc_connect/core/reconcile.py` and `core/builder.py` already own the phase table and the task assembly this path plugs into; the loop already runs a tick that must not block (`core/loop.py`).
