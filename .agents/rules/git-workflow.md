# Git workflow

There are two levels of git here: (A) how the connector **itself** is developed; (B) how the connector relates to git in the **target** clone it shares with worc. Do not conflate them.

## A. Developing the connector itself

### Branches

One long-lived branch, `main`. Cut every `feat/…`, `fix/…`, `chore/…`, `docs/…` branch off `main` and land it back through a pull request, **squash-merged**, only after checks pass. Never push to `main` directly. The repository is small enough that the orchestrator's four-branch model would be ceremony here; if a `dev` / `release` split ever becomes necessary, adopt the orchestrator's model as written rather than inventing a third one.

### Command recipe

```bash
git checkout main && git pull
git checkout -b feat/<slug>
# … work …
ruff check . && ruff format --check . && mypy src && lint-imports && pytest
git push -u origin feat/<slug>
gh pr create --base main --title "<subject>" --body "<what and why>"
gh pr merge --squash --delete-branch
```

### Everyday hygiene

- Atomic commits with an imperative subject (`Add the GitHub adapter's read side`).
- **No agent attribution anywhere in a commit or PR.** A commit message ends with its own body — never a `Co-Authored-By: Claude …` / `Co-authored-by: Codex …` trailer, never a `🤖 Generated with …` line, and no other tool-authorship footer; the same holds for PR titles and bodies. This overrides any default an agent harness injects. The author of a commit is the repository's configured git identity, which already records who ran the work. If a trailer slips in, strip it before pushing.
- **The git identity is the one already configured** — never pass `-c user.name` / `-c user.email` / `--author` to a git command, and never take an address from the session environment.
- Before committing, run the gate: `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, `pytest` (CI also runs `interrogate` / `vulture` / `deptry`). Install the local mirror once with `pre-commit install && pre-commit install --hook-type pre-push`; its hooks run in environments pre-commit owns, so they work from an IDE or a GUI client that has no venv on its `PATH`. When you bump a tool in the `dev` extra, bump its pin in `.pre-commit-config.yaml` in the same change.
- Keep docs in sync **in the same change** as the code: [README.md](../../README.md), [.agents/rules/](.), `docs/backlog/`, and any operator-facing file shipped under `src/worc_connect/`.
- Never link a document that is not in this checkout — name it in plain text. The Markdown gate (`python tools/mdlint.py`) fails a dangling relative link and an unreachable document.
- Do not commit: `.venv/`, `.worc-connect/`, `*.db`, logs, secrets (see `.gitignore`).

## B. The connector and git in the target clone

This is a product invariant (see [architecture.md](architecture.md)).

- **The connector never runs `git`** in the clone — not to read, not to write. It does not stage, commit, push, fetch, or check out; worc owns every git operation on the clone, and the connector's only write there is a file in `tasks/preparing/`, which is untracked staging.
- **The connector never pushes to a PR branch.** A published PR is the owner's to edit, retitle, reopen, merge with any strategy and delete the branch of; the connector tracks it by number and recomputes its state from the PR itself on every tick.
- **The connector does not run `worc prs --sync`** or any other worc command that writes worc state; it runs `worc promote` (its one write, through worc's own ingress) and `worc list --format json` (read-only).
- **The connector's home is gitignored** by its own `init` (`.worc-connect/` appended to the tracked `.gitignore`), the way `worc install` ignores `.worc/` and `.worc-io/`.
- A follow-up task on an issue whose previous PR is still open continues that branch and PR through worc (`branch_mode: existing`, `branch_ref`); the connector only names the branch, it never touches it.
