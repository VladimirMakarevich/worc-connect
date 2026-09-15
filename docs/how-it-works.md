# How it works

What actually happens between a maintainer applying a label and an issue closing itself. Read this when you want to predict the connector's behaviour rather than look a key up — for the keys, use the [configuration reference](configuration.md).

## The shape of the thing

```text
    tracker (GitHub)                 worc-connect                     worc
 ┌────────────────────┐        ┌────────────────────────┐      ┌──────────────────┐
 │ issue #142         │  read  │  list → gate → build   │write │ tasks/preparing/ │
 │  label: worc       ├───────►│  → promote → follow    ├─────►│ + worc promote   │
 │                    │        │                        │      │        ↓         │
 │  worc:queued       │◄───────┤  write-back:           │ read │  worc watch runs │
 │  worc:pr-open      │ write  │  one label, a comment  │◄─────┤  worc list --all │
 │  closed on merge   │        │  the close on merge    │      │        ↓         │
 └────────────────────┘        └────────────────────────┘      │  pull request    │
                                                               └──────────────────┘
```

Three properties fall out of that picture and explain most of the behaviour:

- **The connector is the only thing that talks to both sides.** worc never learns that a tracker exists; the tracker never learns that worc does.
- **It reaches worc through worc's own public surfaces only** — one write (a task file in `tasks/preparing/`, promoted with `worc promote`) and two reads (`worc list --format json --all`, and `worc --version` once per run). It never runs `git` in your clone and never reads anything under `.worc/`.
- **The issue is the state machine.** The label on the issue is the authoritative answer to "how far did this get"; the connector's own database is a cache of what the issue and worc already say.

## One tick, step by step

Every tick — a single `watch --once`, or one iteration of the loop — does the same seven things:

1. **List.** Ask the tracker for the repository's open items updated at or after the watermark. On the first tick there is no watermark, so it is every open item.
2. **Follow what is in flight.** The listing answers "what changed", and an item whose task is running is exactly an item nothing is changing. So every row still in flight is also read by its identifier, whether or not the listing mentioned it. A busy repository cannot push a running task out of sight.
3. **Gate.** For each listed item with no row yet, apply the rules: trigger label, author allow-list, or the explicit `allow_all`. The verdict is recorded with a reason from a closed vocabulary (`trigger-label`, `allowed-author`, `allow-all`, `no-trigger-label`, `author-not-allowed`) — never a label an item invented or an author's name.
4. **Build and stage.** For an admitted item: allocate the id (`gh-142`) and the branch, render the task file, and write it atomically into `<tasks_dir>/preparing/`.
5. **Promote.** Run `worc promote <task-id>`. That is worc's own atomic, refuse-to-overwrite ingress; from here the task is worc's.
6. **Reconcile.** For every row that already exists, re-derive its phase from the three sources that actually know: the lifecycle folders on disk, `worc list --format json --all`, and — once there is one — the pull request itself, read by number and recomputed from its live state.
7. **Write back.** Publish the state the row now implies: one label (the previous one removed), a comment when the state actually changed, and the close when the pull request merged. Then advance the watermark.

A tick with nothing new writes nothing at all. A tick in which the tracker is unreachable, throttled or refusing the login is logged, changes no state, and **the next tick is the retry** — no partial state, no backoff to configure.

## An item's lifecycle

| Phase (the connector's own) | The issue shows | What moves it on |
| --- | --- | --- |
| `gated` | — | The gate admitted it; the task file is next. |
| `staged` | — | The file is in `preparing/` but `promote` has not run or did not finish. Re-derived from disk next tick. |
| `queued` | `worc:queued` | worc has the task. |
| `running` | `worc:in-progress` | worc reports it running. |
| `pr-open` | `worc:pr-open` | The task opened a pull request; the URL is commented. |
| `done` | `worc:done` | The PR merged (and the issue closed), or the task finished without one. |
| `failed` | `worc:failed` | The task ended with no PR, the PR was closed unmerged, or worc's validation gate refused the task. |
| `researched` / `needs-info` / `declined` | — / `worc:needs-info` / `worc:declined` | Triage outcomes — see [triage](triage.md). |

The phases the operator never sees a label for (`gated`, `staged`, `researched`) are bookkeeping: a task file written but not yet promoted, or a triage report already acted on.

## Why a crash costs exactly one tick

The connector's database, `.worc-connect/state.db`, is **a cache and never the authority**. Every tick re-reads the truth: the lifecycle folders say whether a file was promoted, `worc list` says what the task is doing, the pull request says what it is, and the label on the issue says what has already been published.

That is what makes all of these safe and boring:

- **Delete `state.db`** — the next tick rebuilds every row from the tracker, worc and the folders.
- **Crash between writing the task file and promoting it** — the file is still in `preparing/`, the row is `staged`, and the next tick promotes it. The write is atomic (a temporary file, then `os.replace`), so a half-written file is never visible to a promote.
- **Restart the connector mid-run** — nothing is re-published, because the state already on the issue is read back before every write, and re-applying a label an issue already shows is a no-op.
- **Edit a label by hand** — the connector adopts what the issue shows rather than fighting it.

The one thing that is never recomputed is the task id. A row remembers which id it allocated, and an id is never reused: a re-trigger allocates the next sequence suffix (`gh-142`, then `gh-142.2`).

## What a re-trigger is, exactly

Removing the trigger label does **not** withdraw anything — from the moment it was promoted the task is worc's — but it does _arm_ the row. A trigger re-applied to an armed row whose task has nothing left in flight is a new request, and gets a fresh attempt at the next sequence number. A trigger put back while the task is still running disarms the row instead: a label cycled mid-run is a correction, not a queue for a second task the moment this one ends.

An attempt that ended by asking the reporter a question (`worc:needs-info`) needs no arming — the reporter answering is the new request, measured against the stamp the connector's own question left on the item.

A follow-up on an item whose previous pull request is still open continues that branch rather than opening a second one.

## The files the connector owns

Everything lives in `.worc-connect/`, a sibling of worc's `.worc/` and never inside it. `init` appends it to the clone's `.gitignore`.

| Path | What it is | Safe to delete? |
| --- | --- | --- |
| `config.yaml` | The only file you edit. | No — it is your configuration. |
| `state.db` | The SQLite cache: one row per item, plus the poll watermark. | **Yes** — costs one tick. |
| `connect.log` | One line per action: `item=… task=… action=… result=…`. Ids and URLs are the only item-derived values in it. Rotated at 5 MB, three files kept. | Yes. |
| `connect.pid` | Held while a `watch` daemon runs, removed on a clean exit. A second watcher in the same clone refuses to start while it exists. | Only after a crash — liveness is deliberately not guessed at. |
| `connect.stop` | The stop sentinel. Create it and the loop exits before its next tick and removes it. | Yes. |
| `triage/<task-id>/report.md` | Where the triage flow leaves its report, in a directory the connector owns. | Yes, once the attempt is over. |

## What can and cannot reach an agent

Issue text is untrusted input from strangers, and it has exactly **one** destination: the body of the task file, verbatim, under a provenance line. It is never an argument, an environment value, a log line, a label, or a comment. Comment bodies are templates this package wrote, carrying a task id, a status name and URLs, and they reach the tracker as _files_ rather than as command arguments.

The task file the connector writes cannot weaken worc either: its key set is a strict subset of worc's allowed keys and can never carry `extra_args`, a permission profile, a flow edit, or `auto_merge` unless you configured it. `gh` and `worc` are launched as argument lists resolved with `shutil.which`, never through a shell, and pinned with `--repo OWNER/REPO` from your configuration.

The one deliberate exception is the reason a triage verdict gives, which is published on the issue as the agent wrote it — see [triage](triage.md) for what that costs and why it is the trade that was chosen.
