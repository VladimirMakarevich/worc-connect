# Phase 04 — Dispatch: report to task

- **Status:** ☐
- **Depends on:** 03. The tracker-connector dependency is **satisfied**: [phase 07](../../tracker-connector/plan/07-connector-triage.md) landed on 2026-09-11 (`ca9db3b`), so the report contract, the verdict parser (`core/triage.py`) and the report → task builder already exist and are reused unchanged
- **Delivers:** FR-R5, FR-R9, FR-R10, FR-R11 — the prompt file, the `worc:researching` state, the shared report reader, the builder path, the four verdicts and `on_failure`. After this phase the feature works end to end.

## Goal

Close the loop: a finished research becomes either a queued implementation task or a verdict write-back, through exactly the code the worc-side provider uses, so the two paths cannot drift.

## Steps

1. `research/prompt.py` — renders `RESEARCH_TASK.md` into the worktree: the connector's fixed instructions (research and, where possible, reproduce; the tree is disposable and read-only; do not commit, push or open a pull request; write `report.md` with the verdict block, whose format is quoted from phase 07), then the item's title, URL, author and **verbatim body** under a fenced, clearly labelled untrusted-input section. Nothing item-derived leaves this file.
2. `core/triage.py` — the verdict-block reader phase 07 shipped (the `worc-connect-triage` fenced block, the four-word closed vocabulary, nothing at all for a broken block) is called by this provider too; if anything in it assumes the worc path, that assumption moves out rather than being copied.
3. `core/writeback.py` + `trackers/github/adapter.py` — the `researching` state and its `worc:researching` label; applied at `start()`, replaced at dispatch; one state label at a time.
4. `core/loop.py` — the real dispatch: `state: report` → read → `actionable` runs the builder (body from the report's draft, provenance naming the producing agent, the `failing_test` block under its fixed heading when present); `needs-info` / `duplicate` / `declined` write back per phase 07; `failed` / `cancelled` follow `on_failure` (`proceed` → build from the raw item and say why in the comment; `fail` → `worc:failed` with the reason).
5. `core/writeback.py` — `_comment_body` gains the bound on the quoted `reason` (D13): one paragraph, capped, blockquoted, with `@mention` and `#123` defused, on every verdict and for **both** producers; this is the revisit FU-1 in [follow-ups.md](../../follow-ups.md) asked for, so that entry is removed from the page in this phase.
6. `core/loop.py` — the staleness line (D14): compare `run.json`'s `item_updated_at` with the item's current value at dispatch and add the one-line note to the task body and the comment when it moved; never cancel or restart on it.
7. Run-directory hygiene driven by configuration (D16): `retain` (`on_failure` default / `always` / `never`) decides what survives a finished research, and `retain_max_runs` prunes the oldest retained runs at startup and after each dispatch.

## Files touched

`src/worc_connect/research/prompt.py`, `src/worc_connect/core/{triage,loop,builder,writeback}.py`, `docs/backlog/follow-ups.md`, `src/worc_connect/trackers/github/adapter.py`, `tests/`.

## Invariants in play

- Item text reaches the agent as a file and appears in no argv, environment, log line, label or comment.
- The builder, not the agent, produces the task; an unparsable report is a failure, never an `actionable` default.
- Nothing the agent wrote is executed by the connector.

## Tests

- AC-R4, AC-R9, AC-R10, AC-R11; the phase 07 verdict fixtures reused as-is, asserting identical output from both providers for the same report bytes.

## Docs to sync in this phase

- `README.md`: the full `research.mode: local` walk-through and the label vocabulary, including `worc:researching`.

## Acceptance for this phase

- [ ] A labelled issue reaches a promoted implementation task through a local research, with worc idle until the very last step.
- [ ] Each of the four verdicts produces the same outcome as the worc-side provider would.
- [ ] A sentinel planted in the issue body is found only in `RESEARCH_TASK.md` and the task file.
- [ ] All gates green, both CI families.
