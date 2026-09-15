# Local research runtime — the connector researches before it queues

Status: **needs-decisions** Date: 2026-09-11 Owner: Vladimir Makarevich Slug: `local-research`

## Summary

A second, optional path for the analyse-and-reproduce step the [tracker-connector](../tracker-connector/README.md) record designs as worc-side triage: the connector runs the research agent **itself**, in a disposable git worktree of a clone it owns, without queueing anything into worc. The point is throughput, not capability — a triage task occupies worc's queue (and, with `priority: high`, jumps ahead of implementation), so every issue the operator gates costs the orchestrator a slot before any code is written. A local research child runs beside worc, blocks nothing, and hands the deterministic task builder the same report the worc-side flow would have produced.

Everything downstream of the report is reused verbatim from tracker-connector phase 07: the report's verdict block, the four verdicts, the builder that turns an `actionable` report into the implementation task, and the `needs-info` / `duplicate` / `declined` write-backs. Only the **producer** of the report changes, so the two producers sit behind one `ResearchProvider` seam and the operator picks with `research.mode`.

Two agent CLIs are supported — Claude Code and Codex — as an ordered list with fallback: when the first fails, the second runs on a fresh worktree.

The three ways to run the connector are **one switch with three independent values** — `research.mode: off | worc | local`. `off` (the default) is the connector exactly as phases 03–06 built it, with no research anywhere; `worc` produces the report as a worc task; `local` produces it here. Switching is one line and needs no other edit.

This record deliberately **removes a hard invariant** of the connector ("No agent runtime"). That change, and the documents it touches, is phase 01's first step; see [D1](design.md#d1--the-connector-gets-an-agent-runtime-deliberately).

These documents are design detail, not an implementation contract, and must not override the hard invariants in `CLAUDE.md`, `AGENTS.md`, or `.agents/rules/` as they read at implementation time. The code remains the source of truth; this folder leaves the queue once the work lands.

**Five questions are open, and they block phase 01.** A review on 2026-09-12 — after every document below had been signed off — read this record against the code that actually shipped and found seven shapes it does not pin down. Three were answered the same day: the symmetry between the three modes lives in the **configuration** and not in the code, so only the local producer goes behind the protocol (R-19); a finished research is noticed by a second pass over the store's own live rows, which costs a quiet tick nothing (R-18); and the bare clone and its worktrees live in a `research.workspace` outside worc's clone, while the run directories stay in the connector's home (R-20, decided against worc's own source). The four still open are the id a local research is keyed by, who renders the prompt file, what environment the agent gets, and whether `gate.allow_all` stays legal with `mode: local` — [R-17, R-21, R-22, R-23](questions.md#open), with an **Open** marker in every document one of them decides. None of this is a change of direction: they are the seams the phases would otherwise have had to invent mid-implementation.

## Documents

| # | Document | Purpose | Signed off |
| --- | --- | --- | --- |
| 1 | [problem.md](problem.md) | The problem, in the operator's own words | ☑ |
| 2 | [requirements.md](requirements.md) | What the local research runtime must do | ☑ |
| 3 | [out-of-scope.md](out-of-scope.md) | What this task explicitly does **not** cover | ☑ |
| 4 | [design.md](design.md) | Technical design and the decisions behind it | ☑ |
| 5 | [acceptance-criteria.md](acceptance-criteria.md) | Testable pass/fail criteria | ☑ |
| 6 | [definition-of-done.md](definition-of-done.md) | When the task is truly finished | ☑ |
| 7 | [questions.md](questions.md) | Open questions and the decisions already taken (living) | ☑ |
| — | [plan/README.md](plan/README.md) | Phased implementation plan (01–05) | ☑ |

## Relationship to the tracker-connector record

| Concern | Where it lives |
| --- | --- |
| The polling loop, the gate, the builder, the write-back, the state machine | [tracker-connector](../tracker-connector/README.md), phases 03–06 (code complete) |
| The report format, the four verdicts, report → implementation task, the `needs-info` round-trip | tracker-connector [phase 07](../tracker-connector/plan/07-connector-triage.md), **landed 2026-09-11** (`ca9db3b`) — a dependency of this record that is already satisfied, and code this record calls rather than copies |
| Who produces the report, and where the agent runs | this record |

## Change log

- 2026-09-12 — three of the seven review questions were decided with the operator: R-19 (the symmetry between modes is in the configuration, not in the code), R-18 (a second pass over the store's live rows dispatches a finished research; costs measured against `gh`), and R-20 (a `research.workspace` outside worc's clone for `repo.git` and `worktrees/`, run directories staying in the connector's home). R-20 was settled against the orchestrator's own source: worc creates no worktrees and never runs `git clean`, which withdrew one argument, while its `GitControlState` fingerprint and the agent CLIs' ancestor walk carried the decision.
- 2026-09-12 — the record was reviewed against the shipped code before implementation started. Seven questions were opened ([R-17…R-23](questions.md#open)); six contradictions inside the record were corrected in place (the default mode in [out-of-scope.md](out-of-scope.md), the retention rule against the control-flow description, the report directory's real name, the "no signals" wording, the platform count, and AC-R9's "identical" claim); and eight implementation risks the plan did not carry were added to [plan/README.md](plan/README.md). The folder left `ready-to-implement` for `needs-decisions`.
- 2026-09-11 — the operator signed off every document and set the folder to `ready-to-implement`. Implementation order is 01 → 02 → 03 → 04 → 05; nothing is blocked from outside this repository.
- 2026-09-11 — folder scaffolded from the design conversation of the same day. The operator chose the local runtime over a worc-side triage lane after the trade-off was laid out, and decided: the two providers coexist; a new `worc:researching` state; the report format is taken from phase 07 unchanged; a `doctor` precondition check; no timeout unless the operator asks for one; both Claude Code and Codex with fallback. Nothing implemented.
- 2026-09-11 — tracker-connector phase 07 landed while this record was being drafted, already carrying the `research.mode` vocabulary decided here, with `local` reserved and refused by name (`IMPLEMENTED_RESEARCH_MODES` in `config.py`). Phase 01 of this record turns that refusal into the second provider; phase 04's dependency is satisfied.
- 2026-09-11 — the six drafting questions were walked through one at a time and all closed (R-10…R-16 in [questions.md](questions.md)): one switch `research.mode: off | worc | local` replacing the `triage.enabled` + `triage.mode` pair in **both** records; `base_ref` defaulting to the clone's own default branch; the report's `reason` paragraph quoted on the item, defused, and nothing else published; a research finishing on the text it started with, with a staleness note; `research cancel` promoted to a first-class command (by id, by item, `--all`, and able to kill an orphaned agent) with the cross-platform kill path called out; the agent profiles pinned as constants and printed in full by `doctor`; retention configurable (`retain`, `retain_max_runs`). No open questions remain.
