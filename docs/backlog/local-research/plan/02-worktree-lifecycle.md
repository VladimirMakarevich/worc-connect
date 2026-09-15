# Phase 02 — The research clone and its worktrees

- **Status:** ☐
- **Depends on:** 01
- **Delivers:** FR-R3, FR-R13 — the connector's own bare clone with its push URL disabled, a fetch before each research, a detached worktree per research, removal, `prune` and an orphan sweep at startup.

## Goal

Give the research a place to run that is disposable, isolated from worc's clone, and incapable of publishing anything — with no agent involved yet, so the mechanics can be tested on their own.

## Steps

1. `research/worktree.py` — `ensure_clone()` creates `.worc-connect/repo.git` on first use (`git clone --bare <origin>`), then `git remote set-url --push origin DISABLED` so nothing in a worktree can push; `fetch()` before each research, followed by `git remote set-head origin -a` so `origin/HEAD` tracks the remote's default branch; `add(task_id, base_ref)` → `git worktree add --detach <root>/<task_id> <base_ref>`, where `base_ref` defaults to `origin/HEAD` (R-11); `remove(task_id)` → `git worktree remove --force`, tolerant of an already-gone tree; `sweep()` → `git worktree prune` plus removal of directories under the worktree root that no longer belong to a live research.
2. The origin URL is derived from the connector's own configuration (the `repo` pin), not from worc's clone configuration — the connector still runs no `git` against worc's clone. **Open:** "resolved through the tracker adapter's clone URL" is what an earlier draft said, and `TrackerAdapter` has no such method — adding one contradicts [R-11](../questions.md), which put the research path deliberately outside the adapter. Either the URL is composed from the tracker's known host and the `repo` pin (no protocol change, one host string per adapter), or the protocol gains a `clone_url` and R-11 is amended; decide before writing `ensure_clone`.
3. **The workspace (R-20).** This phase adds `research.workspace`: the root holding `repo.git/`, `worktrees/` and `clone.lock`, resolved **outside** worc's clone and defaulting to `~/.worc-connect/<owner>__<repo>/` from the `repo` pin. Assert it on resolved absolute paths so a configured value with `..` cannot walk back into worc's clone, and give `doctor` a line naming where it resolved. The run directories stay in `.worc-connect/` — worc runs no `git clean`, so nothing there needs moving. `fetch` belongs to the parent, once per tick; every clone-level operation takes `clone.lock` for its own duration and never for the length of a research.
4. Every `git` this package launches runs with terminal prompting disabled, so a private origin with no credential helper fails with a named error instead of hanging a detached child on a password prompt. `gh auth setup-git` is the documented prerequisite; the connector adds no credential of its own.
5. Every `git` launch is an argument list with an explicit `-C` or `--git-dir` naming the connector's own paths; `git` is resolved with `shutil.which`; stored paths go through `Path.as_posix()`.
6. `doctor` (from phase 01) grows the clone checks: present or creatable, fetchable, push URL disabled, worktree root writable, and a list of any orphaned worktrees it would sweep.
7. `keep_worktree: true` skips removal and says where the tree was left.

## Files touched

`src/worc_connect/research/worktree.py`, `src/worc_connect/cli.py` (doctor checks), `tests/`.

## Invariants in play

- No `git` process runs with worc's clone as its working directory, `-C` or `--git-dir`.
- Nothing under `.worc-connect/` is committed; the home stays gitignored.
- The research clone cannot push, by configuration and not by convention.

## Tests

- AC-R3 and AC-R13, against a real local git repository built in a temp directory (git is the subject here, so it is exercised, not faked — no network, no remote host).
- A killed run's leftover worktree is pruned and its directory removed at startup.
- Windows and POSIX path handling of the worktree root.

## Docs to sync in this phase

- `README.md`: what lands under `.worc-connect/` and that the home must stay gitignored.

## Acceptance for this phase

- [ ] A worktree can be created, used and removed without any process touching worc's clone — and without resolving to a path inside it (R-20).
- [ ] A `git push` attempt from a worktree fails.
- [ ] An orphaned worktree from a killed process is cleaned at the next startup.
- [ ] All gates green, both CI families.
