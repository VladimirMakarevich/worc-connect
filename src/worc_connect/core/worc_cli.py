"""Launching worc, and the two surfaces the connector is allowed to reach it through.

Into worc there is exactly one write — a task file promoted with ``worc promote`` — and out of it
exactly one read, ``worc list --format json --all``. Both live here so that the narrowness of that
boundary is visible in one file: nothing else in the connector launches worc, and nothing anywhere
reads ``state.db``, the ledger, the logs or the quarantine folder, which are worc's private home.

The launch follows the same discipline as the tracker transport: an argument list, never a shell;
the executable resolved with :func:`shutil.which` so a Windows ``worc.cmd`` works; a mandatory
timeout; the connector's own environment forwarded unchanged; and the working directory set to the
configured clone, which is how worc finds its own configuration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# Both surfaces are local and cheap — a file rename and a read-only database query. A worc that has
# not answered by then is wedged, and the tick is better spent retrying next time than waiting.
DEFAULT_TIMEOUT_SECONDS: Final = 120.0


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
class WorcCommand:
    """The configured worc launcher, bound to the clone it runs against."""

    command: str
    repo_path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def promote(self, task_id: str) -> WorcRun:
        """Move the staged task into the queue through worc's own refuse-to-overwrite step.

        The task id is the only value that reaches this argument list, and it was built from the
        item's identifier under worc's id grammar — never from the item's text.
        """
        return self.run("promote", task_id)

    def list_tasks(self) -> dict[str, str]:
        """Every task worc knows, as ``task_id`` to its status label.

        The ``--all`` view is the one that holds every database row; the default view caps its
        recent section, which would make a task silently unreadable the moment the queue got busy.
        The status is worc's display label, so callers match its leading token rather than the
        whole string.
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
        return _statuses(payload)

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


def _statuses(payload: Any) -> dict[str, str]:
    """The listing as a mapping of task id to status, or :class:`WorcUnavailable`.

    An entry the connector cannot read is refused rather than skipped: the listing is what decides
    whether a task is still running or has failed, and a half-read answer would be worse than none.
    """
    if not isinstance(payload, list):
        raise WorcUnavailable("`worc list --format json` did not return a list of entries")
    statuses: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            raise WorcUnavailable(
                "`worc list --format json` returned an entry that is not an object"
            )
        task_id = entry.get("task_id")
        status = entry.get("status")
        if isinstance(task_id, str) and task_id and isinstance(status, str):
            statuses[task_id] = status
    return statuses
