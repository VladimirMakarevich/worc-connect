# worc-connect documentation

Everything an operator needs to install, configure, run and debug the connector. Read them in this order the first time; after that, the configuration reference and the troubleshooting page are the two you come back to.

| Guide | Read it for |
| --- | --- |
| [Getting started](getting-started.md) | The whole path in order: prerequisites, install, `gh` login, `init`, the gate, a dry run, the first real issue, and how to verify each step. Start here. |
| [How it works](how-it-works.md) | What one tick actually does, the lifecycle of an item from label to closed issue, and every file the connector owns. |
| [Configuration reference](configuration.md) | Every key of `.worc-connect/config.yaml` — default, meaning, and what changes if you touch it. |
| [Operating it](operations.md) | Day-to-day running: daemon or cron, stopping, the log, the cache, upgrades, and what to do when a task fails. |
| [Triage (optional)](triage.md) | The optional agent analysis step: what it costs, what verdicts it may reach, how to install its flow, and how to turn it off again. |
| [Troubleshooting](troubleshooting.md) | Symptom → cause → fix, with the exit codes, the failure classes and the log vocabulary. |

## The shortest possible version

```bash
cd /path/to/your/clone                     # the same clone `worc watch` runs in
gh auth status                             # you, logged in; the connector holds no token
worc-connect init --repo OWNER/REPO        # writes .worc-connect/config.yaml
worc-connect watch --once --dry-run        # prints the plan, writes nothing at all
worc-connect watch --once                  # one real tick
worc-connect status                        # what it believes now
```

## Beyond the operator's documentation

- [docs/backlog/](backlog/README.md) — the design record: the problem, the requirements, the design decisions, the acceptance criteria and the implementation phases. Read it to understand _why_ something is the way it is, not how to run it.
- The rules every contributor and coding agent follows live in [.agents/rules/](../.agents/rules/architecture.md).
