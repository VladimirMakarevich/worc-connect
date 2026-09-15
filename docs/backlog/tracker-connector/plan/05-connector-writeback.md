# Phase 05 — Write-back and reconciliation

- **Status:** ☑ code complete on `feat/connector-v1` — the two acceptance lines below are real runs and stay open until an operator has made them
- **Depends on:** 04
- **Delivers:** FR-C9, FR-C10, FR-C11, FR-C12 — state labels, comments, the PR link, close on merge and the failure path, all derived from `worc list --format json` and the PR found by branch; the first real end-to-end run.

## Goal

Make the connector visible to the people who use the tracker and make its state recoverable from the sources of truth. Moves AC-9, AC-10, AC-11, AC-12, AC-E3, AC-E4, AC-E5, AC-E6, AC-N3, AC-N6.

## Steps

1. `trackers/github/` write side: `set_state` (remove the previous `worc:*` label, add the new one, in one `gh issue edit` call), `comment` via `--body-file`, `close` via `gh issue close --comment` — the one body that travels as an argument, because the tool has no file form for it, and a connector-authored template is safe there for the same reason a label name is — `ensure_labels` via `gh label create` (skip when present; Q-9), run once per process before the first state is published rather than at `init`, which is offline.
2. `core/writeback.py` — the six states, "exactly one label, re-apply is a no-op", comment templates (task id; PR URL; status + `worc status <id>`; rejection reason string) — templates only, never item text.
3. `core/reconcile.py` (second half) — the phase table from design "Control flow & state": `queued`/`running` from `worc list --format json --all`, matching the leading token of the `status` label (`new`/`validated`/`preparing`/`pending` → queued, `running` → in-progress, including `running (paused…)` and `parked (no daemon)`); `done` → find the PR by branch **once** and store its number and URL on the row; from then on read the PR by number (`get_pull_request`, for GitHub `gh pr view <n> --json state,mergedAt,url,title`) and recompute the PR-derived phase every tick — `mergedAt` → close (if configured) → done; `CLOSED` unmerged → failed with one comment, back to `pr-open` if reopened; `OPEN` → stay, whatever commits, title or branch changes happened (D14). `failed`/`manual_action_required` from `worc list` → failed; a gate reject (file gone from `pending/`, no row in `--all`) → failed with a comment that names the task id and `worc status <id>` and, in this phase, no reason — the connector does not read `.worc/`; phase 06 adds the `validation_reason` from the listing's `rejected` section (FR-W3).
4. Rebuild rows from labels + `worc list` when the state database is missing (AC-12); a `worc:pr-open` label with no stored number re-runs the by-branch discovery.
5. Follow-up on an open PR (Q-11, decided yes): a re-trigger from `pr-open` builds the new task with `branch_mode: existing` / `branch_ref: <previous branch>`; from a terminal phase, or once the PR is merged or closed, a fresh branch.
6. Log lines per action; `status` shows rows, phases, watermark, last tick.
7. First real end-to-end run on a throwaway repository, recorded in the connector README.

## Files touched

- connector: `src/worc_connect/core/{writeback,reconcile}.py`, `src/worc_connect/trackers/github/`, `tests/`.

## Invariants in play

- Comment bodies travel as files; label names come from configuration; nothing item-derived but the number and the URL appears in a comment or a log line.
- No log, diff, or error text from worc is posted to the tracker — the comment points at `worc status <id>` on the host.
- The connector follows worc's outcome; it never edits, re-queues or reruns a task on its own.
- The connector reads `worc list --format json` only — not `state.db`, not the ledger.

## Tests

- Label transitions and idempotency (AC-9); the three comment kinds and their contents (AC-10); close on merge on/off (AC-11); rebuild from labels (AC-12); rejection with reason string (AC-E3); PR closed unmerged (AC-E4); label removed while queued (AC-E5); watermark skew (AC-E6); no token anywhere (AC-N3); quiet tick writes nothing (AC-N6); owner's commits + retitle + squash merge + deleted branch (AC-16); follow-up on an open PR vs a merged one (AC-17); PR closed then reopened (AC-E9).

## Docs to sync in this phase

- Connector README: what each `worc:*` label means, what the operator does on `worc:failed`, the auto-merge caveat, and the recorded end-to-end run.

## Acceptance for this phase

- [ ] One real run: label → `worc:queued` + comment → `worc:in-progress` → `worc:pr-open` + PR link → merge → issue closed + `worc:done`.
- [ ] A second real run in which the owner pushes a commit to the PR, retitles it and squash-merges it with branch deletion: the issue still closes and the row ends `done`.
- [ ] AC-9 … AC-12, AC-E3 … AC-E6, AC-N3, AC-N6 pass on both CI families — green locally on macOS; the matrix runs when the branch is pushed.

## What the phase actually delivers

- The five states are published as labels, one at a time, and the state the **item** already shows decides whether anything is written: re-applying it writes nothing, and a comment is exactly what a state change sounds like, so a quiet tick and a rebuilt row are both silent.
- Three comments and a closing message, all templates: the task id when the task is queued, the pull-request URL when it opens, the status plus `worc status <id>` when the task ends without one, and the task id plus the URL when the merge closes the item. A test plants a sentinel in an item's title and body and finds it in none of them.
- The pull request is found once by the branch the connector named and read by number ever after, and every phase derived from it is recomputed from its live state on every tick: a merge closes the item whoever merged it and however, a closed-unmerged request fails the row once, and the same request reopened puts the row back.
- A task worc finished without opening a pull request ends the row at `done` and closes nothing — the branch query is answered from the pull requests themselves rather than from a search index, so "none" is an answer and not a lag.
- Rows are rebuilt from the item's own label plus worc's listing and queue folder when the database is gone, so a deleted cache costs a tick and never a second task; a `worc:pr-open` label with no stored number re-runs the by-branch discovery.
- A re-trigger is armed by an event the connector observed — the trigger label withdrawn, or the item closed because its pull request merged — and only after the close actually happened, so a tracker that refused the close cannot leave a row the next tick reads as a fresh request. A trigger put back while the task is still in flight disarms the row again, so a label cycled mid-run never becomes a second task when the task ends ([review](../review.md), F5). From `pr-open` the follow-up continues the same branch (`branch_mode: existing`); from a terminal phase it gets a fresh one.
- Every item whose row is still in flight is followed whether or not the poll window still lists it: the listing is a filter on updates, and an item the listing left out is read by identifier (`get_item`) in the same tick, an unreadable one costing its row one tick ([review](../review.md), F1). The re-trigger bookkeeping lives in `core/retrigger.py`, split out of the loop along that seam.
- Two modules are not in the design's layout sketch: `core/pullrequest.py`, which owns find-once-then-read-by-number and the recomputation, split out of the reconcile so the property that makes the owner's edits harmless is readable on its own; and `trackers/github/payloads.py`, which owns turning `gh`'s JSON into records, so the adapter reads as the list of things the connector does to GitHub.
- **Not done:** the real end-to-end run. It needs a repository, a `gh` login and a `worc watch`, and inventing a record of it would be worse than leaving the box unticked.
