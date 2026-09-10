---
name: fake-cli
description: Scaffold deterministic fake `gh` and fake `worc` executables and pytest fixtures that stand in for the real binaries in worc-connect integration tests. Use when adding integration coverage for the GitHub adapter, the handoff into worc, the reconcile, or the write-back — success and every infrastructure failure scenario.
---

# fake-cli

Create stub executables and the pytest plumbing that point the connector at them, so integration tests exercise the adapter, the handoff and the reconcile without the real `gh` or `worc` — no network, fully deterministic ([testing.md](../../../.agents/rules/testing.md)).

## Two fakes

- **fake `gh`** — answers each subcommand from a JSON fixture (`issue list`, `issue view`, `pr list --head`, `pr view <n>`) and **records every argv** it was called with, so a test can assert what was posted (`issue edit --add-label/--remove-label`, `issue comment --body-file`, `issue close --comment`, `label create`) and what was **not** (a dry run records no side-effect verb). Infrastructure scenarios: an auth error, a rate-limit error, a network error — each with the exit code and stderr signature the adapter maps to `TrackerAuth` / `TrackerRateLimited` / `TrackerUnavailable`.
- **fake `worc`** — `promote <id>` moves `tasks/preparing/<id>.md` into `tasks/pending/` and prints `promote: <file> already in pending — not overwriting a queued task` with exit 1 when the target exists; `list --format json [--all|--pending]` returns a fixture (including a `rejected` section when the scenario needs one); `--version` prints a configurable version.

## Steps

1. **Stub body — one Python script per fake, OS-independent.** `fake_gh.py` / `fake_worc.py` read the scenario (fixture directory, canned exit codes) from an **environment variable** the fixture sets, append their argv to a recording file, write the canned stdout/stderr, and exit with the chosen code. Deterministic — no real time or randomness; bound any sleep.
2. **Launcher named like the CLI.** The connector resolves `gh` / `worc` with `shutil.which`, so the fake must be a runnable executable of that name on `PATH`:
   - **Windows:** `gh.cmd` / `worc.cmd` containing `@python "%~dp0fake_gh.py" %*`.
   - **POSIX:** `gh` / `worc` with a `#!/usr/bin/env python3` shebang and the executable bit. Generate the launcher for the current OS in a fixture, place it in a `tmp_path` dir, and prepend that dir to `PATH` for the test.
3. **Pytest fixtures (`tests/conftest.py`, `tests/fakes/`).** A fixture builds both launchers for a requested scenario, creates the clone layout (`tasks/preparing/`, `tasks/pending/`, a worc `config.yaml` when the scenario reads limits from it), and returns the recording paths.
4. **Assert** the recorded argv (verbs, `--repo` pin, `--body-file` use, absence of side effects), the files under the clone (only `tasks/preparing/<id>.md`), the comment bodies, and that a sentinel planted in the item body appears in the task file and nowhere else.

## Rules

- Deterministic and isolated: **no network, no real `gh` or `worc`**, time bounded or injected.
- Cross-platform: the fixture must work on Windows and POSIX (generate the matching launcher).
- Never bake a real token into a stub or a fixture.
- A fake never runs `git`; a test asserting "no `git` process was launched" reads the recording, not the host.

## Definition of Done

- Reusable fixtures produce a runnable fake `gh` and fake `worc` per scenario.
- Every infrastructure scenario and every side-effect verb is covered and asserted through the recording.
- The suite passes on Windows and POSIX; `/run-checks` green.
