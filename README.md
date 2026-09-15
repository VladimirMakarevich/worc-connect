# worc-connect

**Labelled issue in → worc task out → status back on the issue.**

`worc-connect` is the tracker connector for [wastech-orchestrator](https://github.com/VladimirMakarevich/wastech-orchestrator) (`worc`). It runs beside `worc watch` in the same clone, polls an issue tracker for one repository, turns each work item a maintainer has explicitly gated into a worc task through the ingress worc already has (`tasks/preparing/` + `worc promote`), and writes the outcome back to the tracker: a state label, a comment naming the task, the pull-request link, and the close on merge. GitHub is the first tracker, driven through the operator's own `gh` login.

## Why it exists

worc runs tasks; it does not watch your issue tracker. Without a connector the human in the middle copies an issue into a task file by hand, remembers which task belongs to which issue, and goes back to the issue to say what happened. That is the work this does — and nothing else:

- **A maintainer's label is the only trigger.** Nothing an outsider writes starts a run; the gate fails closed and a configuration with no rule refuses to start.
- **The issue is the status page.** Whoever reported it sees `worc:queued` → `worc:in-progress` → `worc:pr-open` → `worc:done` without being told, and the pull request is linked in a comment.
- **It adds no second brain.** The polling loop makes no model call and holds no credential: worc runs the agent inside its own sandbox, `gh` runs as your own login, and the connector's database is a cache you may delete.

**Status: the loop is complete, and the first real run is the operator's.** Every path is covered against a fake `gh` and a fake `worc`; **the end-to-end run against a real repository has not been recorded yet** — it needs your own `gh` login and a `worc watch` beside the connector. `worc-connect watch --once --dry-run` is the safe first command and writes nothing at all.

## What it does, in four steps

1. A maintainer adds the trigger label (default `worc`) to an issue. Nothing else.
2. Within one poll interval (default five minutes) the connector allocates a task id (`gh-142`) and a branch name, writes `tasks/preparing/gh-142.md` — the issue text, verbatim, under a provenance line — and runs `worc promote gh-142`. The issue gets `worc:queued` and a comment naming the task.
3. `worc watch` runs the task to a pull request as it does any other; the connector follows it through `worc list --format json`, moves the label, and comments the PR link.
4. When the PR is merged — by anyone, any way — the issue is closed: by GitHub itself, from the `Fixes #142` the connector put in the task's `references`, or by the connector, which is the default.

Triage by an agent (analyse, reproduce, write a failing test) is an **optional** step, off by default — see the [triage guide](docs/triage.md).

## Requirements

| What | Which | Why |
| --- | --- | --- |
| Python | 3.12 or newer | The connector itself. |
| `worc` | **0.14.0a1 or newer**, in the target clone | The release that ships `references:`, `pr_url` and the `rejected` listing. An older worc works with three conveniences missing, nothing fails. |
| `gh` | on `PATH`, logged in (`gh auth login`) | Every tracker call runs as your own login. The connector holds no token. |
| A clone | the same one `worc watch` runs in | The connector's only write into it is a task file in `tasks/preparing/`. |

## Quick start

```bash
pipx install "git+https://github.com/VladimirMakarevich/worc-connect.git#egg=worc-connect[github]"

cd /path/to/your/clone                     # the same clone `worc watch` runs in
gh auth status                             # must say you are logged in
worc-connect init --repo OWNER/REPO        # writes .worc-connect/config.yaml, gitignores the home
worc-connect watch --once --dry-run        # lists the gated items and writes nothing at all
```

The dry run prints every item the tick saw, whether the gate admitted it and why, and the task id, branch and label it _would_ write. It creates no file — no task, no state database, not even a log. Edit `.worc-connect/config.yaml` and re-run it until the plan is what you expect, then do one real pass:

```bash
worc-connect watch --once                  # one real tick: stages, promotes, labels, comments
worc-connect status                        # what it now believes about each item
```

The step-by-step version of all of this — including how to label your first issue, what to look for on it, and how to tell a working run from a stuck one — is the [getting-started guide](docs/getting-started.md).

## What each label means

Exactly one of these is on an issue at a time; re-applying the one it already has does nothing. The prefix is yours to change (`write_back.labels_prefix`). On its first tick the connector creates whichever labels the repository is missing — these, and the trigger label(s) your gate names, so a fresh repository has something to gate with — and never edits one that already exists; the two triage labels are created only with the triage step on. A state label the connector did not write, with no worc task behind it, is left alone and logged as `state-label-without-task`: remove it to let the connector take that issue on.

| Label | What it means |
| --- | --- |
| `worc:queued` | The task file has been promoted into worc's queue. The comment names the task id. |
| `worc:in-progress` | worc is running the task. |
| `worc:pr-open` | The task opened a pull request; the comment carries its URL. From here the connector only watches — it never pushes to the branch. |
| `worc:done` | The pull request was merged (and, unless you set `close_on_merge: false`, the issue was closed), or worc finished the task without opening one. |
| `worc:failed` | worc ended the task without a pull request, or the pull request was closed unmerged, or worc's validation gate refused the task. The comment names the status — for a refused task, the reason worc recorded — and points at `worc status <task-id>` on the host. |
| `worc:needs-info` | Triage concluded that something only you can answer is missing. The comment carries the question. Reply on the issue and triage runs again; the connector's own comment never counts as a reply. _(triage only)_ |
| `worc:declined` | Triage concluded this is a duplicate, or outside what the project does. The comment says why. _(triage only)_ |

**On `worc:failed`, the recovery is yours and it happens on the host**, with `worc status <task-id>` and `worc rerun`. The connector never re-queues, edits or reruns a task on its own, and it never copies worc's logs or diffs onto the issue — a public issue is not the place for them. A pull request closed without a merge and then reopened returns the issue to `worc:pr-open` on the next tick; the connector recomputes every pull-request-derived state from the request itself, every time.

## Commands

| Command | What it does |
| --- | --- |
| `worc-connect init --repo OWNER/REPO` | Create `.worc-connect/`, write a configuration to edit, and append the home to the clone's `.gitignore`. Refuses to overwrite an existing configuration. |
| `worc-connect watch` | Poll every `poll_interval_seconds` until stopped. Holds `.worc-connect/connect.pid`, so a second watcher in the same clone refuses to start. |
| `worc-connect watch --once` | A single pass, for a cron job or a first real run. Writes no PID file. |
| `worc-connect watch --dry-run` | Print the plan and write nothing anywhere. Combines with `--once`. |
| `worc-connect status` | The items taken on and their phase, the poll watermark, and how the last tick ended. |
| `worc-connect install-flow` | Copy the shipped triage flow into worc's `.worc/flows/`. Only with the triage step on; refuses to overwrite a copy you edited unless given `--force`. |

Every command takes `--home PATH` to point at a connector home other than `./.worc-connect`. Exit codes: `0` completed, `1` an infrastructure failure or a refused start, `2` a usage or configuration problem.

**Stopping a watcher** is a file, not a signal — the same on Windows and POSIX:

```bash
: > .worc-connect/connect.stop    # the loop exits before its next tick and removes the sentinel
```

## Two things to know before you run it

- **Issue text is untrusted input from strangers.** It ends up in a prompt to an agent with write access to your repository. That is why nothing becomes a task unless a maintainer gated it, why the connector refuses to start with no gate rule, and why an issue's text reaches only the task file — never a command, a log line, a label, or a comment.
- **`auto_merge` stays unset.** An issue-sourced task under `auto_merge: true` is where a prompt injection in an issue turns into merged code with no human in between. `worc-connect init` does not set it; worc's own policy and your review of the PR decide.

## Documentation

| Guide | Read it for |
| --- | --- |
| [Getting started](docs/getting-started.md) | The whole path, in order: install, log in, configure, dry run, first real issue, and how to verify each step. |
| [How it works](docs/how-it-works.md) | What one tick does, the lifecycle of an item, and every file the connector owns. |
| [Configuration reference](docs/configuration.md) | Every key of `.worc-connect/config.yaml`, with its default and what it changes. |
| [Operating it](docs/operations.md) | Running it for real: daemon or cron, stopping, logs, upgrades, recovering a failed task. |
| [Triage (optional)](docs/triage.md) | The optional agent analysis step: what it costs, what it decides, how to install its flow. |
| [Troubleshooting](docs/troubleshooting.md) | Symptom → cause → fix, with the exit codes and the log vocabulary. |
| [docs/](docs/README.md) | The index of all of the above. |

## Boundaries

The rules every coding agent (and every human) follows here are in [AGENTS.md](AGENTS.md) and [.agents/rules/](.agents/rules/architecture.md). The ones worth knowing as an operator:

- **Into worc, one write:** a task file in `tasks/preparing/<id>.md`, promoted with `worc promote`. **Out of worc, two reads:** `worc list --format json --all` for the state of a task, and `worc --version` once per run for which front-matter keys that worc accepts. The connector never runs `git` in your clone, never writes into `tasks/pending/`, and never reads anything under `.worc/` — worc's own `config.yaml` included. The one thing it ever writes under `.worc/` is the triage flow `install-flow` copies into `.worc/flows/`, on your command and from bytes shipped in this package. The two things both sides must agree on, worc's `paths.tasks_dir` and its `validation.max_*` limits, are keys in the connector's own configuration instead; the [configuration reference](docs/configuration.md) says what to do if you changed either.
- **It never pushes to a pull-request branch.** Once your task's PR exists it is yours to edit, retitle, reopen, merge any way GitHub allows and delete the branch of; the connector tracks it by number and recomputes its state from the PR itself on every tick.
- **Its state is a cache.** `.worc-connect/state.db` is rebuilt from the tracker, `worc list` and the lifecycle folders on every tick, so deleting it is safe.
- **A task in flight is followed until it ends.** The poll asks the tracker what changed, and nothing changes on an issue while its task runs — so the connector reads such an issue by number on every tick until its task has a pull request or has ended, and a busy repository cannot make it lose track of one.
- **No agent runtime.** The polling loop makes no model call and the package depends on no model SDK; everything that needs a model is a worc task, run inside worc's sandbox with worc's own containment and audit trail. Triage is no exception: the connector queues a task and reads a file.
- **The model proposes, the connector decides.** A triage report reaches a task only through the deterministic builder, and only what the builder chose from it. A report carrying no machine-readable verdict produces no task at all — it becomes a visible failure on the issue.

The plan and the design record are in [docs/backlog/](docs/backlog/README.md); decisions taken deliberately and meant to be revisited are in [docs/backlog/follow-ups.md](docs/backlog/follow-ups.md).

## Development

```bash
python -m venv .venv && . .venv/bin/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install -r requirements-worc.txt            # worc itself, for the one suite that needs it
pre-commit install && pre-commit install --hook-type pre-push
ruff check . && ruff format --check . && mypy src && lint-imports && python tools/size_gate.py && pytest
```

Windows, macOS and Linux are all release targets; the test suite runs natively on all three in CI and never launches the real `gh` or `worc` — every integration test drives a fake executable, so the suite needs no network and no credential.

The second install is worc itself, and it is a **test** dependency: one suite feeds a generated task file to worc's real validation gate, because the connector has to produce a file worc accepts unchanged and worc rejects rather than repairs. Nothing under `src/` imports it, which is why it is not in `pyproject.toml` — worc is published to no package index, and a direct reference in the project's own metadata is refused by the build backend and by every index. Skip it and that suite skips with a note; the rest of the suite is unaffected. The requirements file names a branch, because the contract this connector adopts is not on worc's default branch yet; a worc installed from somewhere else and predating it skips the contract assertions with a reason instead of failing.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
