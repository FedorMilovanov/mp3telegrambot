"""Numbered, transactional and fail-closed SQLite migrations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable

DATABASE_MIGRATION_POLICY = "numbered-transactional-sqlite-migrations-v1"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]

    @property
    def checksum(self) -> str:
        payload = f"{self.version}:{self.name}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class MigrationReport:
    database: str
    applied: tuple[int, ...]
    current_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": DATABASE_MIGRATION_POLICY,
            "database": self.database,
            "applied": list(self.applied),
            "current_version": self.current_version,
        }


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ident(value: str) -> str:
    text = str(value or "").strip()
    if not text or not text.replace("_", "").isalnum() or text[0].isdigit():
        raise ValueError(f"Unsafe SQLite identifier: {value!r}")
    return text


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table),),
    ).fetchone()
    return row is not None


def _row_value(row: sqlite3.Row | tuple[object, ...], key: str, index: int) -> object:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[index]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    table = _ident(table)
    if not _table_exists(conn, table):
        raise RuntimeError(f"Required SQLite table is missing: {table}")
    return {
        str(_row_value(row, "name", 1))
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def add_column_if_missing(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    declaration: str,
) -> bool:
    table = _ident(table)
    column = _ident(column)
    if column in _columns(conn, table):
        return False
    declaration = str(declaration or "").strip()
    if not declaration or ";" in declaration:
        raise ValueError(f"Unsafe SQLite column declaration: {declaration!r}")
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    if column not in _columns(conn, table):
        raise RuntimeError(f"SQLite did not add expected column {table}.{column}")
    return True


def _ensure_journal(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            scope TEXT NOT NULL,
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at INTEGER NOT NULL,
            PRIMARY KEY(scope, version)
        )
        """
    )


def run_migrations(
    path: Path,
    *,
    scope: str,
    migrations: Iterable[Migration],
) -> MigrationReport:
    scope = str(scope or "").strip()
    if not scope:
        raise ValueError("Migration scope is required.")
    ordered = tuple(sorted(migrations, key=lambda item: item.version))
    versions = [item.version for item in ordered]
    if versions != sorted(set(versions)) or any(version <= 0 for version in versions):
        raise ValueError("Migration versions must be unique positive integers.")

    applied_now: list[int] = []
    with _connect(Path(path)) as conn:
        # The journal is infrastructure, not an application migration. Persist it
        # first so a rolled-back migration still leaves auditable empty history.
        _ensure_journal(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = {
                int(row["version"]): row
                for row in conn.execute(
                    "SELECT version, name, checksum FROM schema_migrations WHERE scope=?",
                    (scope,),
                ).fetchall()
            }
            known = {migration.version for migration in ordered}
            unknown = sorted(set(existing) - known)
            if unknown:
                raise RuntimeError(
                    f"Database contains unknown {scope} migrations: {unknown}"
                )
            for migration in ordered:
                row = existing.get(migration.version)
                if row is not None:
                    if (
                        str(row["name"]) != migration.name
                        or str(row["checksum"]) != migration.checksum
                    ):
                        raise RuntimeError(
                            f"Migration history mismatch for {scope} v{migration.version}."
                        )
                    continue
                migration.apply(conn)
                conn.execute(
                    """
                    INSERT INTO schema_migrations(
                        scope, version, name, checksum, applied_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scope,
                        migration.version,
                        migration.name,
                        migration.checksum,
                        int(time.time()),
                    ),
                )
                applied_now.append(migration.version)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    return MigrationReport(
        database=str(Path(path).resolve()),
        applied=tuple(applied_now),
        current_version=max(versions, default=0),
    )


def _main_v1_gemini_validation(conn: sqlite3.Connection) -> None:
    add_column_if_missing(
        conn,
        table="gemini_runs",
        column="validation_summary",
        declaration="TEXT DEFAULT ''",
    )


def _main_v2_video_cache(conn: sqlite3.Connection) -> None:
    declarations = {
        "questions": "TEXT DEFAULT '[]'",
        "share_text": "TEXT DEFAULT ''",
        "quotes_tg_url": "TEXT DEFAULT ''",
        "questions_tg_url": "TEXT DEFAULT ''",
        "ai_data": "TEXT DEFAULT ''",
        "telegraph_url": "TEXT DEFAULT ''",
        "analytics_json": "TEXT DEFAULT ''",
        "cache_version": "TEXT DEFAULT ''",
        "prompt_version": "TEXT DEFAULT ''",
        "model_name": "TEXT DEFAULT ''",
        "updated_at": "INTEGER DEFAULT 0",
        "terms_tg_url": "TEXT DEFAULT ''",
        "rutube_url": "TEXT DEFAULT ''",
        "vk_url": "TEXT DEFAULT ''",
        "study_tg_url": "TEXT DEFAULT ''",
        "reflection_tg_url": "TEXT DEFAULT ''",
        "publication_status": "TEXT DEFAULT 'unknown'",
        "publication_missing": "TEXT DEFAULT '[]'",
        "publication_warning": "TEXT DEFAULT ''",
        "livedub_file_id": "TEXT DEFAULT ''",
        "livedub_file_id_version": "TEXT DEFAULT ''",
        "audio_file_id": "TEXT DEFAULT ''",
    }
    for column, declaration in declarations.items():
        add_column_if_missing(
            conn,
            table="video_cache",
            column=column,
            declaration=declaration,
        )


def _main_v3_short_trims(conn: sqlite3.Connection) -> None:
    for column, declaration in (
        ("video_path_nosub", "TEXT DEFAULT ''"),
        ("nosub_expiry", "INTEGER DEFAULT 0"),
        ("source_duration", "INTEGER DEFAULT 0"),
    ):
        add_column_if_missing(
            conn,
            table="short_trims",
            column=column,
            declaration=declaration,
        )


MAIN_MIGRATIONS = (
    Migration(1, "gemini-validation-summary", _main_v1_gemini_validation),
    Migration(2, "video-cache-current-columns", _main_v2_video_cache),
    Migration(3, "short-trims-current-columns", _main_v3_short_trims),
)


def _dub_v1_execution_leases(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dub_execution_leases (
            job_id INTEGER PRIMARY KEY,
            worker_id TEXT NOT NULL,
            runner_pid INTEGER NOT NULL,
            runner_started_at TEXT NOT NULL,
            process_start_token TEXT NOT NULL DEFAULT '',
            process_group_id INTEGER,
            command_fingerprint TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            state TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dub_execution_leases_state_heartbeat
        ON dub_execution_leases(state, heartbeat_at)
        """
    )


DUB_MIGRATIONS = (
    Migration(1, "runner-execution-leases", _dub_v1_execution_leases),
)


def apply_database_migrations(main_module: ModuleType | None = None) -> dict[str, object]:
    """Create legacy base tables, then enforce the strict current schema."""
    from core import database
    from services.dub_studio import DubStore

    initializer = getattr(main_module, "db_init", None) if main_module else None
    if not callable(initializer):
        initializer = database.db_init
    initializer()
    main_report = run_migrations(
        Path(database.DB_PATH),
        scope="main",
        migrations=MAIN_MIGRATIONS,
    )

    store = DubStore()
    # DubStore owns its base tables; connect once before numbered extensions.
    with store.connect() as conn:
        if not _table_exists(conn, "dub_jobs"):
            raise RuntimeError("Dub Studio base schema was not initialized.")
    dub_report = run_migrations(
        Path(store.db_path),
        scope="dub-studio",
        migrations=DUB_MIGRATIONS,
    )
    payload = {
        "policy": DATABASE_MIGRATION_POLICY,
        "main": main_report.as_dict(),
        "dub_studio": dub_report.as_dict(),
    }
    return json.loads(json.dumps(payload, ensure_ascii=False))


__all__ = [
    "DATABASE_MIGRATION_POLICY",
    "DUB_MIGRATIONS",
    "MAIN_MIGRATIONS",
    "Migration",
    "MigrationReport",
    "add_column_if_missing",
    "apply_database_migrations",
    "run_migrations",
]
