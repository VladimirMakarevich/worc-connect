# Configuration reference

The connector reads one file, `.worc-connect/config.yaml`, written by `worc-connect init` and edited by the operator. This page is the reference for every key in it; the quickstart is in the repository README.

The loader **rejects rather than repairs**. A key this build does not declare, a value of the wrong type, or a gate that admits nothing stops the process with exit code 2 and a message naming the key. That strictness is deliberate: a mistyped `authors:` would otherwise silently widen the gate from "these people" to "anyone who can apply the label", and the gate is the only thing between a stranger's issue text and an agent with write access to the repository.

## The file

```yaml
schema_version: 1

tracker: github
repo: OWNER/REPO
poll_interval_seconds: 300

gate:
  labels: [worc]
  authors: []
  allow_all: false

task:
  task_type: implementation
  # task_type_by_label: { "flow:quick": quick_fix }
  queue: default
  priority: mid
  commit_type: feat
  # commit_type_by_label: { bug: fix, documentation: docs }
  branch_prefix: worc
  id_prefix: gh
  # auto_merge: false
  # auto_merge_labels: [automerge]

worc:
  command: worc
  repo_path: .
  tasks_dir: tasks
  max_task_bytes: 262144
  max_task_lines: 5000
  max_line_bytes: 8192

write_back:
  labels_prefix: "worc:"
  comment: true
  close_on_merge: true

research:
  mode: "off" # "off" | "worc"
  flow: issue_triage
```

## Top level

| Key | Default | Meaning |
| --- | --- | --- |
| `schema_version` | `1` | The schema this file is written against. A version this build does not understand is refused rather than read with today's meaning. |
| `tracker` | — (required) | The adapter to use, resolved by name in the `worc_connect.trackers` entry-point group. A name with no adapter installed is refused with the extra to install. |
| `repo` | — (required) | The one repository to watch, as `OWNER/REPO`. Pinned onto every tracker call. |
| `poll_interval_seconds` | `300` | How long the loop waits between ticks. The stop sentinel is checked about once a second during that wait, so a stop does not have to wait out the interval. |

## `gate` — the perimeter

Only an item an explicit rule admits ever becomes a task. Both rules are **filters**, applied in sequence: with both set, an item needs a trigger label _and_ an author on the list.

| Key | Default | Meaning |
| --- | --- | --- |
| `labels` | `[worc]` | Trigger labels. An item needs at least one; matching is case-insensitive. |
| `authors` | `[]` | Optional author allow-list. Empty means the labels alone decide; non-empty narrows the gate. Matching is case-insensitive, and an author the tracker cannot name is never admitted. |
| `allow_all` | `false` | Accept every open item of the repository. Must be stated in so many words. |

With `labels: []`, `authors: []` and `allow_all: false` the connector **refuses to start**: "nothing configured" must never read as "every item".

A trigger label removed after the item was taken on does not withdraw anything — the task is worc's from the moment it was promoted — and the withdrawal is logged.

## `task` — the dispatch fields of the generated task

A key left out here is a key the generated task file does not carry, so worc's own default stands instead of the connector's opinion of it.

| Key | Default | Meaning |
| --- | --- | --- |
| `task_type` | absent | worc's task type / flow name. |
| `task_type_by_label` | `{}` | Per-label override of `task_type`, e.g. `{ "flow:quick": quick_fix }`: a maintainer picks the flow of one item by labelling it. |
| `queue` | absent | worc's queue. |
| `priority` | absent | worc's priority. |
| `commit_type` | absent | The conventional-commit type for the task. |
| `commit_type_by_label` | `{}` | Per-label override of `commit_type`, e.g. `{ bug: fix }`. |
| `branch_prefix` | `worc` | First segment of the branch the connector names. |
| `id_prefix` | `gh` | First segment of the task id (`gh-142`). Validated at load against worc's id grammar, including the trailing-dot and Windows device-name rules, so a prefix that could not build a legal id is refused before any task exists. |
| `auto_merge` | absent | **Left unset by `init` on purpose.** An issue-sourced task under `auto_merge: true` is where a prompt injection in a stranger's issue turns into merged code with nobody in between. worc's own policy and your review of the pull request are what should decide. |
| `auto_merge_labels` | `[]` | Per-item opt-in: a task whose item carries one of these labels gets `auto_merge: true`, whatever `auto_merge` says. Read the caveat below before setting it. |

### Choosing the flow and the merge per item

The three `*_by_label` / `*_labels` keys let a maintainer decide, per issue, among values you wrote here. A label only **selects** a value from this file; its own text never reaches the task, so nothing an issue's author can spell ends up in the front matter.

- **The first matching label wins**, in the order the item lists its labels, not the order of the mapping. Matching is case-insensitive. An item with no matching label gets the plain `task_type` / `commit_type` / `auto_merge`, or no key at all when that is absent, so worc's own default stands.
- **These labels do not gate anything.** An item still needs a trigger label from `gate.labels` (or a listed author) to become a task; `flow:quick` alone does nothing.
- **They are read when the task is built.** Changing a label while a task runs changes nothing for that task; a re-trigger builds the next one from the labels the item carries then.
- **A label under `write_back.labels_prefix` is refused at load.** The connector adds and removes those labels itself, and a re-triggered item still shows the last attempt's state, so `auto_merge_labels: ["worc:failed"]` would let the connector's own write-back opt the next task into a merge.
- **The connector does not create these labels.** Create them once on the repository, e.g. `gh label create automerge --repo OWNER/REPO`.
- **With the triage step on**, the triage task keeps its own flow; the label-chosen flow and merge apply to the implementation task built from the report.
- **A flow name must be one worc knows** — a file `.worc/flows/<name>.yaml` in the clone. The connector never reads worc's flow directory, so it cannot check the name: a typo passes worc's task gate and fails when worc runs the task ("flow not found"), which the item shows as `worc:failed`.

**The `auto_merge_labels` caveat.** The label is the whole of the protection, so it is only as strong as the rule for who can apply it. On GitHub only people with triage access or above can label an issue — but an issue template or issue form whose `labels:` lists the auto-merge label applies it for anyone who opens an issue from it, and so does any workflow or bot that labels new issues. Check `.github/ISSUE_TEMPLATE/` and your automation before you set this key, and keep the label off every template.

## `worc` — how the connector reaches the orchestrator

| Key | Default | Meaning |
| --- | --- | --- |
| `command` | `worc` | The launcher to resolve on `PATH`. Resolution handles a Windows `.exe` / `.cmd`, and the command is always launched as an argument list, never through a shell. It is asked `--version` once per run — see below. |
| `repo_path` | `.` | The clone worc runs in, relative to the directory that holds the connector's home. Refused if it is not a directory. |
| `tasks_dir` | `tasks` | worc's own `paths.tasks_dir`. The connector stages the task file in `<tasks_dir>/preparing/` and reads `<tasks_dir>/pending/` to tell a promoted task from a vanished one. Must be a directory inside the clone, written with forward slashes and no `..` segment. |
| `max_task_bytes` | `262144` | worc's `validation.max_task_bytes`. The generated file is truncated to fit, with a visible marker and the item's URL. |
| `max_task_lines` | `5000` | worc's `validation.max_task_lines`, applied the same way. |
| `max_line_bytes` | `8192` | worc's `validation.max_line_bytes`, applied per line. |

The connector's only write into that clone is a task file in `<tasks_dir>/preparing/`, promoted with `worc promote`; its only reads out of worc are `worc list --format json --all` and `worc --version`. It never runs `git` there and never touches `.worc/` — **including worc's own `config.yaml`**, which is why the last four keys exist. If you changed `paths.tasks_dir` or any `validation.max_*` in worc, restate the value here: the connector cannot see it, and the mismatch would surface as a task worc never queues or a file its gate quarantines.

### Which worc you are running

The connector asks `worc --version` once per run, before it writes its first task file, and adapts to the answer. **0.14.0a1** is the release that ships the contract it uses:

| What it uses | With 0.14.0a1 or newer | With an older worc |
| --- | --- | --- |
| `references:` in the task | The task carries `Fixes #<n>`, which worc appends to the pull-request body, so the code host can close the item itself | The key is not emitted at all — an unknown front-matter key is a hard reject at worc's gate, not a warning |
| `pr_url` in the JSON listing | The pull request is read by the number in the URL worc recorded, so it is found even after a squash merge deleted the head branch | The pull request is searched for by the branch the connector named |
| the `rejected` section of `--all` | A task the validation gate refused is reported on the item with the reason worc recorded | The refusal is reported without a reason, pointing at `worc status <task-id>` on the host |

The handshake **fails closed**: a worc that cannot be launched, refuses to say what it is, or reports a version this build does not recognise is treated as an older one. You lose the three conveniences above and nothing else.

## The task file the connector generates

```markdown
---
id: gh-142
title: "Signup form accepts foo@ as an email"
branch_name: worc/gh-142-signup-form-accepts-foo-as-an-email
priority: mid
queue: default
commit_type: fix
references:
  - "Fixes #142"
---

## Description

Source: github item #142 by @reporter — https://github.com/OWNER/REPO/issues/142

<the item's text, verbatim, truncated with a marker if it is over one of the limits above>
```

- **The id** is `<id_prefix>-<item number>`, and `<id_prefix>-<item number>.<n>` for every later attempt at the same item. An id is never reused.
- **The title** is the item's, with whitespace collapsed and every control character, newline, `;`, backtick, `|` and `$(` removed, capped at 120 characters, and with the whole leading run of dashes stripped, whitespace inside the run included — worc's front-matter scan refuses all of those, and a refusal quarantines the task inside `.worc/`, where the connector may not look. An item whose title survives none of that is titled `Issue #<n>`.
- **The branch** is `<branch_prefix>/<task id>-<slug>`, at most 50 characters: above that worc discards the name and generates its own, which the connector could then not find the pull request by. It always carries the `<branch_prefix>/` segment, so it can never collide with a base branch.
- **The body** is the item's text, verbatim, under one provenance line. No acceptance criteria are invented for an item that carries none — enriching a thin report is worc's refinement step, not the connector's guess.
- **A follow-up on an item whose previous pull request is still open** carries `branch_mode: existing` and `branch_ref` instead of `branch_name`, so worc continues that branch and appends to that pull request.
- **`references`** carries the tracker's own closing keyword (`Fixes #142` for GitHub) when the installed worc accepts the key. worc appends the lines to the pull-request body verbatim and interprets none of them; the keyword is the tracker adapter's knowledge, which is why a tracker that has none produces a task without the key.
- **Nothing else is emitted.** The key set is a strict subset of worc's allowed keys and can never include `nodes`, `subtasks`, `decomposition`, `trust_level`, `prompt_audit` or `publish`: a task file the connector writes cannot change how worc runs it.

## `write_back` — what appears on the item

| Key | Default | Meaning |
| --- | --- | --- |
| `labels_prefix` | `worc:` | Prefix of the connector-owned state labels (`worc:queued`, `worc:in-progress`, `worc:pr-open`, `worc:done`, `worc:failed`, and with triage on `worc:needs-info` / `worc:declined`). Exactly one is present at a time, and re-applying the one the item already shows does nothing. The connector creates the ones the repository lacks — and the `gate.labels` trigger labels — on the first tick of a process; the two triage labels only when `research.mode` is `worc`. |
| `comment` | `true` | Whether to comment when the task is queued, when its pull request exists, and when it ends without one. The closing message on a merge is not a comment and is not switched off by this. |
| `close_on_merge` | `true` | Whether to close the item when its pull request is merged. With it off the item keeps its `worc:done` label, and **GitHub closes it instead**: the generated task carries `references: ["Fixes #<n>"]`, which worc appends to the pull-request body, so merging into the default branch closes the issue with the code host's own link between the two. Against a worc older than 0.14.0a1 there is no such key, and the item then simply stays open for you to close. |

The item is the visible state machine, and the connector reads it back before every write: the state it shows decides whether anything is written at all. That is what makes a deleted `state.db` safe — the label says how far the item got — and what makes a tick with nothing new write nothing.

The labels are **created on demand**: the connector asks the tracker which of them exist just before it publishes the first state of a run, creates the missing ones, and never edits one that is already there. `worc-connect init` does not create them, because `init` is offline and writes nothing but the connector's own home.

Comment bodies are connector-authored templates carrying the task id, a status name and URLs — never the item's own text, never a log line, never a diff — and they reach the tracker as **files**, never as command arguments. The closing message on a merge is the one body that travels as an argument, because `gh issue close` offers no file form; it is a template too, carrying only the task id and the pull-request URL.

## `research` — the optional analysis step

Off by default. With it on, a gated item becomes a worc _triage_ task first and the **same** task builder then produces the implementation task from that task's report — so what an implementation task looks like never depends on which path it took.

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `"off"` | `"off"` sends a gated item straight to an implementation task. `"worc"` queues a triage task into worc first — one extra agent run per item, inside worc's own sandbox. **Quote the value**: YAML reads a bare `off` as the boolean false. |
| `flow` | `issue_triage` | The flow a triage task names, resolved by worc as `.worc/flows/<flow>.yaml`. `worc-connect install-flow` ships `issue_triage`; naming anything else means the flow is yours to write and install. |

`mode: "local"` is a value of the vocabulary that this build does not implement, and the loader refuses it by name rather than falling back to `worc` — an operator who asked for local research and got a worc queue slot would be paying for exactly what they avoided.

### What the triage task produces

The report lands at `.worc-connect/triage/<task-id>/report.md` — the flow's declared `report_dir`, inside the connector's own gitignored home, never under `.worc/`. The connector reads one machine-readable block at the end of it and acts on the verdict:

| Verdict | What happens |
| --- | --- |
| `actionable` | The implementation task is built from the report: its reason as the description, its acceptance criteria under `## Acceptance criteria`, and its failing test under `## Failing test`. The task gets the next id (`gh-142` triaged → `gh-142.2` implemented), so no id is ever reused. |
| `needs-info` | The item is labelled `worc:needs-info` and the report's question is commented. When the reporter replies — the item's update stamp moves past the connector's own question and the gate still admits it — triage runs again with the next id. The connector's own label and comment never count as the reply. |
| `duplicate` | The item is labelled `worc:declined`; the comment names what it duplicates. |
| `declined` | The item is labelled `worc:declined`; the comment says why. |

A report that is **missing**, that carries no verdict block, or whose verdict is not one of those four ends the attempt at `worc:failed` with a comment naming the path the connector looked at. There is no second location to look in.

### Installing the flow

```bash
worc-connect install-flow          # only with `research.mode: worc`
worc-connect install-flow --force  # replace a copy you have edited
```

The command writes the shipped flow and its five role prompts into `.worc/flows/` — the only thing the connector ever writes inside worc's home, and only on this explicit command. It refuses before writing anything when the step is off, when `research.flow` names a flow this package does not ship, or when the worc in the clone predates `flow.report_dir` and would refuse the flow at load. A file identical to the shipped one is rewritten silently; a file you have edited is left alone and reported, so re-running the command is a safe way to pick up a newer flow.

The flow itself can weaken nothing: it declares `publishing: none`, grants no network, keeps every node but the reproduction one read-only, and confines that one node's writes to the report directory. worc's validator and sandbox enforce all of it.

## The connector's home

Everything the connector owns lives in `.worc-connect/` next to worc's own `.worc/`, never inside it:

| Path | What it is |
| --- | --- |
| `config.yaml` | This file. |
| `state.db` | The SQLite cache of one row per item plus the poll watermark. **Disposable**: every tick re-derives the truth from the tracker, `worc list` and the lifecycle folders, so deleting it costs one tick. |
| `connect.log` | One line per action: `item=… task=… action=… result=…`. Ids and URLs are the only item-derived values it carries. Rotated by size — 5 MB per file, three older files kept — so a daemon on a busy repository never grows it without end. |
| `connect.pid` | Written while a `watch` daemon runs, removed when it exits cleanly. A second `watch` in the same clone refuses to start while it exists; after a crash, delete it. |
| `connect.stop` | The stop sentinel. Create the file and the loop exits before its next tick and removes it. A file rather than a signal, so Windows behaves like POSIX. |
| `triage/<task-id>/report.md` | Where the triage flow leaves its report, in a directory the connector owns rather than under `.worc/`. Read by the connector, written by worc, and gitignored — worc refuses to publish a private report that git can see. |

`worc-connect init` appends `.worc-connect/` to the clone's tracked `.gitignore`, so nothing here can reach a commit.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The run completed. |
| `1` | An infrastructure failure (the tracker was unreachable during a single pass) or a refused start (another watcher holds the PID file, a configuration already exists). |
| `2` | A usage or configuration problem. Nothing was written and no tracker call was made. |
