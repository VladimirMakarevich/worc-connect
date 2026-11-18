"""Composing the task file a row stands for, from the item and from what surrounds it.

The builder is pure: it turns what it is handed into a file. What it is handed is decided here — the
triage task or the implementation task, the report an implementation task is built from, the
closing reference the installed worc accepts, what the reporter answered a triage question with,
and the other open items a triage run should know about. Each of those is read only where the row
needs it, and the tracker's listing of open items at most once per tick.
"""

from __future__ import annotations

from dataclasses import dataclass

from worc_connect.config import ConnectorConfig
from worc_connect.core import builder, triage
from worc_connect.core.items import ItemHeadline, Reply, WorkItem
from worc_connect.core.state import ItemRow, Phase, Stage, StateStore
from worc_connect.core.triage import Report
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerAdapter


@dataclass
class Drafter:
    """One tick's task-file composer. Not frozen: it keeps the open items it read for the tick."""

    config: ConnectorConfig
    store: StateStore
    worc: WorcCommand
    adapter: TrackerAdapter
    home: ConnectorHome
    _open_items: tuple[ItemHeadline, ...] | None = None

    def draft(self, row: ItemRow, item: WorkItem) -> builder.TaskDraft:
        """The task this row stands for: the triage task, or the implementation task it produced.

        The report is re-read here rather than carried on the row because this is the one place a
        task file is composed, and a crash between reading a report and writing the file must cost
        a re-read rather than a task built from nothing.
        """
        if row.stage is Stage.RESEARCH:
            return builder.build_research(
                item,
                self.config,
                seq=row.seq,
                replies=self._replies(row, item),
                others=self._others(),
            )
        report = self._report(row.research_task_id)
        if report is None:
            return builder.build(
                item,
                self.config,
                seq=row.seq,
                branch_ref=row.branch,
                references=self._references(item),
                replies=self._replies(row, item),
            )
        return builder.build_from_report(
            item,
            self.config,
            report,
            seq=row.seq,
            branch_ref=row.branch,
            references=self._references(item),
        )

    def _replies(self, row: ItemRow, item: WorkItem) -> tuple[Reply, ...]:
        """What was said on the item since the previous attempt asked the reporter a question.

        Only an attempt the answer started has any: the previous one ended ``needs-info``, and the
        stamp it recorded is the one the question itself left on the item — so the question, and
        every other comment the connector posted before it, is older and never read back as the
        answer, though the operator's own login wrote both.
        """
        previous = self.store.latest_row(row.tracker, row.item_id, before=row.seq)
        if previous is None or previous.phase is not Phase.NEEDS_INFO:
            return ()
        if previous.item_updated_at is None:  # pragma: no cover - a posted question is stamped
            return ()
        return tuple(self.adapter.replies(item.identifier, since=previous.item_updated_at))

    def _others(self) -> tuple[ItemHeadline, ...]:
        """The repository's open items, read once per tick and only by a tick that builds triage."""
        if self._open_items is None:
            self._open_items = tuple(self.adapter.open_items())
        return self._open_items

    def _report(self, task_id: str | None) -> Report | None:
        """The triage report ``task_id`` left in the connector's home, if it left a usable one."""
        if task_id is None:
            return None
        return triage.read(self.home.triage_path, task_id)

    def _references(self, item: WorkItem) -> tuple[str, ...]:
        """The closing line this task may carry, if the installed worc accepts the key at all.

        The adapter authors the line in its own tracker's keyword and worc appends it to the pull
        request it opens without interpreting it, which is how the item can be closed by the code
        host itself. A worc that does not know the key gets a task without one: an unknown
        front-matter key is a hard reject, not a warning.
        """
        if not self.worc.accepts_references():
            return ()
        line = self.adapter.closing_reference(item)
        return () if line is None else (line,)
