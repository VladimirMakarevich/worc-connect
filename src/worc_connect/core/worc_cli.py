"""Launching worc, and the surfaces the connector is allowed to reach it through.

Into worc there is exactly one write — a task file promoted with ``worc promote`` — and out of it
two reads: ``worc list --format json --all`` for the state of a task, and ``worc --version`` for
the one thing the connector must know before it writes a file, which of the front-matter keys this
worc accepts. Both live here so that the narrowness of that boundary is visible in one file:
nothing else in the connector launches worc, and nothing anywhere reads ``state.db``, the ledger,
the logs or the quarantine folder, which are worc's private home.

The launch follows the same discipline as the tracker transport: an argument list, never a shell;
the executable resolved with :func:`shutil.which` so a Windows ``worc.cmd`` works; a mandatory
timeout; the connector's own environment forwarded unchanged; and the working directory set to the
configured clone, which is how worc finds its own configuration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from worc_connect.core import worc_version

# Every surface is local and cheap — a file rename, a read-only database query, a string. A worc
# that has not answered by then is wedged, and the tick is better spent retrying next time than
# waiting.
DEFAULT_TIMEOUT_SECONDS: Final = 120.0

# The status worc gives an id its validation gate refused. Such an id has no task row of its own,
# so the listing is the only place outside worc's private home that names it — and the reason.
REJECTED_STATUS: Final = "rejected"

# How much of a rejection reason may be repeated back. worc's reasons are short enum names, and the
# string ends up in a comment on a public item, so it is bounded here rather than trusted to stay
# short forever.
_REASON_MAX_CHARS: Final = 120


class WorcUnavailable(Exception):
    """worc could not be launched, did not finish, or answered with something unusable.

    One class rather than several because the connector's reaction never differs: the tick stops
    where it is, no row changes, and the next tick is the retry. A task worc already holds is worc's
    to finish either way.
    """


@dataclass(frozen=True)
class WorcRun:
    """What one worc invocation said. Non-zero exits are ordinary here, not failures.

    ``worc promote`` exits 1 both for "I could not" and for "that file is already queued", and the
    second is a success as far as the connector is concerned — so the classification belongs to the
    caller that knows the verb, not to the launcher.
    """

    returncode: int
    stdout: str
    stderr: str

    def stdout_lines(self) -> list[str]:
        """The non-blank lines worc printed, which is where it reports each outcome."""
        return [line.strip() for line in self.stdout.splitlines() if line.strip()]


@dataclass(frozen=True)
class ListedTask:
    """One entry of worc's listing, reduced to what the connector is allowed to act on.

    ``status`` is worc's display label, so callers match its leading token. ``pr_url`` is worc's own
    record of the request the task opened — exact, and still there after the head branch is gone.
    ``validation_reason`` is present only on an id worc's gate refused, which has no task row at all
    and is therefore reported to the operator from this entry or not at all.
    """

    status: str
    pr_url: str | None = None
    validation_reason: str | None = None


@dataclass
class WorcCommand:
    """The configured worc launcher, bound to the clone it runs against.

    Holds one piece of per-process state, the answer to the version handshake, because a worc
    cannot change underneath a running connector and asking it once is the difference between one
    launch and one per staged task.
    """

    command: str
    repo_path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    _accepts_references: bool | None = field(default=None, repr=False)

    def accepts_references(self) -> bool:
        """Whether the worc on this ``PATH`` understands the ``references:`` front-matter key.

        Answered ``False`` whenever it cannot be established — a launcher that is missing, a
        version line this build does not recognise, a non-zero exit. An unknown front-matter key is
        a hard reject at worc's gate and a rejected task is quarantined inside a home the connector
        may not read, so "I cannot tell" has to produce the file an older worc already accepts.
        """
        if self._accepts_references is None:
            self._accepts_references = worc_version.supports_contract(self._reported_version())
        return self._accepts_references

    def promote(self, task_id: str) -> WorcRun:
        """Move the staged task into the queue through worc's own refuse-to-overwrite step.

        The task id is the only value that reaches this argument list, and it was built from the
        item's identifier under worc's id grammar — never from the item's text.
        """
        return self.run("promote", task_id)

    def list_tasks(self) -> dict[str, ListedTask]:
        """Every task worc knows, as ``task_id`` to the entry worc published for it.

        The ``--all`` view is the one that holds every database row and the only one that carries
        the ids worc's gate refused; the default view caps its recent section, which would make a
        task silently unreadable the moment the queue got busy.
        """
        completed = self.run("list", "--format", "json", "--all")
        if completed.returncode != 0:
            raise WorcUnavailable(f"`worc list` failed with exit code {completed.returncode}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WorcUnavailable(
                "`worc list --format json` returned output that is not JSON"
            ) from exc
        return _listed(payload)

    def _reported_version(self) -> str:
        """What ``worc --version`` printed, or an empty string when it could not be asked.

        A worc that refuses to say what it is is a worc the connector treats as the oldest one it
        supports, which costs a contract key and never a rejected task.
        """
        try:
            completed = self.run("--version")
        except WorcUnavailable:
            return ""
        return completed.stdout if completed.returncode == 0 else ""

    def run(self, *args: str) -> WorcRun:
        """Run worc with ``args`` in the configured clone and return what it said.

        Raises :class:`WorcUnavailable` only for failures to run at all — a missing launcher, a
        timeout, an operating-system refusal. A worc that ran and disagreed is an answer.
        """
        executable = shutil.which(self.command)
        if executable is None:
            raise WorcUnavailable(
                f"the worc launcher `{self.command}` was not found on PATH; "
                "install worc in this clone or set `worc.command`"
            )
        try:
            # A fixed argument list, no shell, and the environment inherited as it is: nothing is
            # added for worc and nothing item-derived is passed to it.
            completed = subprocess.run(
                [executable, *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorcUnavailable(
                f"`worc {args[0]}` did not finish within {self.timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise WorcUnavailable(f"`worc {args[0]}` could not be launched: {exc}") from exc
        return WorcRun(
            returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
        )


def status_token(status: str) -> str:
    """The leading word of a worc status label.

    worc renders a status for humans: a running task can read ``running (paused)``, ``running
    (paused until …)`` or ``parked (no daemon)``. The first token is the part that is a state.
    """
    return status.split(maxsplit=1)[0] if status.strip() else ""


def _listed(payload: Any) -> dict[str, ListedTask]:
    """The listing as a mapping of task id to entry, or :class:`WorcUnavailable`.

    An entry the connector cannot read is refused rather than skipped: the listing is what decides
    whether a task is still running or has failed, and a half-read answer would be worse than none.
    A key an older worc does not publish is simply absent, which is what lets the same reader serve
    a worc from before the contract and one from after it.
    """
    if not isinstance(payload, list):
        raise WorcUnavailable("`worc list --format json` did not return a list of entries")
    listed: dict[str, ListedTask] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            raise WorcUnavailable(
                "`worc list --format json` returned an entry that is not an object"
            )
        task_id = entry.get("task_id")
        status = entry.get("status")
        if isinstance(task_id, str) and task_id and isinstance(status, str):
            listed[task_id] = ListedTask(
                status=status,
                pr_url=_text(entry.get("pr_url")),
                validation_reason=_reason(entry.get("validation_reason")),
            )
    return listed


def _text(value: Any) -> str | None:
    """A non-blank string from the listing, or ``None`` for a key worc left null or never wrote."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _reason(value: Any) -> str | None:
    """worc's own rejection reason, bounded before it can be repeated on a public item.

    Collapsed to one line and capped: the value is written by worc, not by the item's author, but
    it is published where the item's readers see it, and a comment body is not a place for text of
    unbounded shape whoever wrote it.
    """
    collapsed = _text(value)
    return None if collapsed is None else " ".join(collapsed.split())[:_REASON_MAX_CHARS]
