---
name: handoff
description: Assemble a HANDOFF PROMPT — one self-contained prompt from which a fresh session continues the current task without ever seeing this chat. Reads the repository (`git status`, staged and unstaged diffs, recent commits, the touched files) and treats the files as the truth rather than promises made in conversation; keeps what lives only in the chat (decisions and their reasons, the user's requirements, rejected alternatives, open work) and leaves out what the new session can read for itself. It continues nothing and changes no files: the reply is the finished prompt and nothing else. Use for "handoff prompt", "prepare a prompt for a new session", "I'm running out of context, hand the work over", "record the state of the task", "let's continue in another chat", and the same in Russian ("сделай handoff", "передай работу в новую сессию").
---

# handoff

Packs the current work — **one task**, the one being worked on right now — into a single prompt the user copies wholesale into a new chat.

Two principles decide what goes in:

- **The new session will not see this conversation, but it will see the repository.** So do not retell the discussion; explain the state of the files: what is in them, why they look that way, what to do next. Anything readable from the repository is only named. Anything that lives only in the chat — decisions, reasons, rejected alternatives, the user's requirements — is written out in full, because otherwise it is lost.
- **The files are the source of truth.** Where a plan from the conversation disagrees with the disk, the disk goes into the handoff and the discrepancy is called out.

Keep the prompt short. A line the new session could have got from `git diff` is a line spent on nothing — and one more line it can misread.

## How it works

1. **Identify the task.** If several unrelated tasks ran in this session, hand over the one worked on last and say so in one line before assembling. If it is unclear which one is current — ask first.
2. **Read the repository state** — read it, don't recall it. All read-only; if a command fails, say so in the prompt instead of filling the gap with a guess.

| Command | What it gives |
| --- | --- |
| `git branch --show-current` | The branch the work lives on — every `feat/…` / `fix/…` / `chore/…` / `docs/…` branch is cut from `main` |
| `git status --short` | All uncommitted work, including untracked files |
| `git diff --stat` and `git diff --stat --staged` | The size of the edits. **Staged must be inspected** — part of the work is often already indexed |
| `git diff` on the files that matter | What actually changed where it affects how the work continues |
| `git log --oneline -15` and `git log --oneline origin/main..HEAD` | Where the state came from; what this branch already committed and must not redo |
| `git stash list` | Shelved work the new session would otherwise never learn about |

3. **Read the touched files themselves**, plus the spec folder the task implements (`docs/backlog/<slug>/` — its `design.md`, and the phase document under `plan/`), the current `TodoWrite` list, and the PR description if one exists (`gh pr view`). A diff hunk shows what changed, not whether the thought is finished.
4. **Reconcile the session's agreements against the files.** Confirmed → a fact. Diverging → a discrepancy, stated as such. Not verifiable from the repository → tagged as needing a check.
5. **Fill in the template.** Keep the headings and their order; never drop a section — write "none" or "unknown" instead.
6. **Emit the prompt** and nothing else (see [Output](#output)).

## What to keep, what to leave out

Keep — this is what a context switch destroys first and what is hardest to reconstruct:

- **The user's requirements, especially the small ones**: output format, checkpoints, a ban on some action, limits, language, "ask me first". One "show me the plan before each stage" outweighs half the architectural decisions.
- **Rejected alternatives, and the reasons behind non-obvious decisions.** Without them the new session earnestly re-proposes what was turned down, or redoes a decision that looks accidental.
- **The boundaries of the task** — what was deliberately left out, so the work does not widen.

Leave out:

- Chunks of code or text. Give the path and the place in it; a short excerpt only when the item is incomprehensible without one.
- References to invisible history — "as we discussed", "as I already did". The new session saw none of it.
- Internal reasoning, weighed options, drafts, and the chronology of the session for its own sake.
- Guesses about anything neither in the repository nor stated by the user. No information — say so.

**Mark what is unverified.** Everything in the prompt reads as a checked fact unless it carries `needs checking:`. Never write something as a fact when it is merely likely — that mixture is exactly how the new session takes an assumption for a fact.

## Template

`TASK` … `FILES` are filled in for the task; `START HERE` is carried over word for word.

Headings in English, exactly as below — the new session recognizes them. The body in the user's language (default: the language of this chat, per [AGENTS.md](../../../AGENTS.md)). Paths exact, relative to the repository root, forward slashes, plain text — the new session feeds them to tools. Names verbatim: files, functions, config keys, connector states, task front-matter keys, commands, branches, hashes. Lists short, one fact per item.

```
# TASK

<What is being solved and which part of the project it lives in. 2–4 sentences, no discussion history.>

# GOAL

<The full end goal, not the last step taken, and what counts as done. If the task implements a spec folder, name its documents instead of restating them. If the goal has phases, name them and say which one the work is on.>

# STATE

Branch: <branch> (cut from <base>)
Last commit: <hash> <subject>
Uncommitted: <N staged, N unstaged, N untracked>
Stash: <no / what is in it>

<The significant changed and created files: path — what changed in it and why. Task work only; runtime residue is not work.>

<If the files diverge from earlier plans, say so plainly and state what is true now.>

Done:
<Finished and confirmed by a file. Each item names the file or the place where it can be seen.>

In progress:
<For each item: what already exists, what is missing, exactly where it stopped.>

Todo:
<Not started, in the order it makes sense to do it.>

# DECISIONS

<Decisions that must not be reversed by accident: architecture, data model, naming, behavior, file layout, constraints, deliberately rejected alternatives — each with a short reason. If a hard invariant is involved, name it and say how the current code satisfies it.>

# CONSTRAINTS

<Requirements that must keep holding — and separately and explicitly, the user's own: format, checkpoints, bans, limits, language, "ask first".>

<The project rules this task is bound by, as paths to the rule files.>

# RISKS

<Known bugs, failing checks, temporary workarounds, potential regressions, doubtful places. Tag the unverified ones `needs checking:`.>

<Which checks were run and what they returned. If none were run, say so plainly.>

# FILES

<Path — role in the task. Grouped: rules, task source of truth, working files, tests, config.>

# START HERE

This handoff names files instead of quoting them, and the repository outranks it: where they disagree, the repository is right and the plan adapts. Do not start implementing, even if the next step looks obvious.

1. Read the files above; check `git status` and `git diff`, staged and unstaged; use recent history if the current state needs explaining.
2. Reconcile this handoff against the repository — which Done / In progress / Todo items really match it.
3. Report back in this format, then STOP and wait for a separate instruction from the user to continue:

   **What I understood** — the end goal, 2–5 sentences.
   **Current state** — what is done, what is partly done, what is left.
   **Discrepancies with the handoff** — or "none" explicitly.
   **Plan for the first stage** — where you intend to start.

4. Until that instruction arrives, read-only actions only: no edits, no new or deleted files, no autofixes, no migrations, no commits.

Once the user allows you to continue:

- Investigate the existing implementation first and follow the patterns already in the project.
- Do not revert or rewrite existing changes just because their origin is unclear.
- Do not redo a Todo item that turns out to be already done in the current state.
- Do not ask about what the repository can answer.
- Preserve behavior outside the task; run the relevant tests and checks after changing code.
- Keep working until the GOAL is reached or an objective blocker is hit.
```

## Specifics of this repository

- **One long-lived branch, `main`.** State the branch and that it was cut from `main`; every document the new session needs is in the checkout — there is no second documentation branch here. Never push to `main` directly. See [git-workflow.md](../../../.agents/rules/git-workflow.md) §A.
- **Do not restate the rules or the architecture document** — both live in the repository and change. `CONSTRAINTS` carries paths: [AGENTS.md](../../../AGENTS.md), the [.agents/rules/](../../../.agents/rules/architecture.md) files this task actually depends on, and the spec folder it implements ([docs/backlog/README.md](../../../docs/backlog/README.md)). The new session reads them itself.
- **Name the invariant the task brushes against** — the core knowing no tracker, the narrow worc boundary (in: a file in `tasks/preparing/` plus `worc promote`; out: `worc list --format json` and `worc --version`, nothing else, never `.worc/`, never `git`), no agent runtime, the deterministic builder deciding what a model only proposes, issue text with exactly one destination, the fail-closed gate, no credentials, argv instead of a shell string, one task per item with no reused id, cross-platform — and how the current code satisfies it. That is the knowledge lost first, and violating it fails silently.
- **Untracked runtime residue is not task work.** A working checkout shows `.worc-connect/`, `.worc/`, `tasks/`, `*.db`, `.venv/` and the tool caches in `git status`; do not list them unless one really is part of the task.
- **The state of the gates is a fact worth recording.** Which of `ruff check .`, `ruff format --check .`, `mypy src`, `lint-imports`, `python tools/size_gate.py`, `pytest`, `python tools/mdlint.py` actually ran in this session and what they returned — see [/run-checks](../run-checks/SKILL.md). "Not run" is said plainly; never imply green. An unset `WASTECH_MDLINT_HOME` means the Markdown hook passed without checking anything — that too is worth one line.
- **Docs are half of Done.** If behavior, the CLI, config, or the architecture changed, say whether the docs were updated in the same change — [README.md](../../../README.md), `docs/configuration.md`, the rules, `docs/backlog/`, and above all the shipped operator-facing files under `src/worc_connect/packaged/`, the half forgotten most often. Carry a drafted doc-impact note over verbatim.
- **Uncommitted work is normal — but explain it.** Say whether the commit is deliberately withheld until the checks are green or until the user confirms, or the new session will "tidy up" the index or commit early. Staging here is always scoped to explicit paths, never `git add .`.
- **Carry over what earlier phases already settled** into `DECISIONS` instead of leaving it to be re-derived: the phase [/implement](../implement/SKILL.md) reached, the scope agreed with the user, and the approaches weighed and rejected along the way.

## Output

- The final message contains **only the prompt** — not a line before, not a line after. Anything you need to say in your own voice (which of several tasks is being handed over, for instance) is said before assembling.
- The prompt is emitted as one block in four backticks, so inner blocks and commands do not break the markup and the whole thing copies in one go.
- No file is created. If the user asks to save the handoff, write it to the path they name or to the session scratchpad — not under `docs/`, which is the linted task queue, not a place for session notes.

## Common mistakes

- **Retelling the chat** instead of explaining why the repository looks the way it does.
- **Forgetting `--staged`** — that part of the work then lands in `Todo` as undone.
- **Putting a promise into `Done`.** Done means confirmed by a file.
- **Losing a small user requirement.** It disappears first and costs the most.
- **Smoothing over doubt.** "Seems done" becomes a fact inside a prompt.
- **Starting the task.** Assemble the prompt, and that is all.
