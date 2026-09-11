# Configuration reference

The connector reads one file, `.worc-connect/config.yaml`, written by `worc-connect init` and edited by the operator. This page is the reference for every key in it; the quickstart is in [README.md](../README.md).

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
  queue: default
  priority: mid
  commit_type: feat
  # commit_type_by_label: { bug: fix, documentation: docs }
  branch_prefix: worc
  id_prefix: gh
  # auto_merge: false

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

triage:
  enabled: false
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
| `queue` | absent | worc's queue. |
| `priority` | absent | worc's priority. |
| `commit_type` | absent | The conventional-commit type for the task. |
| `commit_type_by_label` | `{}` | Per-label override of `commit_type`, e.g. `{ bug: fix }`. |
| `branch_prefix` | `worc` | First segment of the branch the connector names. |
| `id_prefix` | `gh` | First segment of the task id (`gh-142`). Validated at load against worc's id grammar, including the trailing-dot and Windows device-name rules, so a prefix that could not build a legal id is refused before any task exists. |
| `auto_merge` | absent | **Left unset by `init` on purpose.** An issue-sourced task under `auto_merge: true` is where a prompt injection in a stranger's issue turns into merged code with nobody in between. worc's own policy and your review of the pull request are what should decide. |

## `worc` — how the connector reaches the orchestrator

| Key | Default | Meaning |
| --- | --- | --- |
| `command` | `worc` | The launcher to resolve on `PATH`. Resolution handles a Windows `.exe` / `.cmd`, and the command is always launched as an argument list, never through a shell. |
| `repo_path` | `.` | The clone worc runs in, relative to the directory that holds the connector's home. Refused if it is not a directory. |
| `tasks_dir` | `tasks` | worc's own `paths.tasks_dir`. The connector stages the task file in `<tasks_dir>/preparing/` and reads `<tasks_dir>/pending/` to tell a promoted task from a vanished one. Must be a directory inside the clone, written with forward slashes and no `..` segment. |
| `max_task_bytes` | `262144` | worc's `validation.max_task_bytes`. The generated file is truncated to fit, with a visible marker and the item's URL. |
| `max_task_lines` | `5000` | worc's `validation.max_task_lines`, applied the same way. |
| `max_line_bytes` | `8192` | worc's `validation.max_line_bytes`, applied per line. |

The connector's only write into that clone is a task file in `<tasks_dir>/preparing/`, promoted with `worc promote`; its only read out of worc is `worc list --format json --all`. It never runs `git` there and never touches `.worc/` — **including worc's own `config.yaml`**, which is why the last four keys exist. If you changed `paths.tasks_dir` or any `validation.max_*` in worc, restate the value here: the connector cannot see it, and the mismatch would surface as a task worc never queues or a file its gate quarantines.

## The task file the connector generates

```markdown
---
id: gh-142
title: "Signup form accepts foo@ as an email"
branch_name: worc/gh-142-signup-form-accepts-foo-as-an-email
priority: mid
queue: default
commit_type: fix
---

## Description

Source: github item #142 by @reporter — https://github.com/OWNER/REPO/issues/142

<the item's text, verbatim, truncated with a marker if it is over one of the limits above>
```

- **The id** is `<id_prefix>-<item number>`, and `<id_prefix>-<item number>.<n>` for every later attempt at the same item. An id is never reused.
- **The title** is the item's, with whitespace collapsed and every control character, newline, `;`, backtick, `|` and `$(` removed, capped at 120 characters, and with every leading `-` stripped — worc's front-matter scan refuses all of those, and a refusal quarantines the task inside `.worc/`, where the connector may not look. An item whose title survives none of that is titled `Issue #<n>`.
- **The branch** is `<branch_prefix>/<task id>-<slug>`, at most 50 characters: above that worc discards the name and generates its own, which the connector could then not find the pull request by. It always carries the `<branch_prefix>/` segment, so it can never collide with a base branch.
- **The body** is the item's text, verbatim, under one provenance line. No acceptance criteria are invented for an item that carries none — enriching a thin report is worc's refinement step, not the connector's guess.
- **A follow-up on an item whose previous pull request is still open** carries `branch_mode: existing` and `branch_ref` instead of `branch_name`, so worc continues that branch and appends to that pull request.
- **Nothing else is emitted.** The key set is a strict subset of worc's allowed keys and can never include `nodes`, `subtasks`, `decomposition`, `trust_level`, `prompt_audit` or `publish`: a task file the connector writes cannot change how worc runs it.

## `write_back` — what appears on the item

| Key | Default | Meaning |
| --- | --- | --- |
| `labels_prefix` | `worc:` | Prefix of the connector-owned state labels (`worc:queued`, `worc:in-progress`, `worc:pr-open`, `worc:done`, `worc:failed`). Exactly one is present at a time. |
| `comment` | `true` | Whether to comment when the task is queued, when its pull request exists, and when it ends without one. |
| `close_on_merge` | `true` | Whether to close the item when its pull request is merged. |

Comment bodies are connector-authored templates carrying the task id, a status name and URLs — never the item's own text, never a log line, never a diff.

## `triage` — the optional analysis path

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | When true, a gated item first becomes a worc _triage_ task and the same task builder then runs on its report. |
| `flow` | `issue_triage` | The flow the triage task names. Installed into worc's `.worc/flows/` by an explicit `worc-connect install-flow`, and only while the switch is on. |

## The connector's home

Everything the connector owns lives in `.worc-connect/` next to worc's own `.worc/`, never inside it:

| Path | What it is |
| --- | --- |
| `config.yaml` | This file. |
| `state.db` | The SQLite cache of one row per item plus the poll watermark. **Disposable**: every tick re-derives the truth from the tracker, `worc list` and the lifecycle folders, so deleting it costs one tick. |
| `connect.log` | One line per action: `item=… task=… action=… result=…`. Ids and URLs are the only item-derived values it carries. |
| `connect.pid` | Written while a `watch` daemon runs, removed when it exits cleanly. A second `watch` in the same clone refuses to start while it exists; after a crash, delete it. |
| `connect.stop` | The stop sentinel. Create the file and the loop exits before its next tick and removes it. A file rather than a signal, so Windows behaves like POSIX. |
| `triage/<task-id>/` | Where a triage flow leaves its report, in a directory the connector owns rather than under `.worc/`. |

`worc-connect init` appends `.worc-connect/` to the clone's tracked `.gitignore`, so nothing here can reach a commit.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The run completed. |
| `1` | An infrastructure failure (the tracker was unreachable during a single pass) or a refused start (another watcher holds the PID file, a configuration already exists). |
| `2` | A usage or configuration problem. Nothing was written and no tracker call was made. |
