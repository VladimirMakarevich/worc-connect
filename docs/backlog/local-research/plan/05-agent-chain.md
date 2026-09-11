# Phase 05 — The agent chain and fallback

- **Status:** ☐
- **Depends on:** 04
- **Delivers:** FR-R6 — shipped `claude` and `codex` profiles, the ordered chain, fallback on failure only, a fresh worktree per attempt, the attempt record and the provenance line.

## Goal

Let the operator name both agent CLIs and have the spare pick up the work when the first one cannot do it — without ever letting the chain shop for a nicer verdict.

## Steps

1. `research/agents.py` — the shipped profiles as pinned constants (D6a): launcher, the fixed instruction sentence, the output flag and the CLI's non-interactive / auto-approval flag — **verify each flag's spelling against the installed `claude` and `codex` at implementation time and pin what is verified**, rather than carrying the sketch in this document. The table form (`name`, `command`, `args`) overrides a profile entirely; resolution is `shutil.which`, with a clear failure when a launcher is missing.
2. The chain in `research/child.py`: attempt _n+1_ runs only when attempt _n_ **failed** — unresolved launcher, non-zero exit, no `report.md`, an unparsable verdict block, or a configured timeout. A parsed verdict of any kind ends the chain, `declined` and `needs-info` included.
3. Each attempt starts from a fresh worktree: remove the previous one first, add a new one, re-render the prompt. No attempt inherits another's tree.
4. `outcome.json` records `attempts: [{name, exit_code, reason}]` and `produced_by`; `agent.log` is per attempt; the built task's provenance line and the item comment name the agent that produced the report.
5. `doctor` lists each configured agent in order: resolved absolute launcher, reported version, and the **full argv** it would run, so the auto-approval flag is visible rather than implied.

## Files touched

`src/worc_connect/research/{agents,child}.py`, `src/worc_connect/core/builder.py` (provenance), `src/worc_connect/cli.py` (doctor), `tests/`.

## Invariants in play

- The fallback is a reaction to failure, never to a verdict — the "deterministic code decides" rule in its sharpest form.
- Argv stays constant; the fixed sentence and the operator's flags are the only arguments.

## Tests

- AC-R5 in full: failure on the first agent, success on the second; `needs-info` on the first stopping the chain; an unresolvable launcher; every agent failing; the fresh-worktree assertion per attempt.

## Docs to sync in this phase

- `README.md`: the `research.agents` forms, what counts as a failure, and where to read which agent produced a report.

## Acceptance for this phase

- [ ] With the first agent broken, the second produces the report and the task names it.
- [ ] A `needs-info` verdict never triggers a second agent.
- [ ] `doctor` shows the chain in order with resolution and version.
- [ ] All gates green, both CI families.
