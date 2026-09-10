# Security rules

## MANDATORY for any change in the connector

The connector sits between strangers who write issues and an agent with write access to a repository. Its security model is three lines long, and every change must keep all three true.

1. **Untrusted text has exactly one destination.** An item's title reaches the task file only after the deterministic sanitizer; an item's body reaches the task file body verbatim and nowhere else. It is never a command argument, an environment value, a log line, a label name, a comment, or a close message. Comment bodies and close messages are connector-authored templates carrying only the task id, a status name and URLs, and they go to the tracker through a body **file**. Label names come from configuration.
2. **The gate is the perimeter, and it fails closed.** Only an item matching an explicit allow rule — a configured trigger label and/or a configured author allow-list — is ever turned into a task. A configuration with no rule refuses to start unless it states `gate.allow_all: true` in so many words. An item nobody gated never reaches an agent, so a drive-by issue can neither spend budget nor steer a run. An unparseable configuration, an unreachable tracker, or an unreadable `worc list` output stops the tick with a logged reason and changes no state.
3. **The connector cannot weaken worc, and holds nothing worth stealing.** Its only write into worc is a task file, and a task file cannot carry `extra_args`, a permission profile, or a flow edit; it never touches `.worc/`, `.git`, worc's `config.yaml`, or a flow file except the triage flow it installs on an explicit operator command. It holds no token — `gh` owns the login — and writes nothing secret to its state, its log, or a task file. It launches `gh` and `worc` as argument lists resolved with `shutil.which`, forwards no additional environment, and pins every `gh` call with `--repo` from configuration.

## The caveat that is documented, not softened

An issue-sourced task under `auto_merge: true` is where a prompt injection in an issue turns into merged code without a human in between. The connector's `init` leaves `auto_merge` unset so worc's own policy decides; the README says why, and no change may make the connector set it by default.

## Security mechanisms must not degrade the product

As in worc: restrictions are introduced only when a real risk requires them, and then as the least restrictive solution that provides the protection. The three lines above are that minimum; add to them when a concrete threat needs it, not on principle, and never trade an operator capability away silently.
