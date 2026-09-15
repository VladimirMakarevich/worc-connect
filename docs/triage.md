# Triage — the optional analysis step

Off by default. With it on, a gated item does **not** become an implementation task straight away: it becomes a worc _triage_ task first, which scopes the report, analyses the repository read-only, tries to reproduce the behaviour, and writes one report. The connector reads that report and decides what happens next.

This page is what the step does, what it costs, and how to switch it on and off. The keys are in the [configuration reference](configuration.md); when something goes wrong with it, [troubleshooting](troubleshooting.md) has the symptoms.

## What you get for it, and what it costs

**The cost is one extra agent run per gated item**, before any code is written. That is the whole trade:

- an item nobody can act on — a duplicate, a question, something outside what the project does — costs an analysis instead of a full implementation run, and never reaches the code at all;
- an item that _is_ actionable arrives at the implementation task with acceptance criteria and a reproduction attached, rather than with whatever the reporter happened to write.

The triage task runs at `priority: high`, so it does not queue behind the long runs it exists to decide the shape of.

## Turning it on

Two steps, in this order.

```yaml
# .worc-connect/config.yaml
research:
  mode: "worc" # "off" | "worc"
  flow: issue_triage
```

**Quote the value.** YAML reads a bare `off` as the boolean `false`, not as this mode's name.

```bash
worc-connect install-flow
```

That copies the flow and its five role prompts out of this package into worc's `.worc/flows/`. It is the only thing the connector ever writes inside worc's home, and only on this explicit command.

`install-flow` refuses before writing anything if the step is not on, if `research.flow` names a flow this package does not ship (a flow of your own is yours to install), or if the worc in the clone predates the `flow.report_dir` key the flow declares and would refuse it at load. A file identical to the shipped one is rewritten silently; a file **you** have edited is left alone and reported, so re-running the command is a safe way to pick up a newer flow. `--force` replaces your edits.

**Check:** `.worc/flows/issue_triage.yaml` and its prompt directory exist, and `worc-connect watch --once --dry-run` still exits `0`.

## What the connector does with the report

The triage task leaves its report at `.worc-connect/triage/<task-id>/report.md` — the flow's declared `report_dir`, inside the connector's own gitignored home, never under `.worc/`. The connector reads one machine-readable verdict block at the end of it:

| The report's verdict | What the connector does | The issue shows |
| --- | --- | --- |
| `actionable` | Builds the implementation task from the report — its reason as the description, its acceptance criteria, and the failing test it arrived at — and queues it as the next id (`gh-142` triaged → `gh-142.2` implemented). From there it is an ordinary task. | `worc:queued`, then the usual progression |
| `needs-info` | Comments the question and stops. The reporter replying re-runs triage with the next id; the connector's own label and comment never count as the reply. | `worc:needs-info` |
| `duplicate` | Comments what it duplicates. No implementation task is created. | `worc:declined` |
| `declined` | Comments why. No implementation task is created. | `worc:declined` |

A report that is **missing**, that carries no verdict block, or whose verdict is not one of those four ends the attempt at `worc:failed`, with a comment naming the path the connector looked at. There is no second location to look in, and no guess is made from the prose: a report the parser cannot read produces no task at all.

That is the invariant worth remembering — **the model proposes, the connector decides**. Nothing the agent wrote becomes a task, a label or a state without passing through a parser whose vocabulary is closed and a builder that chooses what to carry over. The same builder produces the implementation task on both paths, so what an implementation task looks like never depends on which route it took.

## What the flow may and may not do

The flow ships inside this package and is judged by worc's own validator and sandbox exactly like any operator flow, so it can raise no permission ceiling:

- it declares `publishing: none` — it **publishes nothing** and can open no pull request;
- it grants no network access;
- every node but the reproduction one is read-only, and that one node's writes are confined to `.worc-connect/triage/<task-id>/` — gitignored, and the directory this connector owns.

## The one thing published as an agent wrote it

The report's `reason` is posted on the issue **verbatim**, in the `needs-info` and `declined` comments.

That is a deliberate choice with a real cost: a stranger's issue text can, through the agent that read it, steer the text of a comment posted under your account. It was chosen because the person who has to act on a `needs-info` question is the reporter reading the issue, and a paraphrase would be a question nobody can answer. The cost is recorded where it is paid, in `docs/backlog/follow-ups.md`.

Everything else the connector comments is a template this package wrote, carrying a task id, a status name and URLs.

## Turning it off again

Set `research.mode: "off"`. The next tick sends gated items straight to an implementation task.

Nothing is cleaned up for you, and nothing needs to be: the flow stays in `.worc/flows/` unused (delete it if you like), the reports stay under `.worc-connect/triage/`, and any triage task already in flight runs to its end and is acted on as usual. The two triage labels stay on the issues that have them — the connector created them, but it never deletes a label.
