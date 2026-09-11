# Code style rules

## Language and tooling

- **Python 3.12+**.
- Formatting and linting: **ruff** (`ruff check .`, `ruff format`).
- Types: **mypy** in strict mode for `src/`. The entire public API is annotated.
- Tests: **pytest**.

## Comments and rationale

- Treat comments as part of the deliverable: all new code must be documented where it is introduced, not left for a later cleanup pass.
- Follow the rule `why, not what`: comments explain why the code exists, why a constraint matters, and why a specific shape was chosen, not what the syntax already says.
- Prefer self-documenting names over label-comments. A clear name is part of the documentation rule and should replace comments that merely tag a variable, branch, or helper.
- Comment the non-obvious parts: rationale, invariants, tradeoffs, external-system constraints, race conditions, portability traps, and bug-prevention context.
- Do not add comments that merely restate names, types, assignments, loops, or conditionals.
- Comments must be self-contained and independent. Never reference project documents, tickets, ADRs, PRs, backlog items, or other files in a comment (no "see docs/...", "per the ADR", "as described in ...") — those move, get renamed, or are deleted, leaving the comment dangling. State the actual `why` inline so the comment stands on its own.
- **A bare document identifier is a reference too, and is forbidden the same way.** Requirement, criterion, decision and phase tags — `FR-C4`, `AC-16`, `D14`, `Q-12`, `phase 05` — carry no meaning to anyone without the document they index, and that document is usually gone once the work lands. This applies everywhere the text outlives the document: `#` comments, docstrings, log records, exception messages, and every operator-facing string (the README, a packaged flow, a role prompt). When removing a tag, keep the reason and drop only the identifier. External identifiers that are not our documents (`CVE-…`, `RFC …`, an upstream issue number, a CLI flag name) are not affected.
- **Historical narrative is forbidden — a comment states what is, never what was.** Forbidden in every form: "used to be", "changed from", "no longer …", "we now …", "previously", "kept for backward compatibility", and the compatibility tombstone left behind after a rewrite. Rewrite each one as a positive statement of the current rule plus the reason it holds.
- **No decision attribution, no dates, no change-log entries.** A comment never records _who_ decided something or _when_. The reason a rule exists is what makes it enforceable, and the reason must stand on its own.
- **No references to runs, incidents, tasks, or phases.** What a run once proved is either a real constraint, in which case state the constraint, or it is not, in which case delete it.
- These rules bind **documentation as well as code**: [.agents/rules/](.), [README.md](../../README.md), and every operator-facing file under `src/worc_connect/`. The one place a decision record legitimately lives is `docs/backlog/` — those documents _are_ the record.
- If a block is hard to justify with a short why-comment, simplify or restructure it until the intent and rationale are clear.

## Size budgets (machine-enforced)

- **A `src/` module is at most 500 physical lines** (`tests/` 800, `tools/` 300, `.claude/hooks/` 200) — `python tools/size_gate.py`, per commit and in CI, budgets in `[tool.size_gate.max_lines]`. A module is what a reader or an agent loads whole, and a file grows one reasonable addition at a time; the gate makes the cost visible at the commit that crosses it, when a split is cheapest.
- **A function is bounded by ruff's ratchets:** complexity ≤ 10 (`C901`), statements ≤ 50, branches ≤ 12, returns ≤ 6, arguments ≤ 6 with ≤ 4 positional, locals ≤ 15, nesting ≤ 4, public methods per class ≤ 10 (`PLR09xx`, `PLR1702`).
- **When a gate fires, the fix is a split along a real seam** — a new module named for what it owns, a helper named for what it computes — never a raised threshold, never a per-file ignore. The orchestrator repository carries a burn-down baseline of 5,000-line modules precisely because it enforced these limits late; this repository starts with none and admits none.

## General principles

- Small, focused functions and modules with a single responsibility.
- **Keep it simple (KISS), no unrequested complexity.** Build the simplest thing that satisfies the task and these rules; don't add abstractions, configuration, or layers for hypothetical futures.
- **Add only what the task needs (YAGNI); extensibility through simplicity.** The one extension point this package carries by design is the `TrackerAdapter` protocol and its entry-point group — add another only when a concrete, known requirement needs it.
- Explicit is better than implicit: no "magic" global state.
- No side effects at module import time.
- Errors are surfaced through typed exceptions/results, not "bare" strings.
- **Fail closed.** Where the code cannot tell whether an action is safe — a configuration it cannot parse, a listing it cannot read, a gate rule that is absent — it stops and says why; it never guesses toward the side-effectful branch.

## Structure

- Package: `src/worc_connect/`, src-layout.
- One component = one module/subpackage (`core/gate.py`, `core/builder.py`, `core/handoff.py`, `core/reconcile.py`, `core/writeback.py`, `core/loop.py`, `core/state.py`, `trackers/<name>/`).
- Data contracts are `dataclasses` (frozen where the value is a record). The tracker contract is a `typing.Protocol`.
- Connector states, phases and other closed vocabularies are enums (`StrEnum`), never string literals scattered through the code.

## Naming

- `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants.
- Task ids, branch names and label names are produced by one builder each; no other module composes them.

## Processes and subprocess

- Run external CLIs (`gh`, `worc`) with an **argument list** (`subprocess.run([...])`), without `shell=True` and without interpolating item-derived strings. The executable is resolved with `shutil.which`, so `gh.exe` / `worc.cmd` launchers work on Windows.
- Every `gh` call carries `--repo OWNER/REPO` from configuration.
- **Item-derived text never travels through argv.** A comment body goes to the tracker through a file (`--body-file`); an identifier reaches an argument list only after it has been proven to be a number. Where a tool offers no body-file form — `gh issue close` takes only `--comment` — the argument is a connector-authored template carrying nothing but a task id, a status name and URLs, and it stays that way.
- Timeouts are mandatory for all external calls.
- The child environment is the connector's own environment, forwarded as is: nothing added, nothing item-derived.

## Cross-platform support (Windows / Linux / macOS) — mandatory

Every feature must work on **Windows, Linux, and macOS**. This is a release requirement, not an afterthought: design and test for all three as you build, never "POSIX now, Windows later".

- **Paths via `pathlib.Path`** — never hardcode `/` or `\`, and never assume `os.sep`. When a path is **stored, compared, displayed, or asserted as a string** (state rows, logs, test assertions), normalize it with `Path.as_posix()` so it is identical on every OS.
- **Files that another program reads or byte-compares** (the task file worc's gate hashes, a comment body file): open with `newline=""` and UTF-8 so `\n` is preserved — default text mode rewrites `\n`→`\r\n` on Windows.
- **Atomic writes**: a temp file in the same directory, then `os.replace`. A half-written task file must never be visible to `worc promote`.
- **Cross-process control is OS-neutral**: a stop sentinel file and a self-managed PID file, checked between ticks. `os.kill` and signals are POSIX-shaped and cannot probe or signal a foreign process on Windows — do not rely on them.
- **Branch platform differences explicitly** (`os.name == "nt"` / `sys.platform`) and make the seam injectable so **both** branches are unit-tested on any host (see [testing.md](testing.md)).
- **No POSIX-only filesystem assumptions**: no `/tmp`, `/dev/null`, `fork`, `fcntl`, executable-bit, or symlink dependencies; use `tempfile`, `os.replace`, `pathlib`.

## Logging

- Standard `logging`, one line per action: `item=<id> task=<task-id> action=<verb> result=<outcome>`.
- **Never** log item bodies, tokens, or the process environment. Ids and URLs are the only item-derived values a log line may carry.

## In-code documentation

- Public functions/classes have a docstring that explains the contract and intent: why the API exists, the guarantees and preconditions it relies on, and which exceptions it raises. Do not use docstrings to narrate the implementation step by step.
- Comments belong only where they explain "why", not "what".
