# Implementation review — Tracker connector

Date: 2026-09-12 · Reviewer: Claude Code (deep review, requested by the owner) · Reviewed tree: the committed state of `main` at `54f3e7a` (uncommitted edits to the `local-research` record, made by another session while this review ran, were not reviewed)

Reviews the implementation of this record (phases 03–07) against [requirements.md](requirements.md), [design.md](design.md), [acceptance-criteria.md](acceptance-criteria.md), [definition-of-done.md](definition-of-done.md), the rules in [.agents/rules/](../../../.agents/rules/) and the code itself. It is a findings list, not a redesign: everything below is either a defect against what these documents promise, a divergence between two things that have to agree, or a gap the suite cannot see.

## Method

- Every source module under `src/worc_connect/` was read in full, alongside the spec folder and the two operator-facing documents ([README.md](../../../README.md), [docs/configuration.md](../../configuration.md)).
- All local gates were run: `ruff check` / `ruff format --check` / `mypy src` / `lint-imports` / `python tools/size_gate.py` / `pytest` (401 passed) / `interrogate` / `vulture` / `deptry` / `python tools/mdlint.py`. **All green.**
- Four findings were reproduced against the code rather than argued from reading: three with throwaway tests driven through the real `Watcher` and the fake `worc` (deleted afterwards), one against worc's own `scan_value`. Each is marked **reproduced**.
- The worc installed from `requirements-worc.txt` (`wastech-orchestrator 0.10.3a2.dev229+g3e898df0f`) was inspected directly for the four contract surfaces the connector depends on.

## Verdict

The build is unusually disciplined. The boundaries the record cares most about hold: the core imports no adapter, the only write into the clone is `tasks/preparing/<id>.md`, no `git` is launched, nothing is read under `.worc/`, no credential is held, item text reaches the task file and nothing else, and the generated file is judged by worc's **real** validation gate in the suite. The module docstrings carry the reasoning, not just the description.

What the review found is concentrated in one place: **the connector's model of the tracker is thinner than the tracker itself.** The test double never changes an item's `updated_at`, so two behaviours that only exist on a real tracker — the poll window moving on, and the connector's own writes bumping the item — are invisible to 401 green tests. Both produce visible, repeatable failures on a real repository. Added to that, the worc version the connector demands does not exist, which makes phases 06 and 07 unreachable in practice.

| # | Severity | Finding |
| --- | --- | --- |
| **F1** | **High** | An item whose task is still running is abandoned once the poll watermark moves past it |
| **F2** | **High** | The documented minimum worc version (`0.14.0a1`) is ahead of the worc that ships the contract (`0.10.3a2`), so phases 06 and 07 never activate |
| **F3** | **High** | A `needs-info` verdict re-triggers itself: the connector's own comment is read as the reporter's answer |
| **F4** | **High** | The title sanitizer can still emit a value worc's gate refuses, breaking FR-C4 / AC-4 |
| **F5** | Medium | `retrigger_armed` is sticky: a label cycled mid-run silently produces a second task later |
| **F6** | Medium | The test double does not model `updated_at`, which is why F1 and F3 are invisible |
| **F7** | Medium | `ensure_labels` runs once per tick, not once per process as the design and the rules state |
| **F8** | Medium | The trigger label is never created, against Q-9 and the record's own assumption |
| **F9** | Medium | A rebuilt row loses `stage` and `research_task_id`, so a deleted cache mid-triage restarts on the wrong path |
| **F10** | Medium | The record's own status, change log and phase table are stale |
| **F11** | Low | Eight smaller divergences, races and unbounded growth points |

---

## F1 — an item whose task is still running is abandoned once the watermark moves past it

**Severity: High. Reproduced.**

**What happens.** `Watcher.tick` (`src/worc_connect/core/loop.py:143`) reconciles **only the items the tracker returned for this tick**. `_advance_watermark` (`src/worc_connect/core/loop.py:412`) moves the watermark to the newest update across every listed item, gated or not, and the next listing floor is `watermark - 10 minutes`. So an item stops being listed as soon as any other issue in the repository is updated more than ten minutes after that item's last change — and a row that is not listed is never advanced.

A worc task takes minutes to hours. Between `worc:in-progress` and the pull request opening, nothing touches the issue. One comment on any other open issue in the repository is enough to evict it. From then on the row sits at `queued` or `running` forever: the PR link is never posted, the label never moves, the issue is never closed.

**Reproduction.** With a stub that honours `since` the way `gh issue list --search "updated:>="` does, one unrelated issue updated an hour later leaves the gated item's row at `queued` after five further ticks, with only `queued` ever written to the tracker.

**Why the suite misses it.** `StubAdapter.list_items` (`tests/support.py`) ignores `since` and returns every item every time, so no test can observe an item leaving the window. `test_the_next_listing_reaches_back_behind_the_watermark` asserts the floor that is _requested_, never the consequence of it.

**Why it was not caught by design either.** The design says "on every tick it also reconciles the items it already handed off", and `TrackerAdapter.get_item` exists with the docstring "Used where the listing cannot answer — an item that has dropped out of the open listing but still carries a live task" (`src/worc_connect/trackers/base.py:67`). **Nothing in the core calls it.** The seam was designed and then not wired up.

**Suggested shape of the fix.** After the listing, union it with the items of every non-terminal row the store holds, fetched by `get_item`; a fetch that fails is one row skipped, not a failed tick. That also makes the loop correct for an item closed by hand (it leaves the open listing but its task is still worc's), and it is the only change that lets the watermark stay a cheap poll filter rather than becoming the authority on what to follow.

## F2 — the minimum worc version does not exist, so phases 06 and 07 never activate

**Severity: High.**

**What happens.** `MINIMUM_VERSION = "0.14.0a1"` (`src/worc_connect/core/worc_version.py:22`), and the handshake fails closed below it. The worc that actually ships the contract — the one this repository pins in `requirements-worc.txt` and installs in CI — reports:

```text
wastech-orchestrator 0.10.3a2.dev229+g3e898df0f
```

That build **has** every surface the connector wants: `references` is in `ALLOWED_TASK_KEYS`, `_task_entry` emits `pr_url`, `_list_sections` builds the `rejected` section, `FlowDoc` carries `report_dir`. The tests prove it — `requires_worc_references` and `requires_worc_report_dir` both evaluate true here, so the real-gate and real-flow-validator suites run rather than skip. But `supports_contract("wastech-orchestrator 0.10.3a2.dev229+g3e898df0f")` is `False`, because `0.10.3 < 0.14.0`.

**Consequences, today, against the pinned worc:**

- No task ever carries `references:`, so `Fixes #<n>` never reaches a pull-request body and `write_back.close_on_merge: false` — the choice phase 06 exists to create — silently leaves every issue open.
- `pr_url` is never preferred; the pull request is always searched by branch, which is precisely the lookup that fails after a squash merge deletes the head.
- A gate rejection is always reported without its `validation_reason`.
- **`worc-connect install-flow` refuses outright**, with "the worc in this clone predates `flow.report_dir`" (`src/worc_connect/cli.py`) — so `research.mode: worc` cannot be used at all. Phase 07 is unreachable.

**Why the suite misses it.** The fake `worc` answers the handshake with `MINIMUM_WORC_VERSION` itself (`tests/support.py`, `FakeWorc._scenario`). The fake agrees with the constant; both disagree with the installed worc. Nothing asserts that the worc whose _rules_ the suite judges against is also a worc whose _version_ the connector accepts.

**Suggested shape of the fix.** Two parts. Correct the constant to the version that actually ships the contract (and the three places the documentation repeats it: [README.md](../../../README.md), [docs/configuration.md](../../configuration.md), and this folder). Then add the assertion that would have caught it: when `requires_worc_references` holds, `supports_contract(importlib.metadata.version("wastech-orchestrator"))` must be true. That test ties the version gate to the same installed worc the gate tests use, so the two can never drift again.

## F3 — a `needs-info` verdict re-triggers itself

**Severity: High. Reproduced.**

**What happens.** A `needs-info` row is re-triggered when the item has changed since the row was written (`src/worc_connect/core/loop.py:440`, `_answered`), which is the right idea: the connector asked a question, so the reporter answering it is the new request. But the stamp it compares against, `row.item_updated_at`, is written **once**, when the attempt starts (`src/worc_connect/core/loop.py:408`), and is never refreshed. Everything the connector itself then does to the item — the `worc:queued` label, the `worc:in-progress` label, the `worc:needs-info` label, the comment carrying the question — advances the item's `updated_at` on any real tracker.

So on the tick after a `needs-info` verdict is published, the item's `updated_at` is newer than the row's stamp _because the connector wrote the question_, `_answered` returns true, and a second triage task is queued. Which asks again, publishes again, bumps again. **One agent run per poll interval, for as long as the item carries the trigger label.**

**Reproduction.** With a stub that bumps `updated_at` on `set_state` and `comment` the way a tracker does, the tick after a `needs-info` verdict queues `gh-142.2` with nobody having answered.

**Why the suite misses it.** `test_a_needs_info_item_nobody_answered_is_left_alone` exists and passes — but `StubAdapter.set_state` / `comment` leave `updated_at` untouched, so the test asserts the intended behaviour against a tracker that cannot exhibit the bug. See **F6**.

**Suggested shape of the fix.** Record the moment the question was published, not the moment the attempt started: when a row concludes at `needs-info`, store the item's `updated_at` **after** the write-back (or the wall clock at which the comment was posted) and compare against that. Note the phase-07 decision that armed this path — "a `needs-info` re-trigger is armed by the item's update stamp" — is right; it is the stamp that is the wrong one.

## F4 — the title sanitizer can still emit a value worc's gate refuses

**Severity: High. Reproduced against worc's own scanner.**

**What happens.** `sanitize_title` strips leading dashes with `collapsed.lstrip("-").strip()` (`src/worc_connect/core/sanitize.py:49`). The `.strip()` after the `.lstrip("-")` can expose a **new** leading dash, and the sequence is not repeated. The code comment states the intent exactly — "Every leading dash, not just the first" — and the implementation does not achieve it.

Fed to worc's real `scan_value`:

| Item title | Sanitized to | worc's verdict |
| --- | --- | --- |
| `- -foo` | `-foo` | `value starts with '-'` |
| `-- --yolo` | `--yolo` | `value starts with '-'` (and a forbidden flag shape) |
| `- - -x` | `- -x` | `value starts with '-'` |
| `- - -` | `- -` | `value starts with '-'` |

Any title whose leading run of dashes is broken by whitespace produces a task worc's Phase A rejects with `INJECTION_SUSPECTED`. From outside worc's home that is a task that vanished; the connector does report it as `worc:failed`, so it is visible — but FR-C4 and AC-4 promise the generated file **passes worc's gate unchanged**, and the perimeter here is a title a stranger writes.

**Related, and worth checking in the same pass:** worc's scan has a third arm the connector's sanitizer does not model at all — `find_forbidden_args([stripped])`, which refuses a value that _is_ `--yolo`, `--ignore-rules`, `--allow-dangerously-skip-permissions`, anything starting `--dangerously`, or a `--sandbox` / `--permission-mode` bypass spelling. Today every such value also starts with `-` and so is caught by the same fix, but the sanitizer's comment names only the substring tokens and the leading dash, which leaves the coupling undocumented.

**Suggested shape of the fix.** Strip the leading run with a pattern rather than a single pass — `re.sub(r"^[-\s]+", "", collapsed)` — or loop until the value is stable, and add the cases above to `test_builder.py` and to `test_worc_gate.py` so the real gate judges them.

## F5 — `retrigger_armed` is sticky, so a label cycled mid-run produces a second task later

**Severity: Medium. Reproduced.**

**What happens.** A trigger label taken off arms the row (`src/worc_connect/core/loop.py:335`) whatever phase it is in, and the flag is never cleared. `_is_retrigger` then fires the moment the row reaches a re-triggerable phase (`src/worc_connect/core/loop.py:275`).

So: a maintainer removes the label while the task is running and puts it back a minute later (a correction, a mis-click, a triage pass over the label set). Nothing happens at the time — correct. Then the task finishes and its pull request opens, and on that tick the connector queues `gh-142.2` continuing the same branch, with nobody having asked for it.

**Reproduction.** Label removed at `running`, re-applied at `running`, task then `done` with a pull request: two rows, `gh-142` and `gh-142.2`.

**Why it matters.** It costs a worc run and a second body appended to an open pull request, from an action the maintainer has already undone. The design's wording — "a row **in a terminal phase** whose trigger label is re-applied after removal" — describes arming and firing as one event; the implementation separates them and keeps the arm indefinitely.

**Suggested shape of the fix.** Disarm when the label comes back while the row is not yet re-triggerable: in `_follow`, an admitted verdict on an armed, non-terminal row clears the flag. One line, and it makes "armed" mean "the trigger is currently withdrawn" rather than "was withdrawn once".

## F6 — the test double does not model `updated_at`

**Severity: Medium.**

`StubAdapter` (`tests/support.py`) is otherwise a careful double: `_relabel` writes the new label onto its own copy of the item so that "re-applying a state is a no-op" is asserted against the real mechanism, and `close` removes the item from the listing "exactly as it does on a real tracker". But neither `set_state` nor `comment` advances `updated_at`, and `list_items` ignores `since` entirely.

Those two omissions are what hide **F1** and **F3** — both of them behaviours of the field the whole polling model rests on. The fix is small and pays for itself immediately: bump `updated_at` on every write, honour `since` in `list_items`, and let the existing tests tell you which assumptions they were quietly making.

The same gap exists one level down: the fake `gh` never has to reproduce GitHub's `updated:>=` semantics, because the adapter's search string is asserted as a string rather than exercised as a filter.

## F7 — `ensure_labels` runs once per tick, not once per process

**Severity: Medium.**

`WriteBack` holds `_labels_ensured` and its docstring says the flag exists "because that is a question worth asking once per run rather than once per item" (`src/worc_connect/core/writeback.py:70`). `.agents/rules/architecture.md` states it as an invariant: "The adapter creates whichever state labels the repository lacks **once per process**." But `WriteBack` is constructed inside `tick` (`src/worc_connect/core/loop.py:151`), so the flag resets every tick and a `gh label list` goes out on every tick that changes any state.

Not expensive, but it is an invariant written down in three places and held in none of them. Either hoist `WriteBack` to the `Watcher` (it is stateless apart from this flag) or correct the docstring and the rule.

## F8 — the trigger label is never created

**Severity: Medium.**

`requirements.md` assumes "the trigger label exists on the repository or the adapter can create it (GitHub: `gh label create`)", and Q-9 was decided as "create on `init`, skip those already present". `ensure_labels` creates only the connector's **state** labels (`src/worc_connect/trackers/github/adapter.py`); nothing ever creates `gate.labels`.

On a fresh repository the operator must therefore create the `worc` label by hand or nothing is ever gated — and neither [README.md](../../../README.md) nor [docs/configuration.md](../../configuration.md) says so. The "create it on `init`" half of Q-9 was consciously moved (init is offline, per phase 05 step 1 and `architecture.md`), but the trigger label was dropped in the move rather than relocated with the state labels.

Cheapest resolution: create the configured trigger labels alongside the state labels in `ensure_labels`, or state in the operator documentation that the trigger label is theirs to create.

## F9 — a rebuilt row loses `stage` and `research_task_id`

**Severity: Medium.**

`Reconciler.rebuild` (`src/worc_connect/core/reconcile.py:123`) reconstructs a row from the item's state label plus worc's listing, and leaves `stage` at its default, `Stage.IMPLEMENTATION`, with `research_task_id` unset.

With `research.mode: worc` and a deleted cache, a row that was following a **triage** task is rebuilt as an implementation row. `_after_research` is then never reached, the report is never read, no implementation task is ever built from it, and the row instead waits for a pull request the triage flow (`publishing: none`) can never open — ending at `done` with nothing done. The record's promise that deleting `state.db` "costs one tick" does not hold on the triage path.

The signal needed to rebuild correctly does exist on disk: the report directory `.worc-connect/triage/<task_id>/` is the connector's own, and a task id found there is a triage task. It is a small amount of work, but it should be work the record acknowledges either way — today neither the design nor `architecture.md` mentions that rebuild is implementation-only.

## F10 — the record's own status, change log and phase table are stale

**Severity: Medium (documentation), and this folder is the artefact the owner reads.**

- [README.md](README.md) still says **Status: ready-to-implement**, and its change log's last connector entry is "connector phase 03 landed". Phases 04, 05, 06 and 07 landed after it and are not recorded.
- [plan/README.md](plan/README.md) shows phase 07 as `☐` in the table while the prose two paragraphs below says the chain "03 → 04 → 05 → 06 → 07 is code complete" and [plan/07-connector-triage.md](plan/07-connector-triage.md) declares itself `☑ code complete`.
- [../README.md](../README.md) (the backlog index) carries `ready-to-implement` for this row.
- [definition-of-done.md](definition-of-done.md) is entirely unticked, including the boxes that are demonstrably met (the gates, the tests, the connector baseline). The boxes that genuinely remain — the real end-to-end runs — are honestly left open in the phase documents, which is the right instinct; the rest should be ticked so that what is left stands out.
- Smaller drift: [happy-path.md](happy-path.md) Example 1 step 6 quotes the closing comment as "Fixed in `<PR URL>` (worc task `gh-142`)", while the implementation writes "Closed by the merged pull request of worc task `gh-142` (`<url>`)".

## F11 — smaller findings

Each of these is real but bounded; none of them alone justifies a phase.

1. **`_listed` skips what its docstring says it refuses.** `worc_cli._listed` documents "An entry the connector cannot read is refused rather than skipped", then silently drops any entry whose `task_id` or `status` is not a string. Only a non-`dict` entry raises. Harmless today, but the sentence is load-bearing — it is the reason the caller may treat the listing as complete.
2. **`pullrequest._is_finished` matches the status exactly.** `row.last_status == "done"` (`src/worc_connect/core/pullrequest.py:77`) where every other reader uses `status_token`. worc renders `done` bare today, so it works; the inconsistency is one display-label change away from a pull request that is never discovered.
3. **Claim race.** `_unknown_to_worc` concludes "gone for good → failed" from a per-tick listing snapshot plus a live disk check. A task worc claims between those two reads — the file leaves `pending/` before its row is visible in a listing already taken — would be reported on the item as a failure. Narrow, but it ends a row terminally.
4. **`gh label list --limit 200`.** A repository with more labels than the cap can hide an existing connector label, and `gh label create` on an existing name fails, which raises `TrackerError` and aborts the tick — every tick, permanently. Pagination or tolerating an "already exists" failure would close it.
5. **`connect.log` is unbounded.** One line per action, appended forever, with no rotation. A daemon on a busy repository grows it indefinitely; nothing in the documentation tells the operator it is theirs to manage.
6. **Stray staging temporaries.** `handoff.stage` writes `.<task_id>.XXXX.tmp` next to the target and cleans up only on `OSError`. A crash between `mkstemp` and `replace` leaves the file in `tasks/preparing/` — worc's own staging directory — where nothing ever removes it.
7. **A hand-applied state label creates a phantom row.** `_known_row` rebuilds from any `worc:*` label. Someone adding `worc:done` to an ungated issue by hand gets a terminal, unarmed row for an item that has no task, and the connector will then never take that item on. Reading a state label the connector did not write is trusted more than the design's "labels are the visible state machine" needs it to be.
8. **Triage labels are created regardless of the mode.** `ensure_labels` is handed every value of `PHASE_STATES`, so `worc:needs-info` and `worc:declined` appear on repositories where `research.mode` is `off` and nothing can ever set them.
9. **Dry run does not print the comments AC-8 names.** AC-8 asks the plan to name "items gated, task ids and branch names, labels **and comments**"; the reporter prints item, action, reason, task, branch, label and URL. Either the plan grows the comment body or the criterion should be narrowed.
10. **`gh` search stamp format.** `search_stamp` emits `…Z`; GitHub's documented example for a datetime qualifier uses `+00:00`. Worth confirming against the real API during the first real run, since a rejected qualifier would silently widen or empty the listing.
11. **README boundary bullet omits the one write into `.worc/`.** [README.md](../../../README.md) "Boundaries" says "Into worc, one write" and "never reads anything under `.worc/`"; `install-flow` writes into `.worc/flows/` (and creates the directory if worc has not). The exception is stated in the Triage section and in `AGENTS.md`, but not where the invariant is stated.

## What is solid

Stated plainly, because a findings list is not a fair picture on its own.

- **The boundary with worc is respected exactly as written.** One write (`tasks/preparing/<id>.md`, atomic, `newline=""`), two reads (`worc list --format json --all`, `worc --version`), nothing under `.worc/`, no `git`, no `state.db`. `AC-7` and `AC-N7` are asserted with a recording fake `git` on `PATH`.
- **The generated file is judged by worc's real gate**, not by a local copy of its rules, including the hostile-title and three-limit cases and the `references` line — which is why **F4** is a narrow miss rather than a class of misses.
- **The untrusted-text boundary is structural, not procedural.** Item text reaches the task body and nowhere else; the only item-derived value that reaches an argument list is an identifier proven to be a number; comment bodies travel as files. `AC-N2` plants a sentinel and greps every argv, log line and comment for it. The one deliberate exception is documented with its cost in [follow-ups.md](../follow-ups.md).
- **The fail-closed direction is chosen consistently** — an unparseable configuration, an unknown research mode, an unrecognised worc version, an unreadable report, an unknown pull-request state, an unknown task status: each resolves toward doing less, and each says so in a comment that explains why.
- **The pull-request follow-through is genuinely robust to the owner** (D14): found once by branch, read by number ever after, every derived phase recomputed from live state each tick, and the suite covers retitle, foreign commits, squash merge with branch deletion, and close-then-reopen.
- **The module shape is honest about itself.** Six modules exist that the design's layout sketch did not name (`home`, `naming`, `sanitize`, `worc_cli`, `pullrequest`, `payloads`, `github/gh`), each split along a real seam, each recorded in the phase document that introduced it.

## What this review could not verify

- **The two real runs.** The definition of done names an end-to-end run on a throwaway repository and a second one with `close_on_merge: false`; neither has been made, and the phase documents say so rather than inventing a record. **F1**, **F2** and **F3** are all findings a real run would have surfaced within an hour, which is the strongest argument for making it before anything else.
- **Windows behaviour.** Asserted by the CI matrix, which has not run on this tree here.
- **`gh`'s real search semantics** (see **F11**.10) and the exact `updated_at` bump rules of the GitHub API, which are what **F1** and **F3** turn on. Both are cheap to confirm during the first real run.

## Suggested order of work

1. **F2** — one constant and three documents; without it phases 06 and 07 are dead code and the first real run cannot exercise either.
2. **F6** — make the double model `updated_at` and `since`. Do this **before** F1 and F3, so their fixes are proven rather than asserted.
3. **F1**, **F3**, **F4**, **F5** — the four behavioural defects, each with the test the new double makes possible.
4. **F7**–**F9**, then **F10** — and tick the definition of done while it is being touched.
5. **F11** — as a single cleanup, or split across whichever phases touch those files next.
