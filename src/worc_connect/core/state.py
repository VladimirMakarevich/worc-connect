"""The connector's SQLite cache: one row per item it has taken on, plus the poll watermark.

**This store is a cache, never the authority.** The tracker's own state label is the visible state
machine, worc's listing is the truth about a task, and the lifecycle folders are the truth about a
file; every tick re-reads those and corrects the row. That is what makes deleting this database a
supported operation rather than data loss, and it is why a schema this build does not recognise is
discarded instead of migrated: rebuilding costs one tick.

What the rows *do* carry is idempotency. An item that already has a row is an item that already has
a task, which is how a crash between writing a task file and promoting it stays harmless, and how a
re-trigger gets the next sequence number instead of reusing an id.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

SCHEMA_VERSION: Final = 4

_META_SCHEMA_VERSION: Final = "schema_version"

# The bookkeeping keys the loop writes and ``status`` reads. Public and spelled once: a second
# spelling in another module is a value silently written under a key nothing reads back.
META_WATERMARK: Final = "watermark"
META_LAST_TICK_AT: Final = "last_tick_at"
META_LAST_TICK_RESULT: Final = "last_tick_result"

_SCHEMA: Final = """
CREATE TABLE items (
    tracker           TEXT    NOT NULL,
    item_id           TEXT    NOT NULL,
    seq               INTEGER NOT NULL,
    task_id           TEXT    UNIQUE,
    branch            TEXT,
    phase             TEXT    NOT NULL,
    item_updated_at   TEXT,
    last_status       TEXT,
    validation_reason TEXT,
    stage             TEXT    NOT NULL,
    research_task_id  TEXT,
    research_note     TEXT,
    pr_number         INTEGER,
    pr_url            TEXT,
    pr_merged         INTEGER NOT NULL DEFAULT 0,
    retrigger_armed   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    PRIMARY KEY (tracker, item_id, seq)
);
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StateError(Exception):
    """The store was asked for something it cannot do — a write on a read-only store, say."""


class Stage(StrEnum):
    """Which of an item's two possible tasks this row is following.

    An item reaches an implementation task either directly or through a triage task first, and the
    two are followed differently: a triage task publishes nothing, so it ends at a report the
    connector reads, while an implementation task ends at a pull request the connector watches.
    One row per task keeps each id and each outcome its own, which is what the "never reuse an id"
    rule needs.
    """

    RESEARCH = "research"
    IMPLEMENTATION = "implementation"


class Phase(StrEnum):
    """Where the connector has got to with one item, from first sighting to a terminal outcome.

    Distinct from the tracker-facing :class:`~worc_connect.core.items.ItemState`: a phase is the
    connector's own bookkeeping and includes steps an operator never sees a label for, such as a
    task file staged but not yet promoted, and a triage task whose report has been read and acted
    on — the item's visible state then belongs to the implementation task that report produced.
    """

    GATED = "gated"
    STAGED = "staged"
    QUEUED = "queued"
    RUNNING = "running"
    PR_OPEN = "pr-open"
    DONE = "done"
    FAILED = "failed"
    RESEARCHED = "researched"
    NEEDS_INFO = "needs-info"
    DECLINED = "declined"


TERMINAL_PHASES: Final = frozenset(
    {Phase.DONE, Phase.FAILED, Phase.RESEARCHED, Phase.NEEDS_INFO, Phase.DECLINED}
)


@dataclass(frozen=True)
class ItemRow:
    """One item's cached row, replaced wholesale rather than patched — a write is one statement."""

    tracker: str
    item_id: str
    seq: int
    phase: Phase
    created_at: datetime
    updated_at: datetime
    task_id: str | None = None
    branch: str | None = None
    item_updated_at: datetime | None = None
    last_status: str | None = None
    # The reason worc's validation gate refused the task, as worc published it. Held so the item
    # can be told why, because a refused task has no worc row of its own to point anybody at.
    validation_reason: str | None = None
    stage: Stage = Stage.IMPLEMENTATION
    # The triage task whose report this implementation task was built from, so the builder can read
    # that report back by name instead of inferring which task it belonged to from the sequence.
    research_task_id: str | None = None
    # What the triage report said, for the outcome the item is shown. Separate from
    # `validation_reason` because their provenance differs: that one is worc's own vocabulary,
    # this one is prose an agent wrote.
    research_note: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_merged: bool = False
    # Whether the connector has seen the thing that turns a later trigger into a *new* request:
    # the trigger label taken off, or the item closed by the connector itself. Without it a
    # terminal row whose item still carries the label would start a fresh task on every tick.
    retrigger_armed: bool = False


def _iso(value: datetime | None) -> str | None:
    """A stamp as stored: ISO-8601 in UTC, so rows sort and compare across hosts and timezones."""
    return None if value is None else value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    """A stored stamp back as a timezone-aware datetime; an unreadable value reads as absent.

    The store is a cache, so a value this build cannot parse is worth exactly one re-derivation, not
    an exception that would stop a tick.
    """
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row(record: sqlite3.Row) -> ItemRow:
    """One database record as an :class:`ItemRow`."""
    created = _parse(record["created_at"]) or datetime.now(tz=UTC)
    return ItemRow(
        tracker=record["tracker"],
        item_id=record["item_id"],
        seq=record["seq"],
        phase=Phase(record["phase"]),
        created_at=created,
        updated_at=_parse(record["updated_at"]) or created,
        task_id=record["task_id"],
        branch=record["branch"],
        item_updated_at=_parse(record["item_updated_at"]),
        last_status=record["last_status"],
        validation_reason=record["validation_reason"],
        stage=Stage(record["stage"]),
        research_task_id=record["research_task_id"],
        research_note=record["research_note"],
        pr_number=record["pr_number"],
        pr_url=record["pr_url"],
        pr_merged=bool(record["pr_merged"]),
        retrigger_armed=bool(record["retrigger_armed"]),
    )


class StateStore:
    """The rows and the bookkeeping values, in one SQLite file under the connector's home.

    Open it for a real tick with the constructor; open it for a dry run with :meth:`read_only`,
    which promises — and enforces — that nothing is written, including the database file itself. A
    row is never patched in place: callers copy it, change the copy and :meth:`save` it, so the
    store's surface stays closed as the connector grows phases.
    """

    def __init__(self, path: Path, *, _connection: sqlite3.Connection | None = None) -> None:
        self._read_only = _connection is not None
        if _connection is not None:
            self._connection = _connection
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        _ensure_schema(self._connection)

    @classmethod
    def read_only(cls, path: Path) -> Self:
        """A store that refuses every write, for a dry run that must leave no trace.

        A database that does not exist yet, or one written by another schema, is answered from an
        empty in-memory copy: a dry run may not create the file, and inventing rows would be worse
        than reporting none, since the caller is about to print a plan and change nothing.
        """
        connection = sqlite3.connect(path) if path.is_file() else sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        if not _schema_is_current(connection):
            connection.close()
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.executescript(_SCHEMA)
        return cls(path, _connection=connection)

    def close(self) -> None:
        """Close the connection; a closed store is not reused."""
        self._connection.close()

    def rows(self) -> list[ItemRow]:
        """Every cached row, oldest sighting first — the order ``status`` reports them in."""
        cursor = self._connection.execute(
            "SELECT * FROM items ORDER BY created_at, item_id, seq",
        )
        return [_row(record) for record in cursor.fetchall()]

    def latest_row(self, tracker: str, item_id: str) -> ItemRow | None:
        """The item's highest-sequence row, or ``None`` if the item has never been seen.

        The highest sequence is the live one: a re-trigger adds a row rather than editing the
        previous attempt, so the history of an item stays readable after the fact.
        """
        cursor = self._connection.execute(
            "SELECT * FROM items WHERE tracker = ? AND item_id = ? ORDER BY seq DESC LIMIT 1",
            (tracker, item_id),
        )
        record = cursor.fetchone()
        return None if record is None else _row(record)

    def save(self, row: ItemRow) -> None:
        """Insert ``row``, or update the one with the same tracker, item and sequence.

        An upsert on the primary key rather than ``INSERT OR REPLACE``: replace would resolve a
        collision on the unique ``task_id`` by deleting whichever row already held it, and a task
        id is never reused, so a collision has to be loud rather than silently destructive.
        """
        self._require_writable()
        self._connection.execute(
            """
            INSERT INTO items (
                tracker, item_id, seq, task_id, branch, phase, item_updated_at, last_status,
                validation_reason, stage, research_task_id, research_note,
                pr_number, pr_url, pr_merged, retrigger_armed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracker, item_id, seq) DO UPDATE SET
                task_id = excluded.task_id,
                branch = excluded.branch,
                phase = excluded.phase,
                item_updated_at = excluded.item_updated_at,
                last_status = excluded.last_status,
                validation_reason = excluded.validation_reason,
                stage = excluded.stage,
                research_task_id = excluded.research_task_id,
                research_note = excluded.research_note,
                pr_number = excluded.pr_number,
                pr_url = excluded.pr_url,
                pr_merged = excluded.pr_merged,
                retrigger_armed = excluded.retrigger_armed,
                updated_at = excluded.updated_at
            """,
            (
                row.tracker,
                row.item_id,
                row.seq,
                row.task_id,
                row.branch,
                str(row.phase),
                _iso(row.item_updated_at),
                row.last_status,
                row.validation_reason,
                str(row.stage),
                row.research_task_id,
                row.research_note,
                row.pr_number,
                row.pr_url,
                int(row.pr_merged),
                int(row.retrigger_armed),
                _iso(row.created_at),
                _iso(row.updated_at),
            ),
        )
        self._connection.commit()

    def meta(self, key: str) -> str | None:
        """One bookkeeping value, or ``None`` when it was never written."""
        cursor = self._connection.execute("SELECT value FROM meta WHERE key = ?", (key,))
        record = cursor.fetchone()
        return None if record is None else str(record["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Write one bookkeeping value."""
        self._require_writable()
        self._connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._connection.commit()

    def _require_writable(self) -> None:
        """Refuse a write on a read-only store, so a dry run fails loudly instead of quietly."""
        if self._read_only:
            raise StateError("the state store is open read-only and must not be written")


def read_watermark(store: StateStore) -> datetime | None:
    """The newest item update a completed tick has seen, or ``None`` before the first one.

    A bookkeeping value with a codec rather than a method on the store: the watermark is the loop's
    contract, and keeping its key and its timezone rule in one place next to the loop's own reader
    is what stops a second spelling of either from appearing elsewhere.
    """
    return _parse(store.meta(META_WATERMARK))


def write_watermark(store: StateStore, value: datetime) -> None:
    """Record ``value`` as the newest update seen, which is where the next tick lists from."""
    stored = _iso(value)
    if stored is None:  # pragma: no cover - a datetime always renders
        return
    store.set_meta(META_WATERMARK, stored)


def _schema_is_current(connection: sqlite3.Connection) -> bool:
    """Whether the connected database already carries this build's schema."""
    try:
        cursor = connection.execute("SELECT value FROM meta WHERE key = ?", (_META_SCHEMA_VERSION,))
    except sqlite3.Error:
        return False
    record = cursor.fetchone()
    return record is not None and str(record["value"]) == str(SCHEMA_VERSION)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create this build's schema, discarding a database written by any other version.

    Discarding rather than migrating is the deliberate choice: the rows are re-derivable from the
    tracker, worc's listing and the lifecycle folders within one tick, so carrying migration code
    for a disposable cache would buy nothing and could be wrong in a way that is hard to see.
    """
    if _schema_is_current(connection):
        return
    connection.executescript("DROP TABLE IF EXISTS items; DROP TABLE IF EXISTS meta;")
    connection.executescript(_SCHEMA)
    connection.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        (_META_SCHEMA_VERSION, str(SCHEMA_VERSION)),
    )
    connection.commit()
