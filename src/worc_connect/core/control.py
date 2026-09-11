"""Cross-process control of one watcher: how it is stopped, and how a second one is refused.

Both mechanisms here are **files**, and that is the whole reason the module exists. ``os.kill`` and
signals are POSIX-shaped: on Windows a process cannot signal — or even probe — a process it does not
own, so a stop built on them would behave differently on the operating system a release has to
support equally. A sentinel file the loop polls and a PID file it holds behave identically
everywhere, and both are visible to an operator who wants to know what is going on.

Liveness is deliberately **not** probed. A PID file left behind by a crash is refused with the path
to delete rather than guessed about: the alternative is a heuristic that eventually decides a
running watcher is dead and starts a second one against the same clone.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from worc_connect.home import ConnectorHome

logger = logging.getLogger(__name__)

# How often the sleep between ticks looks at the stop sentinel. Short enough that a stop is acted on
# while the operator is still watching, long enough to be invisible next to a five-minute interval.
STOP_POLL_SECONDS: Final = 1.0


@dataclass(frozen=True)
class ProcessControl:
    """The stop sentinel and the PID file of one connector home.

    The clock and the sleep arrive as fields so a test drives a whole daemon loop — claim, tick,
    interrupted sleep, release — without waiting a single real second.
    """

    home: ConnectorHome
    now_fn: Callable[[], datetime]
    sleep_fn: Callable[[float], None]

    def stop_requested(self) -> bool:
        """Whether the stop sentinel is present; it is consumed so the next start is not stopped."""
        if not self.home.stop_path.is_file():
            return False
        self.home.stop_path.unlink(missing_ok=True)
        logger.info("item=- task=- action=stop result=sentinel")
        return True

    def sleep(self, seconds: float) -> bool:
        """Sleep out ``seconds``, returning False as soon as a stop is requested.

        Sliced rather than slept in one call so a stop written mid-interval is acted on within a
        second instead of waiting out the operator's poll interval.
        """
        remaining = seconds
        while remaining > 0:
            if self.stop_requested():
                return False
            slice_seconds = min(STOP_POLL_SECONDS, remaining)
            self.sleep_fn(slice_seconds)
            remaining -= slice_seconds
        return True

    def claim(self) -> bool:
        """Write the PID file, or refuse to start because another watcher holds it.

        Presence is the claim: a watcher writes the file on start and removes it on a clean exit.
        """
        path = self.home.pid_path
        if path.is_file():
            logger.error(
                "item=- task=- action=start result=refused (another watcher holds %s; "
                "delete it if no connector is running)",
                path.as_posix(),
            )
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"pid": os.getpid(), "started_at": self.now_fn().astimezone(UTC).isoformat()}
        path.write_text(json.dumps(record) + "\n", encoding="utf-8", newline="")
        return True

    def release(self) -> None:
        """Remove the PID file so the next watcher can start; safe if it is already gone."""
        self.home.pid_path.unlink(missing_ok=True)
