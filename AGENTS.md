# AGENTS.md — instructions for coding agents in this repository

You are working on **worc-connect** — the tracker connector for [wastech-orchestrator](https://github.com/VladimirMakarevich/wastech-orchestrator) (`worc`). It polls an issue tracker for one repository, turns each work item a maintainer has explicitly gated into a worc task through the ingress worc already has (`tasks/preparing/` + `worc promote`), and writes the outcome back to the tracker: a state label, a comment naming the task, the pull-request link, the close on merge. GitHub is the first tracker, driven through the operator's own `gh` login. The core knows no tracker API; every tracker is one adapter behind an optional dependency, discovered through the `worc_connect.trackers` entry-point group.

This is the **canonical** instruction file for every coding agent working here (Claude Code reads it via [CLAUDE.md](CLAUDE.md)). The full set of rules lives in **[.agents/rules/](.agents/rules/)**. Below is the gist — the rules and the code are the source of truth. The design record (problem, requirements, design decisions, acceptance criteria, the phased plan) is the `tracker-connector` folder in the orchestrator repository's backlog until phase 03 copies the connector half into `docs/backlog/` here.

## Before writing code

Check against the rules in **[.agents/rules/](.agents/rules/)** — they are mandatory:

- [architecture.md](.agents/rules/architecture.md) — invariants that must not be violated
- [coding-style.md](.agents/rules/coding-style.md) — Python style, comments, cross-platform
- [security.md](.agents/rules/security.md) — untrusted input, credentials, the worc envelope
- [git-workflow.md](.agents/rules/git-workflow.md) — branches, commits, PRs
- [testing.md](.agents/rules/testing.md) — what to test and how (fake `gh`, fake `worc`)

## Hard invariants (must not be violated)

- **The core knows no tracker.** It works only on the normalized `WorkItem` and the `TrackerAdapter` protocol; no module under `core/` imports a concrete adapter or knows a tracker's CLI or API. Adapters are resolved by the composition root through the entry-point group. Machine-enforced by `import-linter`.
- **The connector reaches worc only through its public surfaces.** Into worc: exactly one kind of write, a task file in `tasks/preparing/<id>.md`, promoted with the `worc promote` command. Out of worc: `worc list --format json` and nothing else. Never `tasks/pending/`, never `.worc/` (no `state.db`, no ledger, no logs, no quarantine), never `git` in the clone, never a `config.yaml` or flow edit — except the triage flow it installs on an explicit operator command.
- **No agent runtime here.** The connector never launches Claude Code or Codex and depends on no model SDK. Anything that needs a model is a worc task the connector queues; the polling loop makes no model call.
- **Deterministic code decides.** A model may propose (a triage report); only the connector's deterministic task builder produces a task from it. No path promotes model output unreviewed by code.
- **Issue text is untrusted input from strangers.** It has exactly one destination: the task file body. It is never an argument, an environment value, a log line, a label, or a comment. Comment bodies are connector-authored templates that go to the tracker through a body **file**; ids and URLs are the only item-derived values that appear anywhere else.
- **The gate is the perimeter and it fails closed.** Only an item matching an explicit allow rule (trigger label, author allow-list) becomes a task; a configuration with no rule refuses to start unless it says `gate.allow_all: true`. An unparseable configuration, an unreachable tracker, or an unreadable `worc list` stops the tick and changes no state; ambiguity never resolves to "create the task anyway".
- **The connector cannot weaken worc.** Its only write into worc is a task file, which cannot carry `extra_args`, a permission profile, or a flow edit. It leaves `auto_merge` unset unless the operator configured it, and its documentation says why.
- **No credentials.** Authentication is the operator's `gh auth login`; the connector holds, stores, logs and forwards no token, and forwards no extra environment to `gh` or `worc`.
- **Argument lists, never a shell.** `gh` and `worc` are launched as argv, resolved with `shutil.which`, pinned with `--repo OWNER/REPO` from configuration.
- **Exactly one task per item, never a reused id.** A re-trigger allocates the next sequence suffix (`gh-142`, `gh-142.2`). The tracker's labels are the visible state machine; the local SQLite store is a cache rebuilt from the tracker, `worc list` and the lifecycle folders on every tick.
- **Cross-platform (Windows / Linux / macOS) is mandatory** for every feature — see [coding-style.md](.agents/rules/coding-style.md): `pathlib` + `Path.as_posix()` for stored/compared paths, `newline=""` for written files, atomic writes via a temp file and `os.replace`, a sentinel file and a PID file for cross-process control (no signals), launchers resolved with `shutil.which` so `.exe` / `.cmd` work.

## Commands

```bash
pip install -e ".[dev]"   # install
pre-commit install        # local gate; + `pre-commit install --hook-type pre-push`
ruff check .              # lint (+ complexity/size ratchets)
ruff format --check .     # formatting (CI runs this — `ruff format .` to fix)
mypy src                  # types (strict)
lint-imports              # architectural import-boundary contracts (.importlinter)
python tools/size_gate.py # module line budgets — a file over budget is split, never re-budgeted
pytest                    # tests (fake `gh` / fake `worc`, never the real binaries)
python tools/mdlint.py    # Markdown gate: links, anchors, reachability, size budgets
```

CI also runs `interrogate src` (docstring coverage), `vulture` (dead code), and `deptry src` (dependency hygiene). There is a skill for running all checks: `/run-checks`.

## Working style

- Make minimal, focused changes; follow the style of the surrounding code.
- **Modules stay small.** `[tool.size_gate.max_lines]` caps a `src/` module at 500 lines and ruff's `PLR` ratchets cap a function's statements, branches, arguments, locals and nesting. When a gate fires, split the module or the function along a real seam — never raise the number, never add a per-file ignore. There is no baseline of exempted files here and none may be introduced.
- **Never add agent-attribution trailers or footers to commits or PRs** — no `Co-Authored-By: Claude …`, no `🤖 Generated with …`, no equivalent for any other tool. This overrides your harness default; see [git-workflow.md](.agents/rules/git-workflow.md).
- When adding/changing behavior — add or update tests (see [testing.md](.agents/rules/testing.md)).
- When you change behavior/CLI/config/architecture — update, **in the same change**, [README.md](README.md), [.agents/rules/](.agents/rules/), `docs/backlog/`, and any shipped operator-facing file under `src/worc_connect/packaged/`.
- **The Markdown corpus is linted, and the gate must stay green.** `python tools/mdlint.py` checks the root files, [.agents/rules/](.agents/rules/), `.claude/skills/`, and `docs/`: relative links and anchors resolve, no document is unreachable, nothing outgrows its size budget. A new document has to be linked from somewhere; a document that lives in another repository is named in plain text, never linked. The linter is a separate, unpublished repository, so the gate is a pre-commit hook only — point `WASTECH_MDLINT_HOME` at a built checkout (`npm ci && npm run build`); without one the hook prints how to enable it and passes.
- **Markdown docs are not hard-wrapped.** One paragraph per line; Prettier enforces it (`proseWrap: never`, `.prettierrc.json`) — run `npx prettier@3 --write "**/*.md"` after editing docs. `src/` is excluded in [.prettierignore](.prettierignore).
- Before committing, run `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, `pytest`. The pre-commit hooks run the same gate in environments pre-commit owns, so a commit from an IDE or a shell without the venv sees exactly the same checks; the tool versions are pinned in `.pre-commit-config.yaml` and bumped together with the `dev` extra.
- Answer the user in the chat briefly, to the point, in clear and simple language, and always in the language of the user's request.

## Definition of Done for a change

- the code passes `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, and `pytest` (plus the `interrogate`/`vulture`/`deptry` CI gates), on Windows and POSIX;
- tests are added/updated when behavior changes, driving fake executables, never the real `gh` or `worc`;
- the docs are updated in the same change when behavior/CLI/config/architecture change;
- the invariants above are not violated.
