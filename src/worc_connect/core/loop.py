"""The tick and the watch loop: list, gate, record, and sleep — with no model call anywhere in it.

The loop is deliberately dull. It lists the items a tracker says changed, asks the gate about each
one, records what it decided, and advances a watermark. Everything that needs judgement is a worc
task the connector queues; the polling loop itself makes no model call, which is what keeps a tick
with nothing new free and a tick's behaviour predictable.

Two operational properties matter as much as the logic:

* **A failing tracker costs one tick.** All three adapter failures — unreachable, throttled, not
  logged in — are caught here, logged by class, and change no state. The next tick is the retry.
* **Stopping is a file, not a signal.** The loop polls a sentinel while it sleeps and keeps its own
  PID file, so a second watcher in the same clone is refused and a stop behaves identically on
  Windows and POSIX, where no process can signal another one it does not own.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core import gate
from worc_connect.core.items import WorkItem
from worc_connect.core.state import (
    META_LAST_TICK_AT,
    META_LAST_TICK_RESULT,
    ItemRow,
    Phase,
    StateStore,
    read_watermark,
    write_watermark,
)
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerAdapter, TrackerError

logger = logging.getLogger(__name__)

# How far back of the watermark each listing reaches. The tracker's clock, the host's clock and the
# moment a tick finishes are three different things, and an item updated inside that spread would
# otherwise fall between two listings and never be seen. Re-listing an item is free: it already has
# a row, so the overlap is idempotent by construction.
WATERMARK_OVERLAP: Final = timedelta(minutes=10)

# How often the sleep between ticks looks at the stop sentinel. Short enough that a stop is acted on
# while the operator is still watching, long enough to be invisible next to a five-minute interval.
STOP_POLL_SECONDS: Final = 1.0

# Exit codes: 0 a completed pass, 1 an infrastructure failure or a refused start. A configuration
# problem is exit 2 and never reaches this module — the CLI refuses before a loop exists.
_EXIT_OK: Final = 0
_EXIT_FAILED: Final = 1


class Action(StrEnum):
    """What the tick decided to do about one item, in the vocabulary a dry run prints."""

    STAGE = "stage-task"
    FOLLOW = "follow"
    SKIP = "skip"


@dataclass(frozen=True)
class PlannedAction:
    """One item's outcome for this tick: what was decided and the reason it is reported under.

    Carries the item's identifier and URL and nothing else the item wrote — a plan an operator reads
    on a terminal is not a place for a stranger's text.
    """

    item_id: str
    url: str
    action: Action
    reason: str


@dataclass(frozen=True)
class TickReport:
    """What one tick saw and did — the value ``status`` summarizes and a dry run prints."""

    listed: int
    actions: tuple[PlannedAction, ...]
    watermark: datetime | None

    def counted(self, action: Action) -> int:
        """How many items this tick decided ``action`` for."""
        return sum(1 for planned in self.actions if planned.action is action)


def _utc_now() -> datetime:
    """The current time, timezone-aware, as the one clock the loop reads."""
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class Watcher:
    """One connector process against one tracker repository and one worc clone.

    Everything the loop touches arrives as a field — the adapter, the store, the home, the clock and
    the sleep — so the whole loop is exercised in tests without a real tracker, a real wait or a
    second process.
    """

    config: ConnectorConfig
    adapter: TrackerAdapter
    store: StateStore
    home: ConnectorHome
    on_tick: Callable[[TickReport], None] = lambda report: None
    now_fn: Callable[[], datetime] = _utc_now
    sleep_fn: Callable[[float], None] = time.sleep

    def run(self, *, once: bool, dry_run: bool) -> int:
        """Run a single pass or the daemon loop; returns the process exit code."""
        if once:
            return self._single_pass(dry_run=dry_run)
        return self._daemon(dry_run=dry_run)

    def tick(self, *, dry_run: bool) -> TickReport:
        """One pass over the items the tracker reports as changed.

        Raises :class:`~worc_connect.trackers.base.TrackerError` when the tracker cannot be read:
        the caller decides whether that ends the process or costs one tick, and either way no state
        has changed by then — the listing is the first thing the tick does.
        """
        now = self.now_fn()
        items = self.adapter.list_items(self._listing_floor())
        actions = tuple(self._plan(item, dry_run=dry_run, now=now) for item in items)
        watermark = self._advance_watermark(items, dry_run=dry_run)
        report = TickReport(listed=len(items), actions=actions, watermark=watermark)
        if not dry_run:
            self._note_tick(now, f"ok listed={report.listed} staged={report.counted(Action.STAGE)}")
        return report

    def _single_pass(self, *, dry_run: bool) -> int:
        """One tick, reporting an unreachable tracker as a failed run rather than as a retry."""
        try:
            report = self.tick(dry_run=dry_run)
        except TrackerError as exc:
            self._note_failure(exc, dry_run=dry_run)
            return _EXIT_FAILED
        self.on_tick(report)
        return _EXIT_OK

    def _daemon(self, *, dry_run: bool) -> int:
        """Tick until the stop sentinel appears, holding the clone's PID file while it runs.

        A dry run claims no PID file: it promises to write nothing at all, and one watcher's promise
        not to write must not turn into a lock on the next one.
        """
        tracked = not dry_run
        if tracked and not self._claim_pid_file():
            return _EXIT_FAILED
        try:
            while not self._stop_requested():
                self._guarded_tick(dry_run=dry_run)
                if not self._sleep_between_ticks():
                    break
        finally:
            if tracked:
                self._release_pid_file()
        return _EXIT_OK

    def _guarded_tick(self, *, dry_run: bool) -> None:
        """One tick whose infrastructure failure is logged and retried on the next one."""
        try:
            report = self.tick(dry_run=dry_run)
        except TrackerError as exc:
            self._note_failure(exc, dry_run=dry_run)
            return
        self.on_tick(report)

    def _listing_floor(self) -> datetime | None:
        """The timestamp to list from: the watermark less the overlap, or everything open."""
        watermark = read_watermark(self.store)
        return None if watermark is None else watermark - WATERMARK_OVERLAP

    def _plan(self, item: WorkItem, *, dry_run: bool, now: datetime) -> PlannedAction:
        """Decide — and, unless this is a dry run, record — what happens to one item."""
        verdict = gate.evaluate(item, self.config.gate)
        row = self.store.latest_row(self.config.tracker, item.identifier)
        if row is not None:
            return self._follow(item, row, verdict_admitted=verdict.admitted)
        if not verdict.admitted:
            _log(item, None, Action.SKIP, str(verdict.reason))
            return PlannedAction(item.identifier, item.url, Action.SKIP, str(verdict.reason))
        if not dry_run:
            self.store.save(self._first_row(item, now=now))
        _log(item, None, Action.STAGE, str(verdict.reason))
        return PlannedAction(item.identifier, item.url, Action.STAGE, str(verdict.reason))

    def _follow(self, item: WorkItem, row: ItemRow, *, verdict_admitted: bool) -> PlannedAction:
        """Report an item that already has a row; its task belongs to worc from here on.

        A trigger label taken off afterwards does not withdraw the task — it is worc's now, and
        cancelling a run from a label would make a queue an operator cannot reason about — so the
        withdrawal is logged and nothing else happens.
        """
        if not verdict_admitted:
            _log(item, row.task_id, Action.FOLLOW, "gate-withdrawn")
        _log(item, row.task_id, Action.FOLLOW, str(row.phase))
        return PlannedAction(item.identifier, item.url, Action.FOLLOW, str(row.phase))

    def _first_row(self, item: WorkItem, *, now: datetime) -> ItemRow:
        """The row an admitted item gets on the tick that first admits it."""
        return ItemRow(
            tracker=self.config.tracker,
            item_id=item.identifier,
            seq=1,
            phase=Phase.GATED,
            created_at=now,
            updated_at=now,
            item_updated_at=item.updated_at,
        )

    def _advance_watermark(self, items: Sequence[WorkItem], *, dry_run: bool) -> datetime | None:
        """Move the watermark to the newest update this tick saw, once the tick has handled it.

        Recorded after the items are handled, never before: a crash mid-tick leaves the old
        watermark, and the items are listed again on the next one.
        """
        current = read_watermark(self.store)
        if not items:
            return current
        newest = max(item.updated_at for item in items)
        if current is not None and newest <= current:
            return current
        if not dry_run:
            write_watermark(self.store, newest)
        return newest

    def _note_tick(self, now: datetime, result: str) -> None:
        """Record when the last tick ran and how it ended, for ``worc-connect status``."""
        self.store.set_meta(META_LAST_TICK_AT, now.astimezone(UTC).isoformat())
        self.store.set_meta(META_LAST_TICK_RESULT, result)

    def _note_failure(self, exc: TrackerError, *, dry_run: bool) -> None:
        """Log an infrastructure failure by class and leave every row untouched."""
        logger.warning("item=- task=- action=list result=skipped-%s (%s)", type(exc).__name__, exc)
        if not dry_run:
            self._note_tick(self.now_fn(), f"skipped {type(exc).__name__}")

    def _stop_requested(self) -> bool:
        """Whether the stop sentinel is present; it is consumed so the next start is not stopped."""
        if not self.home.stop_path.is_file():
            return False
        self.home.stop_path.unlink(missing_ok=True)
        logger.info("item=- task=- action=stop result=sentinel")
        return True

    def _sleep_between_ticks(self) -> bool:
        """Sleep out the poll interval, returning False as soon as a stop is requested.

        Sliced rather than slept in one call so a stop written mid-interval is acted on within a
        second instead of waiting out the operator's poll interval.
        """
        remaining = float(self.config.poll_interval_seconds)
        while remaining > 0:
            if self._stop_requested():
                return False
            slice_seconds = min(STOP_POLL_SECONDS, remaining)
            self.sleep_fn(slice_seconds)
            remaining -= slice_seconds
        return True

    def _claim_pid_file(self) -> bool:
        """Write the PID file, or refuse to start because another watcher holds it.

        Presence is the claim: a watcher writes the file on start and removes it on a clean exit.
        Liveness is deliberately not probed — a process cannot be probed portably without signals,
        which do not reach a foreign process on Windows — so a file left behind by a crash is
        refused with the path to delete rather than guessed about.
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

    def _release_pid_file(self) -> None:
        """Remove the PID file so the next watcher can start; safe if it is already gone."""
        self.home.pid_path.unlink(missing_ok=True)


def _log(item: WorkItem, task_id: str | None, action: Action, result: str) -> None:
    """One line per action, carrying only the identifiers — never a title, a body or an author."""
    logger.info(
        "item=%s task=%s action=%s result=%s", item.identifier, task_id or "-", action, result
    )
