"""Fail-closed SQLite schema inspection and additive column migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def _ident(value: str) -> str:
    text = str(value or "").strip()
    if not text or not text.replace("_", "").isalnum() or text[0].isdigit():
        raise ValueError(f"Unsafe SQLite identifier: {value!r}")
    return text


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table),),
    ).fetchone()
    return row is not None


def _row_name(row: sqlite3.Row | tuple[object, ...]) -> object:
    if isinstance(row, sqlite3.Row):
        return row["name"]
    return row[1]


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    table = _ident(table)
    if not table_exists(conn, table):
        raise RuntimeError(f"Required SQLite table is missing: {table}")
    return {str(_row_name(row)) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def require_columns(conn: sqlite3.Connection, *, table: str, expected: Iterable[str]) -> None:
    """Fail startup unless every required application column is really present."""
    required = {_ident(column) for column in expected}
    missing = sorted(required - column_names(conn, table))
    if missing:
        raise RuntimeError(f"SQLite schema is missing required columns for {table}: {', '.join(missing)}")


def add_column_if_missing(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    declaration: str,
) -> bool:
    """Add one missing column and verify it; propagate every SQLite failure."""
    table = _ident(table)
    column = _ident(column)
    if column in column_names(conn, table):
        return False
    declaration = str(declaration or "").strip()
    if not declaration or ";" in declaration:
        raise ValueError(f"Unsafe SQLite column declaration: {declaration!r}")
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    require_columns(conn, table=table, expected=(column,))
    return True


__all__ = ["add_column_if_missing", "column_names", "require_columns", "table_exists"]
