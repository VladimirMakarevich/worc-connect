# worc-connect

**Labelled issue in → worc task out → status back on the issue.**

`worc-connect` is the tracker connector for [wastech-orchestrator](https://github.com/VladimirMakarevich/wastech-orchestrator) (`worc`). It runs beside `worc watch` in the same clone, polls an issue tracker for one repository, turns each work item a maintainer has explicitly gated into a worc task through the ingress worc already has (`tasks/preparing/` + `worc promote`), and writes the outcome back to the tracker: a state label (`worc:queued` → `worc:in-progress` → `worc:pr-open` → `worc:done`), a comment naming the task, the pull-request link, and the close on merge. GitHub is the first tracker, driven through the operator's own `gh` login. The core knows no tracker API; every tracker is one adapter behind an optional dependency.

**Status: gated issues become worc tasks.** The connector polls a real repository, applies the gate, writes the task file into worc's `tasks/preparing/` and promotes it with `worc promote` — exactly once per item, with a task id that is never reused. What it does **not** do yet is write anything back onto the issue: the state label, the comments, the pull-request link and the close on merge are the next phase. `worc-connect watch --once --dry-run` is still the safe first command and now prints the task id and branch it would allocate. The plan and the design record are in [docs/backlog/](docs/backlog/README.md).

## What it will do

1. A maintainer adds the trigger label (default `worc`) to an issue. Nothing else.
2. Within one poll interval (default five minutes) the connector allocates a task id (`gh-142`) and a branch name, writes `tasks/preparing/gh-142.md` — the issue text, verbatim, under a provenance line — and runs `worc promote gh-142`. The issue gets `worc:queued` and a comment naming the task.
3. `worc watch` runs the task to a pull request as it does any other; the connector follows it through `worc list --format json`, moves the label, and comments the PR link.
4. When the PR is merged — by anyone, any way — the connector closes the issue.

Triage by an agent (analyse, reproduce, write a failing test) is an **optional** mechanic behind `triage.enabled`, off by default: the item first becomes a worc _triage_ task, and deterministic code builds the implementation task from the report, or writes back `needs-info` / `duplicate` / `declined`.

## Two things to know before you run it

- **Issue text is untrusted input from strangers.** It ends up in a prompt to an agent with write access to your repository. That is why nothing becomes a task unless a maintainer gated it, why the connector refuses to start with no gate rule, and why an issue's text reaches only the task file — never a command, a log line, a label, or a comment.
- **`auto_merge` stays unset.** An issue-sourced task under `auto_merge: true` is where a prompt injection in an issue turns into merged code with no human in between. `worc-connect init` does not set it; worc's own policy and your review of the PR decide.

## Install

```bash
pipx install "worc-connect[github]"     # once the first release exists; until then:
pipx install "git+https://github.com/VladimirMakarevich/worc-connect.git#egg=worc-connect[github]"
```

Requires Python 3.12+, a `worc` installed in the target clone, and `gh` on `PATH` logged in as the operator who runs the connector (`gh auth login`). The connector holds no token of its own.

## First run

```bash
cd /path/to/your/clone                     # the same clone `worc watch` runs in
worc-connect init --repo OWNER/REPO        # writes .worc-connect/config.yaml, gitignores the home
worc-connect watch --once --dry-run        # lists the gated items and writes nothing at all
```

The dry run is the safe first command: it prints every item the tick saw, whether the gate admitted it and why, and it creates no file — no task, no state database, not even a log. Edit `.worc-connect/config.yaml` (every key is in the [configuration reference](docs/configuration.md)) and run it again until the plan is what you expect.

## Commands

| Command | What it does |
| --- | --- |
| `worc-connect init --repo OWNER/REPO` | Create `.worc-connect/`, write a configuration to edit, and append the home to the clone's `.gitignore`. Refuses to overwrite an existing configuration. |
| `worc-connect watch` | Poll every `poll_interval_seconds` until stopped. Holds `.worc-connect/connect.pid`, so a second watcher in the same clone refuses to start. |
| `worc-connect watch --once` | A single pass, for a cron job or a first real run. Writes no PID file. |
| `worc-connect watch --dry-run` | Print the plan and write nothing anywhere. Combines with `--once`. |
| `worc-connect status` | The items taken on and their phase, the poll watermark, and how the last tick ended. |

Every command takes `--home PATH` to point at a connector home other than `./.worc-connect`. Exit codes: `0` completed, `1` an infrastructure failure or a refused start, `2` a usage or configuration problem.

**Stopping a watcher** is a file, not a signal — the same on Windows and POSIX:

```bash
: > .worc-connect/connect.stop    # the loop exits before its next tick and removes the sentinel
```

## Boundaries

The rules every coding agent (and every human) follows here are in [AGENTS.md](AGENTS.md) and [.agents/rules/](.agents/rules/). The ones worth knowing as an operator:

- **Into worc, one write:** a task file in `tasks/preparing/<id>.md`, promoted with `worc promote`. **Out of worc, one read:** `worc list --format json --all`. The connector never runs `git` in your clone, never writes into `tasks/pending/`, and never reads anything under `.worc/` — worc's own `config.yaml` included. The two things both sides must agree on, worc's `paths.tasks_dir` and its `validation.max_*` limits, are keys in the connector's own configuration instead; the [configuration reference](docs/configuration.md) says what to do if you changed either.
- **It never pushes to a pull-request branch.** Once your task's PR exists it is yours to edit, retitle, reopen, merge any way GitHub allows and delete the branch of; the connector tracks it by number and recomputes its state from the PR itself on every tick.
- **Its state is a cache.** `.worc-connect/state.db` is rebuilt from the tracker, `worc list` and the lifecycle folders on every tick, so deleting it is safe.
- **No agent runtime.** The polling loop makes no model call and the package depends on no model SDK; everything that needs a model is a worc task, run inside worc's sandbox with worc's own containment and audit trail.

## Development

```bash
python -m venv .venv && . .venv/bin/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pre-commit install && pre-commit install --hook-type pre-push
ruff check . && ruff format --check . && mypy src && lint-imports && python tools/size_gate.py && pytest
```

Windows, macOS and Linux are all release targets; the test suite runs natively on all three in CI and never launches the real `gh` or `worc` — every integration test drives a fake executable, so the suite needs no network and no credential.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
