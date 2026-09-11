"""The state store: rows, the watermark, and the two promises the cache makes.

The promises are that deleting the database is safe and that a dry run cannot touch it. Both are
asserted here, because both are relied on elsewhere: the loop re-derives rows every tick, and the
dry run's "writes nothing" is an operator-facing guarantee.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from worc_connect.core.state import (
    ItemRow,
    Phase,
    StateError,
    StateStore,
    read_watermark,
    write_watermark,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def row(item_id: str = "142", *, seq: int = 1, phase: Phase = Phase.GATED) -> ItemRow:
    return ItemRow(
        tracker="github",
        item_id=item_id,
        seq=seq,
        phase=phase,
        created_at=NOW,
        updated_at=NOW,
        item_updated_at=NOW,
    )


def test_a_saved_row_comes_back_with_its_types(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.save(row())

    stored = store.latest_row("github", "142")

    assert stored is not None
    assert stored.phase is Phase.GATED
    assert stored.item_updated_at == NOW
    assert stored.pr_merged is False
    store.close()


def test_an_unseen_item_has_no_row(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")

    assert store.latest_row("github", "999") is None
    store.close()


def test_the_latest_row_is_the_highest_sequence(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.save(row(phase=Phase.DONE))
    store.save(row(seq=2, phase=Phase.QUEUED))

    stored = store.latest_row("github", "142")

    assert stored is not None
    assert (stored.seq, stored.phase) == (2, Phase.QUEUED)
    assert len(store.rows()) == 2
    store.close()


def test_saving_the_same_key_replaces_the_row(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.save(row())
    store.save(row(phase=Phase.QUEUED))

    assert len(store.rows()) == 1
    store.close()


def test_two_items_cannot_share_a_task_id(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.save(ItemRow(**{**vars(row("142")), "task_id": "gh-142"}))

    with pytest.raises(sqlite3.IntegrityError):
        store.save(ItemRow(**{**vars(row("143")), "task_id": "gh-142"}))
    store.close()


def test_the_watermark_round_trips_as_an_aware_timestamp(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")

    assert read_watermark(store) is None
    write_watermark(store, NOW)

    assert read_watermark(store) == NOW
    store.close()


def test_an_unreadable_watermark_reads_as_absent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.set_meta("watermark", "not-a-timestamp")

    assert read_watermark(store) is None
    store.close()


def test_a_database_from_another_schema_is_discarded_rather_than_migrated(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    stale = sqlite3.connect(path)
    stale.executescript(
        "CREATE TABLE items (legacy TEXT); CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    stale.execute("INSERT INTO meta VALUES ('schema_version', '0')")
    stale.commit()
    stale.close()

    store = StateStore(path)
    store.save(row())

    assert store.latest_row("github", "142") is not None
    store.close()


def test_a_read_only_store_of_a_missing_database_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    store = StateStore.read_only(path)

    assert store.rows() == []
    assert read_watermark(store) is None
    assert not path.exists()
    store.close()


def test_a_read_only_store_sees_the_rows_that_are_there(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    writable = StateStore(path)
    writable.save(row())
    write_watermark(writable, NOW)
    writable.close()

    store = StateStore.read_only(path)

    assert [stored.item_id for stored in store.rows()] == ["142"]
    assert read_watermark(store) == NOW
    store.close()


def test_a_read_only_store_refuses_every_write(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    StateStore(path).close()
    store = StateStore.read_only(path)

    with pytest.raises(StateError):
        store.save(row())
    with pytest.raises(StateError):
        store.set_meta("watermark", NOW.isoformat())
    store.close()


def test_a_read_only_store_of_a_foreign_schema_reports_nothing(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    stale = sqlite3.connect(path)
    stale.executescript("CREATE TABLE something_else (x TEXT);")
    stale.commit()
    stale.close()

    store = StateStore.read_only(path)

    assert store.rows() == []
    store.close()


def test_the_database_is_created_with_its_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "home" / "state.db"

    StateStore(path).close()

    assert path.is_file()
