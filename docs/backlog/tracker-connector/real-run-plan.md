# Real-run test plan — per-label flow, auto-merge and the rest of the connector

Status: **done** (2026-11-18), findings fixed on branch `fix/real-run-2-findings` · Started: 2026-11-18 · Repository under test: `VladimirMakarevich/wastech-mdlint` (public) · Back to the [folder README](README.md)

The first two real runs (recorded in [definition-of-done.md](definition-of-done.md)) proved the happy path with `research.mode: off` and with triage. Since then `main` gained the per-item choices — `task.task_type_by_label` and `task.auto_merge_labels` (see the [configuration reference](../../configuration.md#choosing-the-flow-and-the-merge-per-item)) — and a round of fixes from those runs. This plan exercises the new keys for real and walks the paths the first runs never took: auto-merge, a flow worc does not know, re-trigger, a follow-up on an open pull request, a pull request closed and reopened, `close_on_merge: true`, the triage verdicts other than `actionable`, and the operational refusals.

Every check has an id, an expected result and a result cell. Results, findings and the run log are filled in this file as the runs happen.

## Summary

**Forty-odd checks run for real, against worc `0.16.0a1` on `VladimirMakarevich/wastech-mdlint`.** The new keys work as specified: `task_type_by_label` chose `implementation_skills` (#29, #38, #32's implementation task) and an unknown flow (`flow:broken`), `auto_merge_labels` matched case-insensitively and worc merged three pull requests with nobody in between (#42, #44, #47); labels changed mid-run changed nothing, the load-time checks refused every bad shape, and a dispatch label alone gated nothing. The rest of the loop held too: re-trigger with a new id after a deleted cache, a follow-up continuing the open pull request (`branch_mode: existing`, #43 reused), a pull request closed and reopened, the daemon's PID file and stop sentinel, `HomeNotIgnored`, a bad token, the sanitizer and the untrusted-text boundary.

**What needs fixing, by weight:**

| Priority | Finding | In one line |
| --- | --- | --- |
| 1 | F-7 | Triage scratch files in `.worc-connect/triage/` are linted as project code and fail every later task's checks. |
| 2 | F-8 | `worc rerun`, the documented recovery, is not followed: the item stays `worc:failed` after its pull request merges. |
| 3 | F-6 | A `needs-info` reply re-runs triage without the reply. |
| 4 | F-2 + F-3 | With auto-merge the item never gets its pull-request link; the closing message is lost whenever GitHub closes first, and the log claims a close that did not happen. |
| 5 | F-4 | A pull request closed unmerged is reported as "opened no pull request". |
| 6 | F-5 | Triage cannot see other open items, so a duplicate comes back `actionable`. |
| 7 | F-1, O-3 | A triage task inherits `auto_merge`; the trigger label is accepted as an auto-merge label. |
| 8 | O-1, O-2, O-4, O-5, O-6 | Messages and docs: `status` on an old cache, the dry run hides the per-item choices, search-index lag, GitHub's label order, repeated pull-request comments. |

W-1 and W-2 belong to the orchestrator's backlog. Phase 4's T-7 and T-8 did not reach their intended verdicts (F-6, F-5); everything else in the tables below has a result.

**Left behind on the repository:** issues #29, #32, #37, #38, #39, #40, #46 closed (#32 wrongly labelled `worc:failed`, F-8); #45 open, labelled `worc:failed` after its interrupted re-triage; #30 and #33 untouched; the labels `automerge`, `flow:skills` and `flow:broken` kept. In the clone: `main`, clean; the connector's configuration, the three flows and their reasoning restored; no watcher running — neither the connector nor `worc watch`, which had already exited before the run. Kept in the session scratchpad only: `gh-46.2`'s task file, `gh-32.2`'s first diff, the triage scratch files.

## Environment

| What | Value |
| --- | --- |
| Connector | `main` at `e63ce02` (pull request #6, per-label flow and auto-merge), editable install in this repository's `.venv` |
| worc | `0.16.0a1` (`~/.local/bin/worc`). The `worc watch --queue default` the operator's `worc shell` had started exited after the night run (a zombie, pid 79627), so a fresh one runs for this test: `worc watch --queue default --poll-seconds 60` |
| worc policy | `git.auto_merge: false`, `auto_merge_strategy: squash`, `auto_merge_wait_for_checks: false` — a task with `auto_merge: true` is merged by worc right after its pull request opens |
| Flows in the clone | `implementation`, `implementation_skills` (adds an advisory `code_review` node), `issue_triage` (edited copy, `review` on codex), `deep_research`, `security_audit`, `merge` |
| Reasoning for this run | Lowered at the user's request, the tasks being small: `planning`, `implementation`, `code_review` → `low`; `review` (codex) and `fixing` → `medium`; every `issue_triage` node → `low`. **Local only**: the three flow files carry `git update-index --skip-worktree`, so worc's clean-tree check passes and no pull request picks the edit up; undone after the run with `--no-skip-worktree` and `git checkout`. The supervisor's levels in `.worc/config.yaml` are unchanged |
| Connector home | `.worc-connect/` in the clone, ignored by a committed `.gitignore` line; its `state.db` is schema 4, this build's is 5, so the first real tick rebuilds it |
| Starting config | `research.mode: "worc"`, `close_on_merge: false`, `poll_interval_seconds: 60`, `commit_type_by_label: { bug: fix, documentation: docs }` |
| Stray file | `tasks/preparing/security-audit-mdlint.md` is not the connector's — left alone |

## Ground rules

- **One configuration per phase.** The configuration is read when a process starts, so every change is followed by a stop (sentinel) and a fresh start.
- **What is recorded per run:** the task file's front matter (from `tasks/pending/`, `tasks/done/` or `tasks/failed/`), the `connect.log` lines for the item, the item's label and comment timeline, the pull request, and `worc list --format json --all` for the task.
- **Auto-merge lands on `main` of a published package**, with no human in between — so the items opted in are the lowest-risk ones (a code comment, a test fixture, a one-line fix).
- **Operator actions** (creating labels and issues, labelling, closing and reopening a pull request, replying on an issue) are run with the operator's own `gh` login as this plan describes. **Every manual merge is asked for separately** at the moment it is due.
- **Cost:** roughly ten agent runs in total — two in phase 3A, three in 3B, five or six in phase 4. The cheap checks (phases 0–2, the unknown-flow and sanitizer probes) come first.

## Labels and issues

### Labels to create

The connector creates its state labels and the trigger label, never the dispatch labels (`gh label create … --repo VladimirMakarevich/wastech-mdlint`):

| Label | Mapped to |
| --- | --- |
| `automerge` | `task.auto_merge_labels: [AutoMerge]` — spelled differently on purpose, matching is case-insensitive |
| `flow:skills` | `task_type_by_label` → `implementation_skills` |
| `flow:broken` | `task_type_by_label` → `missing_flow`, a flow that does not exist in `.worc/flows/` |

### Items

| Key | Item | Source | Labels applied | Used in |
| --- | --- | --- | --- | --- |
| R1 | #29 — concepts guide overstates what front matter keeps out of slice and impact | existing | `worc`, `documentation`, `flow:skills` | 3A |
| R2 | #37 — `document-types.ts`: the images consumer comment omits CTX-005 | follow-up `mdl-ideas-p3-02-ctx-005-image-alt` | `worc`, `automerge` | 3A |
| R3 | #38 — SEC-006 trims leading whitespace from the parsed emphasis text | follow-up `mdl-ideas-p2-05-sec-006-emphasis-as-heading` | `worc`, `bug`, `flow:skills`, `automerge` | 3B |
| R4 | #39 — rule aliases: the custom-rule collision rationale is inaccurate | follow-up `mdl-ideas-p3-05-rule-aliases` | `worc`, `documentation`, `flow:broken` → later without it | 3B |
| P1 | #40 — title-sanitizer probe: a hostile title and a sentinel string in the body | synthetic, closed after the check | `worc`, `flow:broken` | 3B |
| T1 | #32 — REF-007: CRLF fixture for line/column of undefined-reference findings | existing | `worc`, `enhancement`, `flow:skills`, `automerge` | 4 |
| T2 | #45 — deliberately thin report ("some findings look wrong after the fixes") | synthetic; the reply carries follow-up `mdl-ideas-p2-06` (textSpans exclusion) | `worc` | 4 |
| T3 | #46 — a duplicate of #32 | synthetic | `worc` | 4 |
| S1 | #30 — REF-002: harden `extractHtmlAnchors` | existing; used only for the state-label check, never queued | `worc` + `worc:queued` by hand | 2 |

#33 stays unused, a spare.

## Configuration per phase

The `task` section from phase 1 on (the rest of the file as it is today):

```yaml
task:
  task_type: implementation
  task_type_by_label:
    { "flow:skills": implementation_skills, "flow:broken": missing_flow }
  queue: default
  priority: mid
  commit_type: feat
  commit_type_by_label: { bug: fix, documentation: docs }
  branch_prefix: worc
  id_prefix: gh
  auto_merge_labels: [AutoMerge]
```

| Phase | `research.mode` | `close_on_merge` | How it runs         |
| ----- | --------------- | ---------------- | ------------------- |
| 3A    | `"off"`         | `false`          | `watch` as a daemon |
| 3B    | `"off"`         | `true`           | `watch` as a daemon |
| 4     | `"worc"`        | `true`           | `watch` as a daemon |

## Phase 0 — preflight (no writes)

| Id | Check | Expected | Result |
| --- | --- | --- | --- |
| P0-1 | Connector build | `worc-connect` from the editable install runs; the build knows `task_type_by_label` and `auto_merge_labels` | ✅ `0.1.0.dev0`, `e63ce02` |
| P0-2 | `worc --version` | `0.16.0a1` ≥ `0.14.0a1`: `references`, `pr_url` and the `rejected` listing are adopted | ✅ `0.16.0a1` |
| P0-3 | `gh auth status` | logged in, `repo` scope | ✅ scopes `repo`, `workflow`, `read:org`, `gist` |
| P0-4 | worc daemon | `worc watch --queue default` alive; `worc list --format json --all` parses; nothing pending | ✅ pid 79627 alive; 20 entries, all `done`; `tasks/pending/` empty |
| P0-5 | The clone | clean `main`; the `.worc-connect/` line is in the committed `.gitignore` | ✅ clean `main` at `a136a1a`; `.gitignore:25` |
| P0-6 | Flows | `implementation`, `implementation_skills`, `issue_triage` present; `missing_flow` absent | ✅ |
| P0-7 | `install-flow` without `--force` on the edited `issue_triage` copy | reports the copy as edited and writes nothing (hashes unchanged) | ✅ exit 1: `refused issue_triage.yaml`, the five role prompts `unchanged`; hashes identical |
| P0-8 | `status` on the schema-4 cache | observe what it says about rows the cache does hold | ❗ O-1 confirmed: "no items taken on yet", the cache holds 3 rows |
| P0-9 | The auto-merge caveat | no issue template and no workflow in the repository applies labels | ✅ `.github/` holds only `workflows/` (CI, publish, mdlint); none runs on issues or labels |

## Phase 1 — configuration validation (offline, scratch homes)

Each case is a `config.yaml` in its own scratch home, checked with `worc-connect status --home <dir>/.worc-connect`. A refusal is exit code 2 with a message naming the key; nothing is written. In every case below the home held only `config.yaml` afterwards — `status` opens the cache read-only and creates nothing.

| Id | Configuration | Expected | Result |
| --- | --- | --- | --- |
| C-1 | `auto_merge_labels: ["worc:failed"]` | refused, names `task.auto_merge_labels` and the prefix | ✅ exit 2 |
| C-2 | `auto_merge_labels: ["WORC:Done"]` | refused — the prefix check ignores case | ✅ exit 2, the message keeps the operator's spelling |
| C-3 | `task_type_by_label: { "worc:queued": implementation }` | refused, names `task.task_type_by_label` | ✅ exit 2 |
| C-4 | `labels_prefix: "bot:"` with `auto_merge_labels: ["worc:failed"]`, then `["bot:x"]` | the first accepted, the second refused — the check follows the configured prefix | ✅ exit 0, then exit 2 naming `bot:` |
| C-5 | `task_type_by_label: { "flow:x": [a] }` | refused: a value must be a string | ✅ exit 2 |
| C-6 | `task_type_by_label: [a, b]` | refused: must be a mapping | ✅ exit 2 |
| C-7 | `auto_merge_labels: automerge` (a scalar) | refused: must be a list | ✅ exit 2 |
| C-8 | `auto_merge: "yes"` | refused: must be a boolean | ✅ exit 2 |
| C-9 | `auto_merge_label:` (typo) | refused as an unknown key | ✅ exit 2 |
| C-10 | `gate` with no rule at all | refused, names `gate` (regression) | ✅ exit 2 |
| C-11 | `research.mode: off`, unquoted | refused with a reason rather than read as some mode | ✅ exit 2, the message says to quote the value |
| C-12 | `research.mode: "local"` | refused by name — not implemented | ✅ exit 2, lists `off, worc` |
| C-13 | `task_type_by_label: {}`, `auto_merge_labels: []` | accepted | ✅ exit 0 |
| C-14 | The phase-3A configuration | accepted | ✅ exit 0 |
| C-15 | `auto_merge_labels: [WORC]` — the trigger label itself | added during the run: is the trigger refused as an opt-in? | ❗ accepted, exit 0 — see O-3 |

## Phase 2 — dry run and refusals (no tracker writes)

Run after the labels and items exist, before the first real tick of phase 3A.

| Id | Check | Expected | Result |
| --- | --- | --- | --- |
| D-1 | `watch --once --dry-run` with R1 and R2 labelled | both planned as `stage-task` with task id, branch and `worc:queued`; no file in `tasks/preparing/`, `state.db` and `connect.log` unchanged, no `edit`/`comment`/`label create` call | ✅ the first attempt listed 6 of 8 open items — #37 and #39, created a minute earlier, were not in GitHub's search index yet (O-4); 30 s later `listed=8`, #29 and #37 planned as `stage-task`. `state.db`, `connect.log` and `tasks/preparing/` unchanged both times |
| D-2 | An item carrying `flow:skills` and `automerge` but no `worc` | `skip`, reason `no-trigger-label` — dispatch labels gate nothing | ✅ #38: `skip`, `no-trigger-label` |
| D-3 | What the plan line says about the per-item choices | observe whether the chosen flow and the auto-merge are visible before anything is written | ❗ O-2 confirmed: `task=`, `branch=`, `label=` only |
| D-4 | `GH_TOKEN=bogus watch --once` | exit 1, the tracker failure logged, no state, no label, no task | ✅ exit 1, `skipped-TrackerAuth` logged, no item row, no label. The tick did rebuild the schema-4 cache first and recorded `last_tick_result: skipped TrackerAuth` — A-1 happened here |
| D-5 | `.gitignore` without the home's line, `watch --once --dry-run` | exit 1 (`HomeNotIgnored`) before any tracker call; the line is restored with `git checkout .gitignore` | ✅ exit 1, `skipped-HomeNotIgnored` naming the line to add; restored |
| D-6 | S1: #30 with `worc` and a hand-applied `worc:queued` | skipped and logged as `state-label-without-task`, no task. Clean-up removes `worc` **first**, then `worc:queued` | ✅ dry run and real tick: `action=rebuild result=no-task`, then `skip state-label-without-task`; cleaned up |

## Phase 3 — real runs, `research.mode: off`

### 3A — `close_on_merge: false`, daemon; R1 and R2 labelled together

| Id | Check | Expected | Result |
| --- | --- | --- | --- |
| A-1 | First real tick on the schema-4 cache | `state.db` rebuilt at schema 5; nothing staged for the closed #24 and #31 | ✅ at D-4: rows dropped, schema 5; nothing staged for #24 or #31 |
| A-2 | Daemon control | `connect.pid` exists; a second `watch` refuses with exit 1 | ✅ `connect.pid` with pid and start time; a second `watch` exits 1 (`another watcher holds …`) |
| A-3 | R1 front matter | `task_type: implementation_skills`, `commit_type: docs`, no `auto_merge`, `references: ["Fixes #29"]` | ✅ exactly as expected |
| A-4 | R1 runs the chosen flow | worc's log for `gh-29` shows the `code_review` stage | ✅ planning → implementation → checks → review (codex) → **code_review** → documentation → supervisor finalize, 17:39–17:42; `planning` launched with `--effort low` |
| A-5 | R1 lifecycle on the issue | `worc:queued` + "Queued as worc implementation task" → `worc:in-progress` → `worc:pr-open` + the pull-request comment; one state label at a time | ✅ queued comment → `worc:in-progress` → `pull-request found-41` → `worc:pr-open` + "opened a pull request: …/pull/41"; one state label throughout |
| A-6 | Add `automerge` to #29 while `gh-29` runs | the task's front matter is unchanged and worc does not merge — labels are read at build time | ✅ `automerge` added at 17:39 while `gh-29` ran: `tasks/done/gh-29.md` has no `auto_merge`, pull request #41 stays open and unmerged |
| A-7 | Remove `worc` from #29 and re-apply it while `gh-29` still runs | withdrawal logged, the row disarmed, no `gh-29.2` after the task ends | ✅ so far: removal logged `follow gate-withdrawn` + `retrigger armed`; the trigger put back a tick later logged `retrigger disarmed`. No `gh-29.2` after the task ended, through the merge and the close ✅ |
| A-8 | Stop the daemon while `gh-29` runs, delete `state.db`, start it again (a running process keeps writing to the unlinked file, so a delete under it proves nothing) | the next tick rebuilds the row from the label and `worc list`; no second task, no repeated comment | ✅ the restarted daemon logged `rebuild result=in-progress` for #29 and `rebuild result=queued` for #37; rows rebuilt with `queued_announced` set, one comment on each item, no new task |
| A-9 | Manual merge of R1's pull request _(asked for at the time)_ | GitHub closes #29 through `Fixes #29`; the connector labels `worc:done`, no `close` call | ✅ merged by the operator's go-ahead at 18:31 with a merge commit, as #34 and #35 were — under `close_on_merge: true` by then (the `false` path is the 2026-11-12 `gh-24` record). GitHub closed #29 (closed by #41); the connector: `state result=done`, `close result=merged` on the already closed item, no closing message (F-3). The PR's only red check, `audit`, is an `npm audit` advisory on `@hono/node-server`, not the change |
| A-10 | R2 front matter, configured as `AutoMerge` | `auto_merge: true`, `task_type: implementation`, `commit_type: feat` | ✅ `auto_merge: true`, `task_type: implementation`, `commit_type: feat`, `Fixes #37` |
| A-11 | R2 merge with nobody in between | worc squash-merges the pull request; GitHub closes the item; the connector labels `worc:done` | ✅ worc logged `[AUTO-MERGE] merging PR without human review`, squash-merged #42 at 17:47:26 (a comment-only change to `document-types.ts`); GitHub closed #37 through `Fixes #37` (closed by #42); the connector went `in-progress` → `done` |
| A-12 | R2's pull-request link on the item | observe whether the link comment is posted when the pull request merges before any tick saw it open — see F-2 | ❗ F-2 confirmed: the only connector comment on #37 is the queued one. The tick that first saw pull request #42 saw it merged (`pull-request found-42`, then `state result=done`), so `pr-open` was never published and the link never commented; only GitHub's own "closed by #42" event ties them |
| A-13 | Stop by sentinel | the loop exits before its next tick; `connect.pid` and `connect.stop` are gone | ✅ `stop result=sentinel` within a second, exit 0, `connect.pid` and `connect.stop` gone |

### 3B — `close_on_merge: true`, daemon

| Id | Check | Expected | Result |
| --- | --- | --- | --- |
| B-1 | P1 front matter | the title has no leading dash, `;`, backtick, `\|` or `$(`; `task_type: missing_flow` | ✅ the title ``--probe; echo \| $(id) `x` worc-connect title sanitizer check`` became `probe echo id) x worc-connect title sanitizer check`; `task_type: missing_flow`, `commit_type: feat`. `worc promote` accepted the unknown flow without a word |
| B-2 | P1's sentinel string | found in the task file and nowhere else: not in `connect.log`, not in a comment, not in `status` | ✅ `WCSENTINEL-b2229cdfdc86` only in `tasks/pending/gh-40.md`: not in `.worc-connect/` (`state.db` included), `status`, the comments, or worc's daemon log |
| B-3 | What worc does with a flow it does not know | either its gate refuses the task (`worc:failed`, the comment names the reason) or the run fails ("flow not found", `worc:failed`, the comment points at `worc status`); no agent run is spent. P1 is closed by hand afterwards | ✅ the second branch: worc's watcher took the task and failed it at once, `validated → failed`, reason `unknown task_type 'missing_flow': no flow file missing_flow.yaml …` — no branch, no agent. The item got `worc:failed` and "ended as `failed` and opened no pull request … Run `worc status gh-40`"; no worc text on the tracker. #40 closed by hand |
| B-4 | R4 with `flow:broken` | `worc:failed` the same way as B-3 | ✅ the same for `gh-39` |
| B-5 | Delete `state.db`, tick, then remove `flow:broken` and `worc` and re-apply `worc` | the re-trigger allocates `gh-<R4>.2` — never the old id — with `task_type: implementation` and a fresh branch | ✅ after the delete the rows came back as `failed` (`rebuild result=failed`); removing both labels logged `gate-withdrawn` + `retrigger armed`; re-applying `worc` staged **`gh-39.2`** with `task_type: implementation`, `commit_type: docs`, a fresh `branch_name`, `Fixes #39` |
| B-6 | Close R4's pull request unmerged | `worc:failed` + one failure comment; nothing re-queued | ❗ label right, comment wrong — F-4. `gh-39.2` opened pull request #43 (7 files: the comment, both guide pages, a test, `CHANGELOG.md`); closed unmerged at 18:02, the next tick moved #39 to `worc:failed` and commented "worc task `gh-39.2` ended as `done` and opened no pull request … Run `worc status gh-39.2`". Nothing re-queued |
| B-7 | Reopen it | back to `worc:pr-open`; no second failure comment | ✅ reopened at 18:03: back to `worc:pr-open`, no second failure comment. The connector did comment "opened a pull request: …/pull/43" a second time for the same pull request — O-6 |
| B-8 | Remove and re-apply `worc` while that pull request is open and its task has ended | `gh-<R4>.3` carries `branch_mode: existing` and `branch_ref` equal to the `.2` branch, no `branch_name`; worc appends to the same pull request, no second one | front matter ✅ `gh-39.3`: `branch_mode: existing`, `branch_ref: worc/gh-39.2-rule-aliases-the-custom-rule-collisio`, no `branch_name`; worc: "push skipped: origin already holds this branch's commit", "reusing the open pull request for this head" — the agent found the work done, no new commit, no second pull request (#43 is the only one on the branch). The item got a third "opened a pull request: …/pull/43", now naming `gh-39.3`, which reused it (O-6). worc left the clone on the task branch, as its `existing` mode does |
| B-9 | Manual merge of R4's pull request _(asked for at the time)_ | `worc:done` and a close carrying the task id and the pull-request URL; observe how it interacts with GitHub's own close through `Fixes #` — see F-3 | ✅ / ❗ merged at 18:31; GitHub closed #39 (closed by #43); the connector logged `state result=done` and `close result=merged` for `gh-39.3`, and no closing message appeared — F-3 again. The log line claims a close that did not happen |
| B-10 | R3: `bug` + `flow:skills` + `automerge` | `task_type: implementation_skills`, `commit_type: fix`, `auto_merge: true`; worc merges; the issue is closed with the pull-request URL in the closing message | ✅ / ❗ front matter as expected (`implementation_skills`, `fix`, `auto_merge: true`); `code_review` ran; worc squash-merged pull request #44 at 18:18:46 with no human; GitHub closed #38 the same second (closed by #44); the connector's tick at 18:19:41 logged `state result=done` and `close result=merged` — but the closing message never appeared: F-3. #38 carries only the queued comment, so even with `close_on_merge: true` an auto-merged item gets no link to its pull request from the connector |

## Phase 4 — real runs, triage on (`close_on_merge: true`)

T1 and T3 are labelled together, so the duplicate is judged while #32 is still open.

| Id | Check | Expected | Result |
| --- | --- | --- | --- |
| T-0 | Delete the `worc:needs-info` label from the repository before the start | recreated before the first state is published; nothing else is edited | ✅ deleted (no item carried it); recreated on the first publish of the restarted process — with a different colour from the original, as the connector's own default |
| T-1 | T1's triage task `gh-32` | `task_type: issue_triage`, `priority: high`, no `references`; comment "Queued as worc triage task". Observe `auto_merge` and `commit_type` in it — see F-1 | ✅ / ❗ `task_type: issue_triage`, `priority: high`, no `references`, comment "Queued as worc triage task `gh-32` …"; and, as F-1 predicted, **`commit_type: feat` and `auto_merge: true`** in the triage task. worc accepted the file and ran the flow; with `publishing: none` there was nothing to merge, so the flag did no harm here |
| T-2 | The report | read from `.worc-connect/triage/gh-32/report.md`, nothing under `.worc/` | ✅ `triage result=actionable` read from `.worc-connect/triage/gh-32/report.md`; nothing under `.worc/` read |
| T-3 | `actionable` → `gh-32.2` | `task_type: implementation_skills`, `auto_merge: true`, `references: ["Fixes #32"]`, the body built from the report; comment "Queued as worc implementation task" | ✅ `gh-32.2`: `task_type: implementation_skills`, `auto_merge: true`, `Fixes #32`, the description from the verdict's reason and the four acceptance criteria under `## Acceptance criteria`; no failing-test section, the report's `failing_test` being null |
| T-4 | `gh-32.2` merged by worc | `worc:done`, #32 closed | ❌ `gh-32.2` ended `manual_action_required` (`no_file_change`) at 18:40: its `lint` check failed three times on `.worc-connect/triage/gh-46/ref007-crlf.test.ts` — the reproduction file `gh-46`'s triage left in the connector's home — and its fixer could not, and should not, touch that file (F-7). The connector published `worc:failed` with "ended as `manual_action_required`" — correct for what worc reported. **Recovery:** the clone restored to `main`, the triage scratch files moved out, `worc rerun gh-32.2` → pull request #47 (one test file) auto-merged at 18:53, #32 closed by GitHub — **but the item still shows `worc:failed`** (F-8) |
| T-5 | T2 → `needs-info` | `worc:needs-info` and the question as a comment | ✅ `worc:needs-info` and the question — five concrete asks (versions, command and OS, config, a sample, before/after findings) — as the comment |
| T-6 | Ticks after the connector's own question | stay `needs-info`; the connector's label and comment are not a reply | ✅ the ticks after 18:32 logged `follow result=needs-info` and wrote nothing |
| T-7 | Reply on T2 with the textSpans detail | triage runs again as `gh-<T2>.2`; its verdict and what follows are recorded | ⚠️ the reply at 18:38 re-ran triage as **`gh-45.2`** (new id ✅) — but the task carries the issue's original body only: the reply never reaches the agent (F-6). Verdict of the re-run: none — `gh-45.2` was stopped by worc at its `reproduction` node for a change outside its report directory that was not its own: `gh-32.2`'s uncommitted diff, left in the clone and carried onto the new branch (W-1). The connector published `worc:failed` correctly |
| T-8 | T3 → `duplicate` | `worc:declined` and a comment naming #32 | ❌ verdict **`actionable`**, `duplicate_of: null` — `gh-46.2` queued with the same tests `gh-32.2` is writing. The triage nodes run with `network_access: false` and see the item's own text only, so another open item is invisible to them (F-5). `gh-46.2` was taken out of `tasks/pending/` by hand (kept in the scratchpad): the next tick logged `follow result=no-task` and published `worc:failed` with the generic comment — reasonable for a task that vanished. #46 closed by hand as a duplicate of #32 |

## Covered by the suite only

Not re-run for real, because a real run adds nothing the fake `gh` and fake `worc` do not already pin down: the label order deciding between two flow labels, a worc older than `0.14.0a1` (the handshake), a crash between staging and promoting, body and line truncation, the `.exe` / `.cmd` launcher resolution, and the recorded argv of every launch.

## Out of this run

- **A Windows run** — no Windows host.
- **The un-ignored-home half of the triage criterion** in [definition-of-done.md](definition-of-done.md). Since 2026-11-13 `watch` refuses to tick while the clone's `.gitignore` does not name the home (D-5), so the connector can no longer reach the state that criterion describes; worc's own leak refusal is covered by its AC-W4 tests. Whether to close the box on that basis is the user's call.
- **`local-research`** — not implemented.

## Findings

Suspicions from reading the code before the runs, to be confirmed or refuted by the check named; new findings are added as they turn up. The Status column records the fix for each one (branch `fix/real-run-2-findings`); none is deferred.

| Id | Finding | Where | Checked by | Status |
| --- | --- | --- | --- | --- |
| F-1 | A triage task inherits `auto_merge` (and `commit_type`) from the item's labels: `build_research` overrides only `task_type` and `priority`, while the configuration reference says the label-chosen merge applies to the implementation task. Harmless while the triage flow publishes nothing, but the triage task carries a merge flag nobody meant for it. | `core/builder.py` `build_research` | T-1 | **confirmed** (T-1), **fixed**: `build_research` drops `commit_type` and `auto_merge`, configured or label-chosen — `test_the_triage_task_carries_no_merge_and_no_commit_type_whatever_the_labels` |
| F-2 | With `close_on_merge: false`, an item whose pull request worc merges before a tick sees it open goes `in-progress` → `done` with no pull-request link: the link is commented only on the move into `pr-open`, and `done` has no comment of its own. | `core/writeback.py` `_comment_body` | A-12 | **confirmed**, **fixed**: the link is keyed on the row (`pr_announced`, schema 6) and posted on the move into `done` when `pr-open` never was — `tests/test_pull_request_comments.py` |
| F-3 | With `close_on_merge: true` and `Fixes #<n>` in the pull request, GitHub and the connector both close the item; what `gh issue close` does on an already closed issue — and whether the closing message is posted — has not been observed. **Observed:** GitHub closes the item at the merge itself (the closing keyword fires on merge), a tick later the connector runs `gh issue close <n> --comment <message>` on an item already closed, and `gh` reports it already closed, exits 0 and **skips the comment**. So whenever `references` is emitted (worc ≥ 0.14.0a1) the closing message is dead code, and with auto-merge (F-2) the item never gets the pull-request link under either `close_on_merge` setting. A fix for both: on the move into `done`, comment the pull-request link through the body file when it was never published, and close only an item that is still open — the log line `close result=merged` is then also true, which today it is not (A-9, B-9). | `core/writeback.py` `_announce`, `trackers/github/adapter.py` `close` | B-10, A-9, B-9 | **confirmed**, **fixed**: the closing message is a body-file comment posted first, `gh issue close` carries no text and runs only on an item still open, the log says `close result=already-closed` otherwise — `tests/test_pull_request_comments.py`, `test_closing_carries_no_text_of_its_own` |
| F-4 | A pull request closed without a merge is reported as if there never was one: "worc task `gh-39.2` ended as `done` and opened no pull request". The failure body has no case for a closed pull request — with no triage note and no validation reason it renders worc's status (`done`, which is true of the task) and the no-pull-request sentence. The comment should name the pull request and say it was closed unmerged (AC-E4). | `core/writeback.py` `_failure_body` | B-6 | **confirmed**, **fixed**: `_failure_body` names the pull request closed unmerged and how to resume — `test_a_request_closed_unmerged_is_named_in_the_failure` |
| F-5 | Triage cannot recognise a duplicate of another open item: every `issue_triage` node runs with `network_access: false`, and the task carries the item's own text only. `duplicate` is reachable only against what is already in the repository; a fresh duplicate of an open issue comes back `actionable` and costs a second implementation run. Either the connector hands the triage task the open items' numbers and titles (still untrusted text, but into the task body, the one place it may go) or the verdict's promise is narrowed in the docs. | `core/builder.py` `build_research`; the shipped `issue_triage` flow | T-8 | **confirmed**, **fixed** (user's choice: show the open items): the triage task lists the 200 most recent open issues by number and sanitized title, and the `analysis` / `report` prompts use the list for `duplicate` — `tests/test_triage_context.py` |
| F-6 | A `needs-info` reply re-runs triage, but the reply never reaches it: the adapter reads `number,title,body,author,labels,updatedAt,url` and no comments, so the re-run gets the same thin body the first run asked about. The connector's own comment ("Reply on this issue and triage will run again") and the triage guide both promise more. Today only an edit of the issue's body carries new facts. | `trackers/github/adapter.py` `_ISSUE_FIELDS`; `docs/triage.md` | T-7 | **confirmed**, **fixed** (user's choice: pass the reply): the next triage task carries the comments posted after the question's own stamp, so the connector's question is never read back — `tests/test_triage_context.py` |
| F-7 | The triage report directory lives inside the clone, and the reproduction node writes real source files there — `ref007-crlf.test.ts`, `vitest.config.mjs`, a `node_modules/.vite` cache (`gh-31` and `gh-46` both did). Tools that do not read `.gitignore` — ESLint's flat config among them — lint them as part of the project, so one lint error in a triage scratch file fails the `lint` check of **every later task**: `gh-32.2` failed that way, and its fixer, rightly, would not edit the connector's home. Options: the connector removes everything but `report.md` from `triage/<task-id>/` once it has read the report; the shipped flow asks the reproduction node to keep its test in the report rather than as a file; and the getting-started guide tells the operator to add `.worc-connect/` to the project's lint, test and format ignores, as `.worc/` already is in this repository. | `core/triage.py`; the shipped `issue_triage` flow; `docs/getting-started.md` | T-4 | **confirmed**, **fixed** (all three measures): the connector clears `triage/<task-id>/` but `report.md` once the task is over, the reproduction prompt asks the node to leave nothing, and getting-started tells the operator to add `.worc-connect/` to the project's tool ignores — the one measure with no race; `install-flow` now updates an unedited copy of an earlier release without `--force` — `tests/test_triage_scratch.py` |
| F-8 | The recovery the docs prescribe for `worc:failed` — `worc rerun <task-id>` — is invisible to the connector. A `failed` row is in no followed phase (`_FOLLOWED_PHASES` is `queued`, `running`) and does not look for a pull request (`needs_discovery` wants a `done` status), so while `gh-32.2` re-ran the log kept printing `follow result=failed worc="manual_action_required"` — a cached status presented as worc's — and when its pull request #47 merged and GitHub closed #32, the item kept `worc:failed`. A closed item leaves the open-items listing, so nothing will ever correct it. A `failed` row whose task worc reports running again should be followed again; the operations guide and the README promise exactly that path. | `core/reconcile.py` `_follow_task`; `core/pullrequest.py` `needs_discovery`; `docs/operations.md` | T-4 recovery | **confirmed**, **fixed**: a `failed` / `manual_action_required` row with no pull request is watched for a rerun for seven days (one `worc list` per tick, the item read only once worc takes the task back, a closed item included), and the log quotes worc only for what worc said that tick — `tests/test_rerun.py`; the window is recorded as FU-2 in `docs/backlog/follow-ups.md` |
| O-6 | Reopening a closed pull request re-posts "opened a pull request: <url>" for the same pull request — every move into `pr-open` comments, even back into it. Noise rather than harm; the same sentence for a follow-up that only reused the pull request reads as if it opened another. | `core/writeback.py` `_comment_body` | B-7, B-8 | **confirmed**, **fixed**: one announcement per attempt (`pr_announced`); a follow-up says it continued the request (`continued_pr`) — `tests/test_pull_request_comments.py` |
| O-1 | `status` on a cache of an older schema reports "no items taken on yet" while the file holds rows; the next real tick rebuilds it, but the message reads as "nothing ever happened". | `cli.py` `_cmd_status` | P0-8 | **confirmed**, **fixed**: `status` names the other schema and says the next real tick rebuilds the cache — `test_status_on_a_cache_of_another_schema_says_so_instead_of_reporting_nothing` |
| O-2 | The dry-run plan line shows the task id, branch and label but not the chosen flow or `auto_merge`, so the per-item choices cannot be previewed before a real tick. | `cli.py` `_reporter` | D-3 | **confirmed**, **fixed**: the plan line carries `task_type=` and `auto_merge=` — `test_a_dry_run_shows_the_flow_and_the_merge_each_item_would_get` |
| O-3 | `auto_merge_labels` accepts the trigger label itself (`[WORC]` with `gate.labels: [worc]`): every gated item is then merged with nobody in between — `auto_merge: true` for all, hidden behind a per-item key. The load check refuses only labels under `labels_prefix`. Refusing a label that is also in `gate.labels`, or saying so in the configuration reference, would close it. | `config.py` `_refuse_state_labels` | C-15 | **confirmed**, **fixed** (user's choice: refuse): a `gate.labels` label in `auto_merge_labels` or `task_type_by_label` is refused at load — `test_a_trigger_label_is_refused_as_a_per_item_choice` |
| O-4 | The listing always goes through GitHub's search (`--search sort:updated-asc`, watermark or not), and the search index lags: an item created or labelled seconds earlier can be missing. A real tick recovers on its own — the next listing reaches 10 minutes behind the watermark — but a dry run straight after labelling shows the item as absent, which reads as "the gate did not admit it". Worth a line in the getting-started guide. | `trackers/github/adapter.py` `list_items` | D-1 | **confirmed**, **documented** in the getting-started guide (Step 6) |
| O-5 | "The first matching label wins, in the order the item lists its labels": on GitHub that order is the order the labels were **created in the repository**, not the order they were applied (#33 labelled `flow:skills`, `automerge`, `bug` in that order lists `bug, enhancement, automerge, flow:skills`). An operator cannot choose between two flow labels by applying one first; the configuration reference should say so. | `docs/configuration.md` | label probe on #33 | **confirmed**, **documented** in the configuration reference (choosing the flow per item) |

### Worc-side, for the orchestrator's backlog

- **W-1** — a task stopped at `manual_action_required` (`gh-32.2`, `cleanup_safe=true`) left its uncommitted diff in the shared clone. The next task's branch preparation (`gh-45.2`, `branch_mode: new`) did not refuse the dirty tree, carried the diff onto its new branch, and the after-stage guard of its `reproduction` node then blamed that node for a change it never made. The clone is left on `worc/gh-45.2-…` with `gh-32.2`'s diff uncommitted.
- **W-2** — `stuck.md` for `gh-32.2` says "Last failing check output: (none)" although `checks/003.log`, `010.log` and `017.log` hold the failing `eslint` output; the operator has to dig for the reason.

## Run log

- 2026-11-18 — plan written. Phase 0 and phase 1 run: everything as expected; O-1 confirmed, O-3 found (C-15).
- 2026-11-18 — reasoning lowered in the three flows (local, skip-worktree); labels `automerge`, `flow:skills`, `flow:broken` and issues #37–#40 created. Phase 2 run: O-2 confirmed, O-4 and O-5 found. 17:37 local — phase 3A daemon started (pid 14202): `gh-29` and `gh-37` staged, promoted, `worc:queued` and the queued comment on both. Both stayed `pending`: the worc daemon had exited at 16:58; restarted at 17:39 with a 60 s poll, it took `gh-29` at once, the `planning` agent launched with `--effort low`. 17:41 — daemon stopped by sentinel, `state.db` deleted, daemon restarted (A-8, A-13).
- 2026-11-18 17:42 — `gh-29` → pull request #41 (docs only, `Fixes #29`), `worc:pr-open` with the link; waiting for the operator's merge (A-9). 17:47 — `gh-37` → pull request #42 auto-merged by worc, #37 closed by GitHub, `worc:done`, no link comment (F-2). 17:49 — #39 and #40 labelled with `flow:broken` under the 3A configuration: the unknown-flow and sanitizer checks do not depend on `close_on_merge`.
- 2026-11-18 17:49–17:55 — `gh-39` and `gh-40` failed at worc's pickup (unknown flow), `worc:failed` with the `worc status` comment; #40 closed. Daemon stopped, `state.db` deleted, restarted; #39 re-triggered without `flow:broken` → `gh-39.2` queued.
- 2026-11-18 18:02–18:06 — `gh-39.2` → pull request #43; closed unmerged → `worc:failed` with a wrong comment (F-4); reopened → `worc:pr-open` (O-6); trigger cycled → follow-up `gh-39.3` on the open branch queued.
- 2026-11-18 18:10 — `gh-39.3` done, pull request #43 reused. 18:13 — daemon restarted with `close_on_merge: true` (phase 3B proper) while the two manual merges wait for the operator; the `close_on_merge: false` manual-merge path was already recorded on 2026-11-12 (`gh-24`), so A-9 may run under `true`. 18:14 — #38 labelled `worc`: `gh-38` queued with `implementation_skills`, `fix`, `auto_merge: true`.
- 2026-11-18 18:19 — `gh-38` → pull request #44 auto-merged by worc; #38 closed by GitHub, `worc:done`; the connector's close was a no-op and its closing message was lost (F-3).
- 2026-11-18 18:22 — phase 4: `worc:needs-info` deleted, #45 (thin report) and #46 (duplicate of #32) created, `research.mode: "worc"`, daemon restarted. #32 (`worc`, `flow:skills`, `automerge`), #45 and #46 labelled; triage tasks `gh-32`, `gh-46`, `gh-45` queued. `gh-32`'s file carries `auto_merge: true` and `commit_type: feat` (F-1 live).
- 2026-11-18 18:31 — the operator approved both manual merges: pull requests #41 and #43 merged; #29 and #39 closed by GitHub, `worc:done`; both closing messages lost (F-3).
- 2026-11-18 18:28–18:39 — `gh-32` → `actionable` → `gh-32.2` (`implementation_skills`, `auto_merge: true`); `gh-45` → `needs-info` with a good question; `gh-46` (duplicate) → `actionable` → `gh-46.2`, taken out of the queue by hand, #46 closed. Replied on #45 → `gh-45.2` re-triage queued with the old body only (F-6).
- 2026-11-18 18:40–18:44 — `gh-32.2` → `manual_action_required` (`lint` failing on a triage scratch file, F-7), its diff left in the clone; `gh-45.2` (re-triage) → `manual_action_required` on that diff (W-1). Both items `worc:failed`. The leftover diff equals `.worc/logs/gh-32.2/current.diff` and is also saved in the session scratchpad.
- 2026-11-18 18:46–18:56 — recovery: clone back on `main`, triage scratch files moved out (`eslint .` green again), `worc watch` stopped, `worc rerun gh-32.2` → pull request #47 auto-merged, #32 closed by GitHub and left at `worc:failed` (F-8). Clean-up: connector stopped, its configuration restored, `--no-skip-worktree` and `git checkout` on the three flows, every flow valid, the clone clean on `main`.
