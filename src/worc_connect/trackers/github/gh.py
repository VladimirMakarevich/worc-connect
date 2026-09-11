"""Launching the operator's own ``gh``, and classifying what it fails with.

The connector holds no GitHub credential. Authentication is the operator's ``gh auth login``, the
same login worc already requires for its own pull requests, which is the whole reason the transport
is a command-line tool rather than an HTTP client: there is no token here to store, log, leak or
forward.

Every launch is an argument list resolved with :func:`shutil.which` — so a Windows ``gh.exe`` or
``gh.cmd`` launcher works — pinned with ``--repo OWNER/REPO`` from configuration, given a mandatory
timeout, and handed the connector's own environment unchanged. No shell, ever, and no item-derived
text in an argument.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Final

from worc_connect.trackers.base import (
    TrackerAuth,
    TrackerError,
    TrackerRateLimited,
    TrackerUnavailable,
)

EXECUTABLE_NAME: Final = "gh"

# Every call is bounded: a `gh` that hangs on a stalled connection must cost one tick, not the
# process. Generous enough for a cold DNS lookup plus an API round trip on a slow link.
DEFAULT_TIMEOUT_SECONDS: Final = 60.0

# `gh` reports an authentication problem with its own exit code, which is worth trusting before any
# message matching: the text is localized and reworded between releases, the code is not.
_AUTH_EXIT_CODE: Final = 4

# Checked before the authentication signatures on purpose: GitHub's rate-limit message helpfully
# mentions that *authenticated* requests get a higher limit, so matching on authentication words
# first would misclassify a throttled operator as a logged-out one and send them to re-login.
_RATE_LIMIT_SIGNATURES: Final = ("rate limit", "http 429")
_AUTH_SIGNATURES: Final = (
    "not logged in",
    "gh auth login",
    "authentication",
    "authenticated",
    "bad credentials",
    "http 401",
)

# How much of `gh`'s own diagnostic travels into the exception message. Bounded because that message
# is logged: `gh` never echoes an issue body for the verbs used here, and a cap keeps a surprising
# multi-kilobyte error out of the connector's log either way.
_DIAGNOSTIC_LIMIT: Final = 200


@dataclass(frozen=True)
class GhCommand:
    """A ``gh`` invocation pinned to one repository, with the failure mapping the loop expects."""

    repo: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def read_json(self, *args: str) -> Any:
        """Run ``gh`` with ``--json`` output and return the parsed document.

        Output the connector cannot parse is :class:`TrackerUnavailable`, not a crash and not a
        guess: an unreadable listing stops the tick with a reason and changes no state.
        """
        stdout = self.run(*args)
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise TrackerUnavailable(
                f"`gh {_verb(args)}` returned output that is not JSON"
            ) from exc

    def run(self, *args: str) -> str:
        """Run ``gh`` with ``args`` plus the repository pin and return its stdout.

        Raises one of the three :class:`~worc_connect.trackers.base.TrackerError` classes for
        every failure, a ``gh`` missing from ``PATH`` included — the loop treats all three the same
        way, so nothing here needs to decide what a failure means for the connector's state.
        """
        executable = shutil.which(EXECUTABLE_NAME)
        if executable is None:
            raise TrackerUnavailable(
                "the GitHub CLI `gh` was not found on PATH; install it and run `gh auth login`"
            )
        # The pin goes last so that no code path can compose a call without it.
        argv = [executable, *args, "--repo", self.repo]
        try:
            # A fixed argument list, no shell, and no item-derived text anywhere in it.
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TrackerUnavailable(
                f"`gh {_verb(args)}` did not finish within {self.timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise TrackerUnavailable(f"`gh {_verb(args)}` could not be launched: {exc}") from exc
        if completed.returncode != 0:
            raise _failure(args, completed.returncode, completed.stderr)
        return completed.stdout


def _verb(args: tuple[str, ...]) -> str:
    """The subcommand of a call, for a message that names what failed and nothing else."""
    return " ".join(args[:2])


def _failure(args: tuple[str, ...], code: int, stderr: str) -> TrackerError:
    """Classify a non-zero ``gh`` exit into the three failures the loop knows how to react to."""
    text = stderr.casefold()
    detail = f"`gh {_verb(args)}` failed with exit code {code}: {_diagnostic(stderr)}"
    if any(signature in text for signature in _RATE_LIMIT_SIGNATURES):
        return TrackerRateLimited(detail)
    if code == _AUTH_EXIT_CODE or any(signature in text for signature in _AUTH_SIGNATURES):
        return TrackerAuth(f"{detail} — run `gh auth login` on this host")
    return TrackerUnavailable(detail)


def _diagnostic(stderr: str) -> str:
    """``gh``'s own first line of complaint, bounded, or a placeholder when it said nothing."""
    first_line = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    if not first_line:
        return "no diagnostic on stderr"
    return first_line[:_DIAGNOSTIC_LIMIT]
