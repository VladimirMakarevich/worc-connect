# Architecture rules (invariants)

The source of truth is the code (`src/worc_connect/`). These invariants must not be violated.

## Shape

The connector mirrors worc's own architecture on purpose: a core that knows no external syntax, adapters that know one thing each, deterministic code deciding what a model may only propose.

- **Core** (`core/`) — the gate, the task builder, the handoff into worc, the reconcile of task and PR state, the write-back state machine, the tick loop, and the SQLite cache. It sees only a normalized `WorkItem` / `PullRequest` and the `TrackerAdapter` protocol.
- **Tracker adapters** (`trackers/<name>/`) — one per tracker, behind an optional dependency (`worc-connect[github]`), discovered through the `worc_connect.trackers` entry-point group. An adapter maps the six connector states to its tracker's vocabulary (GitHub: labels `worc:<state>`), lists and reads items, adds and removes state, posts comments and closes, and finds a pull request by branch or by number. It raises a small set of infrastructure errors (`TrackerUnavailable`, `TrackerAuth`, `TrackerRateLimited`) that the loop turns into "skip this tick, keep state".
- **Configuration** (`config.py`, with `config_section.py` for the typed key reader and `config_errors.py` for its one exception) — the fail-closed loader of the connector's own YAML under `.worc-connect/`. Shapes only; it depends on nothing above it.
- **The home** (`home.py`) — the connector's directory in the clone: the fixed names of every file it owns, the configuration `init` seeds, and the `.gitignore` line that keeps all of it out of a commit. The gitignore probe is textual because the connector never runs `git`, not even to read.
- **CLI** (`cli.py`) — the composition root: `init`, `watch [--once] [--dry-run]`, `status`, `install-flow`. The only module that resolves an adapter by name.
- **The shipped flow** (`flows.py` + `packaged/flows/`) — the triage flow and its role prompts, and the one function allowed to write inside `.worc/`. It copies shipped bytes into `.worc/flows/` on an explicit operator command and refuses to overwrite a copy the operator edited without `--force`; it reads nothing of worc's.
- **Dependency direction is `cli → core → trackers/base` and `cli → trackers/<name>`**: the core never imports a concrete adapter, an adapter never imports the loop, the CLI or the configuration. Machine-enforced by `import-linter` (`lint-imports`).

## The boundary with worc

The connector and `worc watch` run on the same host and the same clone. The boundary between them is a contract, and it is narrow on purpose.

- **Into worc, exactly one write:** a task file in `tasks/preparing/<id>.md` — written atomically (temp file + `os.replace`, UTF-8, `newline=""`) — then moved by the `worc promote <id>` command, launched as an argument list. `preparing/` is worc's documented staging area that the watch scanner never reads; `promote` is its atomic, refuse-to-overwrite step. Never write into `tasks/pending/`.
- **The two facts both sides must agree on are configuration, not a third read.** The lifecycle directory (worc's `paths.tasks_dir`) and the three size limits the gate enforces (`validation.max_*`) are keys in the connector's own configuration, defaulted to worc's defaults. Reading them out of worc's `config.yaml` would be a second read out of worc and a read inside `.worc/`, and both of those lines are absolute; an operator who moved either restates it, and the connector's loader is what refuses a value it cannot use.
- **Out of worc, two reads and no more:** `worc list --format json --all` for task status, the PR URL and the rejection reason, and `worc --version` for the one thing that has to be known before a file is written — which front-matter keys this worc accepts. `status` is a display label, so match its leading token. A file still in `tasks/pending/` has no row yet and is read from disk. A task worc's gate rejected has no row: it is reported from the `rejected` section of that same listing where worc provides one, and as a reason-less failure otherwise.
- **The version handshake fails closed and is asked once per process.** A worc that cannot be launched, will not say what it is, or reports a version below the documented minimum is treated as one that predates the contract: the task is built with exactly the keys an older worc already accepts. An unknown front-matter key is a hard reject at worc's gate, and a rejected task is quarantined inside the home the connector may not read, so "I cannot tell" must never resolve toward emitting the key.
- **Never** `.worc/` (no `state.db`, no ledger, no logs, no quarantine), **never** `git` in the clone, **never** worc's `config.yaml` or a flow file — except the triage flow copied into `.worc/flows/` by `install-flow` on an explicit operator command.
- **The connector never contends for worc's processing slot** and tolerates the clone being checked out on a task branch: `tasks/preparing/` is untracked staging and survives worc's checkouts.
- **The connector's home is `.worc-connect/`** (config, SQLite, log, PID file, stop sentinel, the triage reports), appended to the tracked `.gitignore` by `init`. It is never a subfolder of `.worc/`: a foreign process writing a cache of untrusted issue text inside worc's control home would blur a boundary worc drew deliberately.
- **The connector reads a task's PR by branch once and by number afterwards**, recomputing every PR-derived state from the PR's live state on every tick. The published PR is the owner's to edit, retitle, reopen, merge any way and delete the branch of; none of that changes discovery. The connector never pushes.

## The model boundary

- **No agent runtime.** The connector never launches Claude Code or Codex and depends on no model SDK. The polling loop makes no model call; a gated item costs exactly one worc task (two with triage on).
- **The analysis step is optional and off by default.** `research.mode` is `off` unless the operator names one; with `worc` an item first becomes a worc _triage_ task (`task_type` naming the flow, `priority: high`), and the connector reads the report from its own home (`.worc-connect/triage/<task_id>/report.md`, the flow's declared report directory) and either runs the **same** task builder on it or writes back `needs-info` / `duplicate` / `declined`. Both paths converge on one builder so the implementation task's shape never depends on the path. A mode this build cannot carry out is refused at load by name, never silently treated as another one.
- **One task per row, and the two tasks of one item are two rows.** A triage task and the implementation task it produced each get their own row and their own id (`gh-142`, then `gh-142.2`), which is how "an id is never reused" survives the second path. The triage row ends in a phase the item is never shown: what the item shows then is the implementation task's own state.
- **A report that cannot be read is a visible failure, never a fallback.** No report file, no verdict block, a verdict outside the closed vocabulary — all end the attempt at `worc:failed` with a comment naming where the connector looked. There is no second location to look in, because a fallback is how a connector ends up reading worc's private home.
- **The model proposes, the builder decides.** A report never becomes a queued task without passing through the deterministic builder; the builder, not the agent, decides what of the report reaches the task body.

## State

- **State labels are created before the first one is used, not at `init`.** `init` is offline: it writes the connector's own home and touches no network. The adapter creates whichever state labels the repository lacks once per process, just before the first state is published, and never edits a label that already exists.
- **Labels are the visible state machine; local state is a cache.** Seven connector-owned states — `queued`, `in-progress`, `pr-open`, `done`, `failed`, and (triage only) `needs-info` / `declined`, which a `duplicate` verdict also publishes as — exactly one label at a time, re-applying the current state is a no-op. The SQLite row per item exists for idempotency; on every tick and on restart the truth is re-read from the lifecycle folders, `worc list`, and the tracker, and the row is corrected. Deleting the database is safe.
- **An item with a task in flight is followed whether or not the poll window lists it.** The listing answers "what changed", and an item whose task is running is exactly an item nothing changes. Every row that is not terminal and whose item the listing did not return is read by identifier in the same tick; an item that cannot be read costs its row one tick, never the whole tick. The watermark moves on the listed page only — it is a poll filter, not the authority on what to follow.
- **Exactly one task per item, across restarts, crashes and repeated polls.** A crash between "file written" and "promote ran" is harmless because the next tick re-runs `promote`, which refuses to overwrite. A re-trigger allocates the next sequence suffix (`gh-142`, `gh-142.2`); an id is never reused. A row is armed for a re-trigger only while its trigger is withdrawn — a label put back while the task still runs disarms it — and a `needs-info` row is re-triggered by the item changing after the connector's own question was posted, never by that question itself.
- **Every generated task passes worc's gate unchanged**: the id grammar (`^[a-z0-9][a-z0-9._-]{0,63}$`, no trailing dot, no Windows device name), a sanitized title that cannot trip the front-matter injection scan (no leading `-`, no `;`, backtick, `|`, `$(`, newline), a non-empty `## Description`, only keys from worc's allowed set, a body under worc's size limits with a visible truncation marker, a `branch_name` of at most 50 characters that never equals the base branch. The connector sanitizes because worc rejects and does not sanitize — and a rejection is never silent: it becomes `worc:failed` on the item.
- **Dispatch fields come only from configuration.** A task field is emitted only when the operator set it; worc's own defaults otherwise stand. Nothing item-derived but the id, the branch, the title and the body enters the front matter.

## What must not be done

- Import a concrete tracker adapter from `core/`, or teach the core a tracker's CLI or API — the tracker's own closing keyword included, which is `TrackerAdapter.closing_reference`'s to author.
- Let a triage report decide anything but which of four verdicts applies: the builder chooses what of it reaches a task, and under which heading.
- Write anywhere in the clone but `tasks/preparing/<id>.md`; write into `tasks/pending/`; run `git`; read `state.db`, the ledger or anything under `.worc/`.
- Launch an agent, import a model SDK, or let model output reach the queue without the deterministic builder.
- Put item text into an argument, an environment value, a log line, a label or a comment.
- Create a task for an item no explicit rule admitted, or resolve any ambiguity toward "create the task anyway".
- Hold, store, log or forward a credential; forward extra environment to `gh` or `worc`.
- Reuse a task id; keep a frozen PR-derived state instead of recomputing it from the live PR.
- Add a worc config key, a tracker-specific field to worc, or a second copy of worc's internals.
