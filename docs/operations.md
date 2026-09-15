# Operating it

Running the connector for real, day to day: how to keep it ticking, how to stop it, what to keep an eye on, and what to do when a task ends badly. The first-time path is the getting-started guide; every key mentioned here is in the [configuration reference](configuration.md).

## Choose how it ticks

There are two supported ways to run it, and the difference is only who owns the interval.

**The loop** — the connector polls until you stop it:

```bash
worc-connect watch
```

It holds `.worc-connect/connect.pid` while it runs, so a second watcher in the same clone refuses to start rather than racing the first. It waits `poll_interval_seconds` between ticks (default 300) and checks the stop sentinel about once a second during that wait.

**A single pass, on your scheduler** — cron, a systemd timer, Task Scheduler:

```bash
worc-connect watch --once
```

This writes no PID file, so overlapping runs are your scheduler's problem to prevent; give it an interval comfortably longer than a tick takes. Use this when you already have a scheduler you trust, or when you want the connector to run only during working hours.

Either way, **`worc watch` has to be running in the same clone** — the connector queues tasks, worc runs them. A connector ticking with no worc behind it will happily promote tasks that nothing picks up.

## Stopping it

Stopping is a file, not a signal, and it behaves identically on Windows and POSIX:

```bash
: > .worc-connect/connect.stop        # POSIX
```

```powershell
New-Item -ItemType File .worc-connect/connect.stop   # PowerShell
```

The loop notices within about a second, finishes what it is doing, exits before starting another tick, and removes the sentinel itself. Ctrl-C works too; the sentinel exists so that _another_ process, or a scheduled job, can stop a watcher it does not own.

A PID file left behind by a crash blocks the next start, by design — liveness is deliberately not probed, because the alternative is a heuristic that eventually decides a _running_ watcher is dead and starts a second one against the same clone. The refusal names the path: delete `.worc-connect/connect.pid` once you are sure no watcher is running.

## Watching it work

```bash
worc-connect status
```

Prints the repository and home it is using, the poll watermark, how the last tick ended, and one line per item it has taken on — identifiers, phase, task id, branch and pull request, and nothing an item wrote. It opens the database read-only, so it cannot create or change anything.

`.worc-connect/connect.log` is the running account: one line per action, `item=… task=… action=… result=…`. It carries ids and URLs only — never issue text, never a diff, never anything read out of worc's home. It rotates at 5 MB with three older files kept, so a daemon on a busy repository never grows it without end.

Lines worth recognising:

| Line | What it means |
| --- | --- |
| `action=list result=skipped-TrackerUnavailable` | The tracker could not be reached this tick. No state changed; the next tick is the retry. |
| `action=list result=skipped-TrackerAuth` | `gh` is not logged in — this one will not fix itself. |
| `action=list result=skipped-TrackerRateLimited` | You are being throttled. Raise `poll_interval_seconds`. |
| `action=fetch result=skipped-…` | One in-flight item could not be read by identifier this tick; its row waits a tick. |
| `result=state-label-without-task` | An issue carries a connector state label with no worc task behind it. It is left alone — remove the label to let the connector take that issue on. |
| `action=promote result=…` | The handoff into worc, per task. |

## Running against more than one repository

One connector home watches one repository. For a second, give it its own home:

```bash
worc-connect init --repo OWNER/OTHER --home /path/to/other-clone/.worc-connect
worc-connect watch --home /path/to/other-clone/.worc-connect
```

Every command takes `--home`. Each home has its own configuration, cache, log and PID file, so two watchers never share state — but remember that each still needs a clone with a `worc watch` in it.

## When a task fails

`worc:failed` on an issue means one of three things, and the comment says which: worc ended the task without opening a pull request, the pull request was closed without being merged, or worc's validation gate refused the task outright.

**The recovery is yours and it happens on the host.** The connector never re-queues, edits or reruns a task on its own, and it never copies worc's logs or diffs onto a public issue.

```bash
worc status <task-id>     # what worc recorded — the logs stay on the host
worc rerun <task-id>      # if it is worth another attempt
```

If the run needs to start over from the issue instead, take the trigger label off and put it back once the task has nothing left in flight. That arms and then fires a fresh attempt, which gets the next sequence suffix (`gh-142.2`) — an id is never reused. A pull request closed unmerged and then reopened needs nothing at all: the next tick recomputes the state from the request itself and the issue returns to `worc:pr-open`.

## Upgrades and moving parts

**Upgrading the connector.** Stop the watcher, upgrade, start it again. A `state.db` written by a schema the new build does not recognise is discarded rather than migrated — rebuilding it costs one tick.

**Upgrading worc.** The connector asks `worc --version` once per run and adapts. Crossing 0.14.0a1 upward gains you three things with no configuration change: `Fixes #<n>` in the pull request body, the pull request found by the URL worc recorded rather than by branch name, and the reason behind a refused task reported on the issue.

**Changing worc's own configuration.** If you change `paths.tasks_dir` or any `validation.max_*` in worc, restate the value under `worc:` in the connector's configuration. The connector never opens worc's `config.yaml`, so a mismatch surfaces as a task worc never queues or a file its gate quarantines.

**Changing the gate.** Edits take effect on the next tick. Widening it means the next tick may take on every item that now matches — run `watch --once --dry-run` after the edit and read the plan before letting the loop act on it. Narrowing it withdraws nothing: a task already promoted is worc's.

**Changing `labels_prefix`.** The connector creates the new labels on its next real tick and starts publishing under them, but it does not migrate issues already labelled under the old prefix — those keep a label nothing owns any more. Change it before you start, or clean up by hand.

## Housekeeping

- **`state.db` is disposable.** Delete it whenever you like; the next tick rebuilds every row from the tracker, `worc list` and the lifecycle folders.
- **The home is gitignored** by `init`. If you moved the home or rewrote `.gitignore`, make sure it still is — the cache holds issue text.
- **Nothing here needs a credential.** The connector stores no token; `gh auth status` is the whole of its authentication story. Rotating your `gh` login needs no change on this side.
- **Cost.** A tick with nothing new makes no model call and writes nothing. What costs money is worc running the tasks you gated — one agent run per gated item, and two with the [triage step](triage.md) on.

When something does not happen at all, work through [troubleshooting](troubleshooting.md).
