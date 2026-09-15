# Troubleshooting

Symptom first, then what it means, then what to do about it. Two things are worth knowing before the tables: **a failing tick changes no state** — the next tick is the retry — and **a configuration problem never reaches the tracker**, because the loader refuses before a single call is made.

Every key named here is in the [configuration reference](configuration.md).

## Start here

```bash
worc-connect watch --once --dry-run    # what the tick sees and would decide; writes nothing
worc-connect status                    # what it currently believes about each item
tail -n 50 .worc-connect/connect.log   # one line per action
gh auth status                         # the connector's entire authentication story
worc --version                         # which contract the connector will use
```

A dry run is always safe: no task file, no state row, no log line, no tracker write.

## Exit codes

| Code | Meaning | Typical cause |
| --- | --- | --- |
| `0` | The run completed. | — |
| `1` | An infrastructure failure, or a refused start. | The tracker was unreachable during a single pass; another watcher holds the PID file; `init` found a configuration already there. |
| `2` | A usage or configuration problem. **Nothing was written and no tracker call was made.** | An unparseable or invalid configuration; a gate that admits nothing; a tracker with no adapter installed. |

## Nothing happens to my issue

| What you see | What it means | What to do |
| --- | --- | --- |
| The dry run lists the issue with `reason=no-trigger-label` | The label on it is not one of `gate.labels`. Matching is case-insensitive but exact otherwise. | Apply the configured trigger label, or add the label you actually use to `gate.labels`. |
| `reason=author-not-allowed` | `gate.authors` is non-empty and the item's author is not on it. An author the tracker cannot name is never admitted. | Add the author, or empty the list to let the labels decide alone. |
| The dry run lists nothing at all | The poll window found no changed items. On a first run there is no watermark, so this means the repository has no open items — or `repo` names the wrong one. | Check `repo: OWNER/REPO`, and `gh issue list --repo OWNER/REPO`. |
| The issue is listed but shows `action=skip` with a phase-looking reason | It already has a row: the connector took it on and is following it. | `worc-connect status` shows the phase; if it is terminal, a re-trigger is what you want. |
| The log says `result=state-label-without-task` | The issue carries a connector state label with no worc task behind it — usually a label applied by hand, or left over from a deleted home. | Remove that state label; the next tick can then take the issue on. |
| The dry run is fine, but a real tick does nothing | You are running the dry run only, or the real tick fails before write-back. | Run `worc-connect watch --once` and read stderr and the log. |

## It refuses to start

| Message | Why | Fix |
| --- | --- | --- |
| a message naming a configuration key, exit `2` | The loader rejects rather than repairs: an unknown key, a wrong type, or a `schema_version` this build does not understand. | Fix the key it named. A key this build does not declare is a typo, not an extension point. |
| the gate admits nothing, exit `2` | `labels: []`, `authors: []` and `allow_all: false` together. "Nothing configured" must never read as "every item". | Name a trigger label, an author list, or set `allow_all: true` deliberately. |
| `` `tracker` names `github`, for which no adapter is installed `` | The `[github]` extra was not installed. | `pipx install "worc-connect[github]"` — or reinstall with the extra. |
| `already exists; edit it instead` (from `init`, exit `1`) | A configuration is already there and `init` will not overwrite it. | Edit `.worc-connect/config.yaml`, or delete it yourself first. |
| `another watcher holds .worc-connect/connect.pid` | A `watch` daemon is running — or crashed and left the file. Liveness is deliberately not guessed at. | Make sure no watcher is running, then delete the PID file. |
| `--repo must name one repository as OWNER/REPO` | `init` got something that is not one repository. | Pass exactly `OWNER/REPO`. |

## The tracker side fails

All three are logged, change no state, and are retried on the next tick.

| In the log | Meaning | What to do |
| --- | --- | --- |
| `skipped-TrackerAuth` — the message ends `run gh auth login on this host` | `gh` is not logged in, or lost its credential. **This one will not fix itself.** | `gh auth login`, then `gh auth status` to confirm. |
| `skipped-TrackerRateLimited` | GitHub is throttling this login. | Raise `poll_interval_seconds`. A tick with nothing new is cheap, but a short interval on a busy repository is not free. |
| `skipped-TrackerUnavailable` | `gh` could not be launched, timed out, or answered with something unusable. The message names the subcommand and `gh`'s own first line of complaint. | Check that `gh` is on `PATH` and the network is up. A single pass exits `1`; the daemon simply retries. |
| A permissions error from `gh` | The login lacks write access to the repository. | The connector can only do what you can: it needs to label, comment and close. |

## The worc side fails

| Symptom | Meaning | What to do |
| --- | --- | --- |
| The issue is never labelled `worc:queued`, and `worc list` does not show the task | `worc promote` did not happen or did not succeed. | Check that `worc.command` resolves on `PATH` and `worc.repo_path` is the clone worc runs in. The task file may still be sitting in `<tasks_dir>/preparing/` — the next tick promotes it. |
| The task file is in `preparing/` and stays there | The promote failed, or worc's `paths.tasks_dir` is not what the connector was told. | Restate worc's `paths.tasks_dir` under `worc:` in the connector's configuration. |
| `worc:failed` immediately, the comment naming a refusal | worc's validation gate refused the generated task. | With worc 0.14.0a1+ the comment carries worc's own reason. A too-large body is truncated by the connector, so the usual cause is a `validation.max_*` in worc that differs from the value under `worc:` here. |
| `worc:queued` forever | Nothing is running worc's queue. | Start `worc watch` in the same clone. |
| The issue stays `worc:pr-open` after the PR merged | The connector has not ticked since, or cannot read the pull request. | Wait one interval, or run `worc-connect watch --once`. The state is recomputed from the pull request every tick. |
| The PR was merged with a squash and the branch deleted, and the issue did not close | Against a worc older than 0.14.0a1 the pull request is found by branch name, and a deleted branch can no longer be found. | Upgrade to worc 0.14.0a1 or newer, where the connector reads the PR by the number in the URL worc recorded. |
| No `Fixes #<n>` in the pull request body | The installed worc predates the `references:` key, so the connector does not emit it — an unknown front-matter key is a hard reject at worc's gate. | Upgrade worc, or leave `close_on_merge: true` so the connector closes the issue itself. |

## Triage behaves oddly

| Symptom | Meaning | What to do |
| --- | --- | --- |
| `install-flow` says `research.mode` is not `worc` | The step is off, so nothing would use the flow. | Set `research.mode: "worc"` — quoted; YAML reads a bare `off` as `false`. |
| `install-flow` says the worc in this clone predates `flow.report_dir` | worc would refuse the flow at load; installing it would fail later, as a task. | Upgrade worc to 0.14.0a1 or newer. |
| `install-flow` leaves files alone and exits `1` | Those files differ from the ones this package ships — you edited them. | Keep your copy, or re-run with `--force` to replace it. |
| `worc:failed` with a comment naming a report path | The triage task produced no report, or one with no readable verdict block. The connector fails closed rather than guessing from the prose. | Read the report at that path; `worc status <task-id>` says what the triage run did. |
| `worc:needs-info` never re-triggers after the reporter replied | The reply has to move the item's update stamp past the connector's own question. The connector's label and comment never count as the reply. | Confirm the reporter commented after the question, and that the gate still admits the item. |

See [triage](triage.md) for what the step does and what it costs.

## Recovering

- **Delete `.worc-connect/state.db`** — it is a cache. The next tick rebuilds every row from the tracker, `worc list` and the lifecycle folders. Safe at any time.
- **Re-run an item from scratch** — remove the trigger label and put it back once its task has nothing left in flight. That is a new attempt with the next sequence suffix (`gh-142.2`); an id is never reused. A label cycled _while the task runs_ is treated as a correction and starts nothing.
- **A failed task** is recovered on the host with `worc status <task-id>` and `worc rerun` — never by the connector, which will not re-queue or edit a task on its own.
- **A wrong label published** can be removed or changed by hand; the connector reads the issue's state back before every write and adopts what it finds rather than fighting it.

If none of this explains it, the log is the record of what the connector actually did — every action, with the item id, the task id and the result. Day-to-day running is covered by the operations guide.
