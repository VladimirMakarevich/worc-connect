# Out of scope — Local research runtime

Scope fence for [requirements.md](requirements.md). This record covers **who produces the research report and where that agent runs**. Everything else about the connector stays as the [tracker-connector](../tracker-connector/README.md) record defines it.

## Not in this task

- **Replacing the worc-side triage path** — tracker-connector phase 07 stays, is implemented first, and remains the default (`research.mode: worc`). This record adds a second provider behind the same seam.
- **The report's content and the four verdicts** — defined by phase 07 and reused unchanged (FR-R10). If the local runs show the report needs a different shape, that is a change to phase 07's contract, made in that folder, for both providers at once.
- **A sandbox, a container, a permission profile or a diff gate for the research agent** — explicitly excluded by the operator (D12). The agent runs with the operator's own CLI permissions.
- **The agent writing code, a branch, a commit or a pull request** — the research deliverable is a report. The worktree is disposable and the clone cannot push (FR-R13). Implementation stays worc's job, which is the whole reason the connector hands it a task.
- **Model choice, token budgets or cost accounting per item** — the agent CLI's own configuration decides; the connector passes constant flags from the operator's configuration and counts nothing.
- **Driving the agent interactively, or a HITL round-trip mid-research** — the child is non-interactive; a question for the author is a `needs-info` verdict, which is phase 07's round-trip.
- **Research before the gate** — the gate stays the perimeter and runs first, always. An ungated item never reaches an agent.
- **Multi-repository research** — one connector instance, one tracker repository, one research clone, as everywhere else in this connector.
- **A worc-side queue lane or a second worc instance** — the alternatives weighed in the design conversation of 2026-09-11 and not chosen; recorded in [questions.md](questions.md) so they are not re-proposed as new ideas.

## Deferred (maybe later, not now)

- **Automatic retry of a failed research** — `on_failure` is `proceed` or `fail`; a `retry` policy with an attempt cap and a backoff is a later refinement once real failure rates are known.
- **Deterministic, model-free evidence collection** (stack-trace to file candidates, `git log -S`, duplicate search) as a cheap pre-step that runs with `research.mode: off` — genuinely useful and independent of any runtime, but a separate item.
- **Attaching the report to the tracker item** — a summary in the comment is enough; the full report stays in the connector's home, as in phase 07.
- **Reusing one warm worktree across researches** — a fresh detached tree per attempt is simpler and the cost is a `git worktree add` over a local clone.
- **A third agent profile or an operator-defined profile library** — the table form already covers any CLI; shipping more named profiles waits for demand.

## Explicit non-goals

- We will **not** let a research result reach worc without passing through the deterministic builder.
- We will **not** put item text into an argv, an environment variable, a log line, a label or a comment — the invariant that survives this record intact.
- We will **not** let the research path write anywhere in worc's clone, run `git` there, or read `.worc/`.
- We will **not** make research mandatory: `research.mode: off` stays the default, and every line of this runtime is unreachable behind it.
