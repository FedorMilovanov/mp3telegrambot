from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import core.database as database
import core.generated_pages as generated_pages
import core.observability as observability
import services.database_migrations as migrations
import services.dub_studio as dub_studio
from services.database_migrations import (
    DATABASE_MIGRATION_POLICY,
    Migration,
    MigrationReport,
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


def test_numbered_migration_is_applied_once(tmp_path: Path) -> None:
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
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE scope='test'").fetchone()[0] == 1


def test_unknown_operational_error_is_not_swallowed(tmp_path: Path) -> None:
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
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE scope='test'").fetchone()[0] == 0


def test_history_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    _base_database(path)
    migration = Migration(1, "one", lambda _conn: None)
    run_migrations(path, scope="test", migrations=(migration,))

    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE scope='test' AND version=1")
        conn.commit()

    with pytest.raises(RuntimeError, match="history mismatch"):
        run_migrations(path, scope="test", migrations=(migration,))


def test_apply_database_migrations_returns_json_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main_path = tmp_path / "main.db"
    dub_path = tmp_path / "dub.db"
    with sqlite3.connect(dub_path) as conn:
        conn.execute("CREATE TABLE dub_jobs(id INTEGER PRIMARY KEY)")

    class Store:
        def __init__(self) -> None:
            self.db_path = dub_path

        def connect(self) -> sqlite3.Connection:
            return sqlite3.connect(dub_path)

    def report(path: Path, *, scope: str, migrations: tuple[Migration, ...]) -> MigrationReport:
        assert Path(path) in {main_path, dub_path}
        assert migrations == ()
        return MigrationReport(database=scope, applied=(1,), current_version=1)

    monkeypatch.setattr(database, "DB_PATH", main_path)
    monkeypatch.setattr(dub_studio, "DubStore", Store)
    monkeypatch.setattr(migrations, "MAIN_MIGRATIONS", ())
    monkeypatch.setattr(migrations, "DUB_MIGRATIONS", ())
    monkeypatch.setattr(migrations, "run_migrations", report)

    result = migrations.apply_database_migrations()

    assert result == {
        "policy": DATABASE_MIGRATION_POLICY,
        "main": {
            "policy": DATABASE_MIGRATION_POLICY,
            "database": "main",
            "applied": [1],
            "current_version": 1,
        },
        "dub_studio": {
            "policy": DATABASE_MIGRATION_POLICY,
            "database": "dub-studio",
            "applied": [1],
            "current_version": 1,
        },
    }


def test_only_real_duplicate_column_is_skipped(tmp_path: Path) -> None:
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


def _connection_with_alter_error(path: Path, message: str) -> sqlite3.Connection:
    class FailingAlterConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            if sql.lstrip().upper().startswith("ALTER TABLE"):
                raise sqlite3.OperationalError(message)
            return super().execute(sql, parameters)

    return sqlite3.connect(path, factory=FailingAlterConnection)


def _connection_with_introspection_error(path: Path) -> sqlite3.Connection:
    class FailingIntrospectionConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            if sql.lstrip().upper().startswith("PRAGMA TABLE_INFO"):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, parameters)

    return sqlite3.connect(path, factory=FailingIntrospectionConnection)


@pytest.mark.parametrize("message", ["database is locked", "readonly database", "disk I/O error"])
def test_unexpected_alter_failures_propagate(tmp_path: Path, message: str) -> None:
    path = tmp_path / "operational.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE video_cache(video_id TEXT PRIMARY KEY)")

    with _connection_with_alter_error(path, message) as conn:
        with pytest.raises(sqlite3.OperationalError, match=message):
            add_column_if_missing(
                conn,
                table="video_cache",
                column="value",
                declaration="TEXT DEFAULT ''",
            )

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(video_cache)")}
        assert "value" not in columns


def test_db_init_is_idempotent_and_asserts_current_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.db"
    monkeypatch.setattr(database, "DB_PATH", path)

    database.db_init()
    database.db_init()

    with sqlite3.connect(path) as conn:
        video_columns = {row[1] for row in conn.execute("PRAGMA table_info(video_cache)")}
        trim_columns = {row[1] for row in conn.execute("PRAGMA table_info(short_trims)")}
        gemini_columns = {row[1] for row in conn.execute("PRAGMA table_info(gemini_runs)")}
    assert {"analytics_json", "audio_file_id", "livedub_file_id_version"} <= video_columns
    assert {"video_path_nosub", "nosub_expiry", "source_duration"} <= trim_columns
    assert "validation_summary" in gemini_columns


def test_db_init_does_not_hide_operational_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.db"
    conn = _connection_with_alter_error(path, "readonly database")

    def failing_conn(_db_path: str | Path | None = None) -> sqlite3.Connection:
        return conn

    monkeypatch.setattr(database, "_db_conn", failing_conn)
    with pytest.raises(sqlite3.OperationalError, match="readonly database"):
        database.db_init()
    conn.close()


@pytest.mark.parametrize(
    "ensure_schema",
    [generated_pages._ensure_schema, observability._ensure_gemini_runs_table],
    ids=["generated-pages", "gemini-observability"],
)
def test_schema_owners_propagate_introspection_failures(
    tmp_path: Path,
    ensure_schema: Callable[[sqlite3.Connection], None],
) -> None:
    with _connection_with_introspection_error(tmp_path / "schema.db") as conn:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            ensure_schema(conn)
