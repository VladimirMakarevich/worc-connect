---
name: run-checks
description: Run all quality checks for worc-connect (ruff, mypy, import-linter, interrogate, vulture, deptry, pytest, the Markdown gate) and report briefly. Use before a commit, before a PR, and before moving to the next implementation phase.
---

# run-checks

Run the full set of checks for the worc-connect repository.

## Steps

1. Make sure the environment is installed (`pip install -e ".[dev]"`, plus `pip install -r requirements-worc.txt` for the suite that runs a generated task file through worc's real validation gate); if dependencies are missing, install them.
2. Run the following in order and collect the result of each command:
   ```bash
   ruff check .
   ruff format --check .
   mypy src
   lint-imports            # architectural import-boundary contracts (.importlinter)
   python tools/size_gate.py   # module line budgets ([tool.size_gate.max_lines]) — the fix is a split
   interrogate src         # docstring-coverage floor
   vulture                 # dead code
   deptry src              # dependency hygiene
   pytest
   python tools/mdlint.py  # Markdown gate (skips with a note when the linter is not available)
   ```
3. If something fails:
   - show the specific errors (file:line) and a brief cause;
   - propose or apply a minimal fix;
   - re-run only the relevant check.
4. At the end, give a short summary: what passed and what did not. Do not declare "all green" if even a single check failed.

## Rules

- Do not disable linter/type-checking rules just to get a "green" run — fix the cause.
- Never raise a line budget or a complexity threshold to pass the size gate or a `PLR` ratchet — split the module or the function.
- Do not commit while checks are red (see .agents/rules/testing.md, git-workflow.md).
