from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.database_migrations import (
    DATABASE_MIGRATION_POLICY,
    Migration,
    add_column_if_missing,
    run_migrations,
)


def _base_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE video_cache(video_id TEXT PRIMARY KEY);
            CREATE TABLE gemini_runs(id INTEGER PRIMARY KEY);
            CREATE TABLE short_trims(short_id TEXT PRIMARY KEY);
            """
        )


def test_numbered_migration_is_applied_once(tmp_path: Path):
    path = tmp_path / "main.db"
    _base_database(path)

    def add_value(conn: sqlite3.Connection) -> None:
        assert add_column_if_missing(
            conn,
            table="video_cache",
            column="value",
            declaration="TEXT DEFAULT ''",
        )

    migration = Migration(1, "add-value", add_value)
    first = run_migrations(path, scope="test", migrations=(migration,))
    second = run_migrations(path, scope="test", migrations=(migration,))

    assert first.as_dict()["policy"] == DATABASE_MIGRATION_POLICY
    assert first.applied == (1,)
    assert second.applied == ()
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(video_cache)")}
        assert "value" in columns
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE scope='test'"
        ).fetchone()[0] == 1


def test_unknown_operational_error_is_not_swallowed(tmp_path: Path):
    path = tmp_path / "locked.db"
    _base_database(path)

    def invalid(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE missing_table ADD COLUMN value TEXT")

    with pytest.raises(sqlite3.OperationalError):
        run_migrations(
            path,
            scope="test",
            migrations=(Migration(1, "invalid", invalid),),
        )

    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE scope='test'"
        ).fetchone()[0] == 0


def test_history_checksum_mismatch_fails_closed(tmp_path: Path):
    path = tmp_path / "history.db"
    _base_database(path)
    migration = Migration(1, "one", lambda _conn: None)
    run_migrations(path, scope="test", migrations=(migration,))

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum='tampered' WHERE scope='test' AND version=1"
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="history mismatch"):
        run_migrations(path, scope="test", migrations=(migration,))


def test_only_real_duplicate_column_is_skipped(tmp_path: Path):
    path = tmp_path / "columns.db"
    _base_database(path)
    with sqlite3.connect(path) as conn:
        assert add_column_if_missing(
            conn,
            table="video_cache",
            column="value",
            declaration="TEXT DEFAULT ''",
        )
        assert not add_column_if_missing(
            conn,
            table="video_cache",
            column="value",
            declaration="TEXT DEFAULT ''",
        )
        with pytest.raises(RuntimeError, match="Required SQLite table"):
            add_column_if_missing(
                conn,
                table="missing",
                column="value",
                declaration="TEXT",
            )
