# worc-connect

**Labelled issue in → worc task out → status back on the issue.**

`worc-connect` is the tracker connector for [wastech-orchestrator](https://github.com/VladimirMakarevich/wastech-orchestrator) (`worc`). It runs beside `worc watch` in the same clone, polls an issue tracker for one repository, turns each work item a maintainer has explicitly gated into a worc task through the ingress worc already has (`tasks/preparing/` + `worc promote`), and writes the outcome back to the tracker: a state label (`worc:queued` → `worc:in-progress` → `worc:pr-open` → `worc:done`), a comment naming the task, the pull-request link, and the close on merge. GitHub is the first tracker, driven through the operator's own `gh` login. The core knows no tracker API; every tracker is one adapter behind an optional dependency.

**Status: bootstrapped, no behaviour yet.** The repository carries its quality gates, its rules for coding agents and an importable package skeleton; the connector's phases land next. The design record lives in the orchestrator repository's backlog (`docs/backlog/tracker-connector/`) until its connector half is copied into [docs/backlog/](docs/backlog/README.md) here.

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

## Development

```bash
python -m venv .venv && . .venv/bin/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pre-commit install && pre-commit install --hook-type pre-push
ruff check . && ruff format --check . && mypy src && lint-imports && python tools/size_gate.py && pytest
```

The rules every coding agent (and every human) follows here are in [AGENTS.md](AGENTS.md) and [.agents/rules/](.agents/rules/): the connector never reads `.worc/`, never runs `git` in the clone, never launches an agent, and treats issue text as untrusted from the first line. Windows, macOS and Linux are all release targets; the test suite runs natively on all three in CI and never launches the real `gh` or `worc`.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
