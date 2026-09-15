# Getting started

The whole path, in order, from an empty machine to an issue that closed itself. Every step ends with something you can check, so you never move on wondering whether the last one worked.

Budget about twenty minutes for steps 1–6, plus however long your first task takes worc to run.

> Nothing in steps 1–5 touches your repository. The first command that writes anything to the tracker is step 7.

## Step 0 — what you need first

| What | Which | Check it with |
| --- | --- | --- |
| Python | 3.12 or newer | `python --version` |
| A clone of the repository | the **same** one `worc watch` runs in | `ls .worc` — worc's home should be there |
| `worc` | 0.14.0a1 or newer, on `PATH` | `worc --version` |
| `gh` | logged in as the operator who will run the connector | `gh auth status` |

Two of these are worth a sentence each.

**The clone.** The connector and worc share one working copy. The connector writes a task file into worc's `tasks/preparing/` and promotes it; worc's own watcher picks it up from there. If worc runs somewhere else, the connector has nothing to hand the task to.

**The login.** Every tracker call is `gh`, running as you. The connector stores, logs and forwards no token, and it forwards no extra environment to `gh`. Whatever you can do on that repository, it can do; what you cannot, it cannot. You need at least write access — it applies labels, comments, and closes issues.

**An older worc is allowed.** Below 0.14.0a1 the connector drops three conveniences and tells you nothing else changed: no `Fixes #<n>` in the pull request, the pull request is found by branch name rather than by the URL worc recorded, and a task refused by worc's validation gate is reported without the reason. See "Which worc you are running" in the [configuration reference](configuration.md).

## Step 1 — install the connector

```bash
pipx install "git+https://github.com/VladimirMakarevich/worc-connect.git#egg=worc-connect[github]"
```

Once the first release exists, `pipx install "worc-connect[github]"` will do. The `[github]` extra is what registers the GitHub adapter; without it the connector starts and then refuses your configuration, naming the extra to install.

**Check:** `worc-connect --version` prints a version, and `worc-connect --help` lists `init`, `watch`, `status` and `install-flow`.

## Step 2 — create the connector's home

```bash
cd /path/to/your/clone
worc-connect init --repo OWNER/REPO
```

`init` is entirely offline: it writes `.worc-connect/config.yaml`, appends `.worc-connect/` to the clone's `.gitignore`, and makes no tracker call at all. It refuses to overwrite a configuration that already exists — delete it yourself if that is what you meant.

**Check:** `.worc-connect/config.yaml` exists and `git status` shows the connector's home is ignored.

## Step 3 — decide who may trigger a run

This is the one setting you must not skim. Open `.worc-connect/config.yaml` and look at the `gate` block:

```yaml
gate:
  labels: [worc] # trigger label(s); an item needs at least one
  authors: [] # optional allow-list; empty means the labels alone decide
  allow_all: false # true, explicitly, to accept every item of the repository
```

The gate is the only thing between a stranger's issue text and an agent with write access to your repository, and it fails closed: an item no rule admits never becomes a task, and a configuration stating no rule at all **refuses to start**.

Both rules are filters applied in sequence. With `labels: [worc]` alone, anyone who can apply that label can start a run — on GitHub that is your maintainers, because applying a label needs write access. Add `authors: [alice, bob]` and an item needs the label _and_ an author on the list.

`allow_all: true` accepts every open item in the repository. It exists for a private repository where every reporter is already trusted; on anything a stranger can open an issue on, it is the wrong answer.

**Check:** `labels` names a label your maintainers will actually apply. Do not create it by hand — the connector creates the labels the repository is missing (the trigger labels included) on the first tick of a real run.

## Step 4 — look at the rest of the configuration

The remaining blocks decide what the generated task looks like and what appears on the issue. The defaults are a working setup; every key, with its default and its consequence, is in the [configuration reference](configuration.md). The three worth a look now:

- `poll_interval_seconds` (default `300`) — how long the loop waits between ticks. The stop sentinel is checked about once a second during the wait, so stopping never waits out the interval.
- `task.*` — the dispatch fields the task file carries: `task_type`, `queue`, `priority`, `commit_type`, the branch prefix and the id prefix. A key you delete is a key the task does not carry, so worc's own default stands instead.
- `worc.tasks_dir` and `worc.max_*` — worc's `paths.tasks_dir` and its `validation.max_*` limits, restated here. The connector never opens worc's configuration, so **if you changed either over there, change it here too**; a mismatch shows up as a task worc never queues, or a file its gate quarantines.

`task.auto_merge` is commented out on purpose and should stay that way. An issue-sourced task that merges itself is where a prompt injection in a stranger's issue becomes merged code with nobody in between.

## Step 5 — the dry run

```bash
worc-connect watch --once --dry-run
```

This is the safe first command, and it is safe in the strongest sense: it writes no task file, no state database, no log, and it makes no write call to the tracker. It reads, decides, and prints.

You get one `plan:` line per item the tick saw:

```text
dry run: nothing is written — no task file, no state row, no tracker call
plan: repo=OWNER/REPO tracker=github listed=3 followed=0 gated=1 following=0
plan: item=142 action=stage-task reason=trigger-label task=gh-142 branch=worc/gh-142-signup-form-rejects-valid-emails label=worc:queued url=https://github.com/OWNER/REPO/issues/142
plan: item=143 action=skip reason=no-trigger-label task=- branch=- label=- url=https://github.com/OWNER/REPO/issues/143
plan: watermark=-
```

Read it as: `action` is what the tick decided — `stage-task` (this becomes a worc task), `follow` (a task already exists and is being tracked), `skip` (nothing to do) — and `reason` is why, in a fixed vocabulary that never carries text an item wrote: `trigger-label`, `allowed-author`, `allow-all`, `no-trigger-label`, `author-not-allowed`.

Run it as often as you like, editing the configuration in between, until the plan says what you expect. A configuration problem stops it here with exit code `2` and a message naming the key — before any tracker call is made.

**Check:** exit code `0`, and every item you wanted gated shows `action=stage-task`. If the list is empty, nothing in the repository carries the trigger label yet — that is the next step.

## Step 6 — label your first issue

Pick one small, well-described issue. Apply the trigger label (`worc` by default).

If the label does not exist in the repository yet, you have two choices: create it in the GitHub UI, or run one real tick (step 7) — the connector creates the labels the repository is missing, both its own state labels and the trigger labels your gate names, on the first tick of a process. It never edits a label that already exists.

**Check:** `worc-connect watch --once --dry-run` now shows that issue with `action=stage-task reason=trigger-label` and prints the task id and branch it would use.

## Step 7 — the first real tick

Start `worc watch` in the clone if it is not already running — the connector queues tasks, worc runs them — and then, in another shell:

```bash
worc-connect watch --once
```

That single pass does, for each gated item: allocate the task id and branch, write `tasks/preparing/gh-142.md`, run `worc promote gh-142`, put `worc:queued` on the issue and comment "Queued as worc task `gh-142`".

```text
tick: repo=OWNER/REPO tracker=github listed=3 followed=0 gated=1 following=0
```

**Check, in three places:**

1. **On the issue** — the label `worc:queued` and a comment naming the task id.
2. **In the clone** — `worc list` shows `gh-142`, and `tasks/preparing/gh-142.md` is gone because `promote` moved it into worc's queue.
3. **In the connector** — `worc-connect status` prints a row for the item:

   ```text
   status: tracker=github repo=OWNER/REPO home=.worc-connect
   status: watermark=2026-09-15T09:12:44+00:00
   status: last_tick=2026-09-15T09:12:45+00:00 result=ok
   status: item=142 seq=1 phase=queued task=gh-142 branch=worc/gh-142-… pr=-
   ```

If something went wrong instead, the tick says so on stderr and in `.worc-connect/connect.log`, one line per action — start at [troubleshooting](troubleshooting.md).

## Step 8 — watch it through to the end

Now keep ticking, either by re-running `worc-connect watch --once` by hand, or by starting the loop:

```bash
worc-connect watch
```

The loop polls every `poll_interval_seconds` until you stop it. Stopping is a file, not a signal — the same on Windows and POSIX:

```bash
: > .worc-connect/connect.stop    # the loop exits before its next tick and removes the sentinel
```

What you should see on the issue, over the next few ticks:

| When | The issue shows |
| --- | --- |
| worc starts running the task | `worc:in-progress` |
| the task opens a pull request | `worc:pr-open`, plus a comment carrying the PR URL |
| the pull request is merged | `worc:done`, and the issue closed with a message naming the task and the PR |
| the task ended without a PR, or the PR was closed unmerged | `worc:failed`, plus a comment naming the status and pointing at `worc status <task-id>` |

Merge the pull request yourself — review it like any other. From the moment the PR exists, the connector only watches it: it never pushes to the branch, and it recomputes the state from the pull request itself on every tick, so a PR you close and reopen returns the issue to `worc:pr-open` by itself.

**Check, when the PR merges:** the issue is closed and carries `worc:done`. That is the whole loop, end to end.

## Where to go next

- Running it for real — as a daemon or from cron, log rotation, upgrades, recovering a failed task: [operating it](operations.md).
- Understanding what a tick does and which file holds what: [how it works](how-it-works.md).
- Turning on the optional agent analysis step before any code is written: [triage](triage.md).
- Something did not happen: [troubleshooting](troubleshooting.md).
