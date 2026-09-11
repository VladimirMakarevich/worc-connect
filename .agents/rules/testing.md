# Testing rules

The source of truth is the code (`src/worc_connect/`, `tests/`).

## Levels

### Unit

Cover pure logic without external processes:

- configuration loading and every rejection (an absent gate without `allow_all: true`, an unknown tracker, a missing repository, an unsafe `id_prefix`);
- the gate: trigger labels, the author allow-list, `allow_all`, an item that lost its label while queued;
- the title sanitizer: every token worc's injection scan rejects (leading `-`, `;`, backtick, `|`, `$(`, newline), control characters, whitespace collapse, the length cap, the `Issue #<n>` fallback;
- task id and branch allocation: worc's id grammar, no trailing dot, no Windows device name, the 50-character branch cap, the base-branch clash, the sequence suffix on re-trigger;
- body assembly: the provenance line, verbatim body, truncation to each of worc's three limits with a visible marker and the item URL;
- front matter: only configured keys are emitted, `commit_type_by_label`, `auto_merge` absent unless configured, the builder's key set a strict subset of worc's allowed set and never `nodes`, `subtasks`, `decomposition`, `trust_level`, `prompt_audit`;
- state transitions and idempotency: exactly one label at a time, re-applying the current state is a no-op, the phase table of the reconcile, the watermark overlap;
- the status-label parser over `worc list --format json`: the leading token of `running (paused)` and `parked (no daemon)`;
- platform seams: launcher resolution finds `gh.exe` / `worc.cmd` on the Windows branch and the bare names on POSIX; stored paths compare via `Path.as_posix()`.

### Integration

Use **fake executables** — a fake `gh` (JSON fixtures per subcommand, recorded argv) and a fake `worc` (`promote` moving the file and refusing an existing target, `list --format json` returning a fixture) — never the real binaries:

- a full tick on a gated item: the only `worc` launches are the version handshake (`--version`, once per process) and `promote <id>`, no `git` process, the only path written under the clone is `tasks/preparing/<id>.md`;
- a crash between the file write and `promote`: the next tick re-runs `promote`, one row, no second file;
- `promote` reporting "already in pending": the row becomes `queued`, nothing reaches the tracker;
- each tracker infrastructure error (auth, rate limit, network): the tick is skipped, no state changes, the process stays up;
- dry run: stdout names the plan, no file, no state row, no `gh` side-effect verb recorded;
- the three comment kinds and their contents; close on merge on and off; the rebuild of rows from labels plus `worc list` after the database is deleted;
- the owner's edits to a published PR (more commits, a new title, a squash merge, a deleted branch) and a PR closed then reopened: every PR-derived state is recomputed by number;
- a gate reject seen from outside `.worc/`: the file is gone from `pending/` and `worc list --all` has no row — or a `rejected` entry, whose `validation_reason` and nothing else from the entry reaches the comment;
- both sides of the version handshake: a worc that reports the documented minimum emits the adapter's closing line as `references:`, and a worc below it (or one that cannot be asked at all) emits no such key and produces the file an older worc already accepts;
- the pull request worc recorded is preferred over the branch query, and a listing without one still falls back to the branch;
- a sentinel string planted in an item body is found in the task file and nowhere else: not in any recorded argv, log line, or comment body.

### End-to-end

One real run, recorded in the README rather than automated: a labelled issue on a throwaway GitHub repository, `worc watch` and `worc-connect watch` side by side, from label to merged PR to closed issue; a second run in which the owner finishes the PR by hand.

## Principles

- Tests are deterministic and isolated (no network calls and no real `gh` or `worc` in unit/integration).
- External processes and time are mocked/injected.
- **The suite must pass on Windows, Linux, and macOS.** No POSIX-only assumptions in tests: compare paths via `pathlib`/`Path.as_posix()`, normalize newlines in byte-compares, generate the matching launcher (`.cmd` on Windows, a shebang script on POSIX) for a fake executable, and exercise **both** platform branches of any `os.name`-split logic by injecting the platform seam rather than depending on the host OS. See the cross-platform rules in [coding-style.md](coding-style.md).
- Every behavior change is accompanied by a test.
- The goal is high coverage of the critical paths (gate, builder, handoff, reconcile, write-back, the untrusted-text boundary), not a percentage for its own sake.
- A green `pytest` is a mandatory precondition for committing and for transitioning between implementation phases.
- The suite runs in parallel by default (`pytest-xdist`, `addopts = "-n auto"`). Run `pytest -n0` for a serial run when debugging.
- **worc is a test dependency, installed separately** (`pip install -r requirements-worc.txt`), and the suites that use it judge what the connector produces against worc's own rules rather than against a second, local copy of them, which could drift from the real ones without anything failing: the generated task file goes through worc's real `task.validation_gate`, the contract key `references:` included. It is deliberately not in `pyproject.toml` — worc is published to no index, and a direct reference in the project's metadata is refused by the build backend and by every index. The suite carries `requires_worc` and skips where it is absent; CI always installs it. Nothing under `src/` may import it.
- A handful of assertions are about the **installed distribution** rather than the working tree — the `worc_connect.trackers` entry-point group, the extras, the runtime dependency set, and the CLI paths that resolve an adapter through that group. They carry `requires_installed_distribution` and skip where the package is not installed, which is only ever an environment pre-commit builds to lint the tree; a dev virtualenv and CI both install it and run all of them.
- Heavy integration files (fake executables, a real process tree) are tagged `pytestmark = pytest.mark.slow`; `pytest -m "not slow"` is the fast inner loop, CI runs the whole suite. Markers are registered in `pyproject.toml` and `--strict-markers` is on.
