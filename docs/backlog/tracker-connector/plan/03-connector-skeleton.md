# Phase 03 — Connector skeleton: core, GitHub adapter (read side), gate, state, dry run

- **Status:** ☑ code complete on `feat/connector-skeleton` — the two acceptance lines below stay open until an operator has run the dry run against a real repository and the CI matrix is green
- **Depends on:** none (runs in the connector repository `VladimirMakarevich/worc-connect`, created empty and private on 2026-09-11; the home is `.worc-connect/` — Q-1 and Q-2 are settled)
- **Delivers:** FR-C1, FR-C2, FR-C8, FR-C13, FR-C14 — a `worc-connect watch --once --dry-run` that lists the gated items of a real repository and prints what it would do, with the package, gates, config loader, `TrackerAdapter`, `WorkItem`, gate, state store and loop in place.

## Goal

Stand up the connector repository with its quality gates and the tracker-agnostic core, and reach the first safe thing to run against a real repository: a dry run. Moves AC-1, AC-2, AC-8, AC-13, AC-14, AC-E1, AC-E7, AC-N1, AC-N5, AC-N8.

## Steps

1. Repository and packaging: `pyproject.toml` with the `github` extra (no runtime dependency beyond PyYAML in the core; `gh` is an executable, not a package), the entry-point group `worc_connect.trackers`, and the CI matrix (Windows + Linux): lint/format, type check, import-linter contract "core never imports trackers except `trackers/base.py`", tests, dependency hygiene. Mirror worc's `.agents/rules/` shape in an `AGENTS.md`.
2. `config.py` — the schema from design "Data & stored shapes", loaded fail-closed; the gate rule (`labels`/`authors`/`allow_all`) validated at load; `init` writes the file and appends the connector home to the tracked `.gitignore` (D5).
3. `core/items.py`, `trackers/base.py` — `WorkItem`, `PullRequest`, `ItemState`, the `TrackerAdapter` protocol and the three infrastructure error classes.
4. `trackers/github/` — the read side: `list_items(since)` via `gh issue list --repo … --state open --json number,title,body,author,labels,updatedAt,url --search "updated:>=…"`, `get_item`, `find_pull_request(branch)` via `gh pr list --head … --state all --json url,state,mergedAt`; executable resolved with `shutil.which`; every call an argv list; JSON parsed, errors mapped to the three classes.
5. `core/gate.py` — label / author / `allow_all`.
6. `core/state.py` — SQLite schema (`items`, `meta`), watermark with overlap.
7. `core/loop.py` — tick, `--once`, PID file, stop sentinel, refuse a second instance.
8. `cli.py` — `init`, `watch [--once] [--dry-run]`, `status`. Dry run prints the plan and performs no write and no side-effect call.
9. Fake `gh` executable for tests (JSON fixtures per subcommand, argv recording), following the shape of worc's fake-CLI fixtures.

## Files touched

- connector: `pyproject.toml` (PyYAML, the `worc_connect.trackers` entry point, `types-PyYAML`), `.importlinter`, `src/worc_connect/{cli,config,home}.py`, `src/worc_connect/core/{items,gate,state,loop}.py`, `src/worc_connect/trackers/{base.py,github/{__init__,gh,adapter}.py}`, `tests/` (fake `gh`, support helpers, seven suites), `README.md`, `docs/configuration.md`, `AGENTS.md`, `.agents/rules/architecture.md`.
- The repository's gates and CI workflow were already in place from the bootstrap commit, so step 1 only added the entry-point group, the runtime dependency and the tightened import contracts.
- Two modules are not in the design's layout sketch: `home.py`, split out of `config.py` when the module-size gate made the configuration file the wrong home for the `init` scaffolding, and `trackers/github/gh.py`, which owns launching `gh` and classifying its failures separately from the payload mapping.

## Invariants in play

- The core imports no adapter; adapters are resolved through the entry-point group.
- `gh` is launched as an argument list, resolved by `shutil.which`, pinned with `--repo` from configuration.
- No credential is read, stored or forwarded.
- No model dependency in the package.
- Cross-platform from the first commit: `pathlib`, sentinel-based stop, launcher resolution tested on both families.

## Tests

- Config: every rejection (`allow_all` absent with no rule; unknown tracker; missing repo).
- Gate: AC-2 matrix.
- Adapter: argv recorded for each verb; error mapping for auth / rate-limit / network exits.
- Loop: `--once` vs daemon, PID refusal, sentinel stop, watermark overlap idempotency.
- Dry run: AC-8 (no writes, no side-effect argv recorded).

## Docs to sync in this phase

- Connector `README.md` (install with the extra, `init`, first dry run) and configuration reference.

## Acceptance for this phase

- [ ] `worc-connect watch --once --dry-run` against a real repository lists the labelled issues and writes nothing — verified against a fake `gh` in the suite; the run against a real repository needs the operator's own `gh` login.
- [ ] AC-1, AC-2, AC-8, AC-13, AC-14, AC-E1, AC-E7 pass on Windows and Linux CI — green locally on macOS; the matrix runs when the branch is pushed.

## What the phase actually delivers

- `init` writes `.worc-connect/config.yaml` and gitignores the home; `watch [--once] [--dry-run]` lists, gates and records; `status` reports rows, phases, the watermark and the last tick.
- The dry run writes **nothing at all** — no task file, no state database, no log, no PID file — and the fake `gh` recording proves no side-effect verb was called.
- A row is created at phase `gated`; allocating an id, writing the task file and promoting it is the next phase, which picks those rows up.
- The state store discards a database written by another schema instead of migrating it, because the cache is re-derivable within one tick.
