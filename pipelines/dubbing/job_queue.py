from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.dub_projects import (
    DubProjectError,
    load_project,
    projects_root,
    validate_project_id,
)


SCHEMA_VERSION = 1
DEFAULT_LEASE_SECONDS = 180
VALID_STATES = {
    "queued",
    "running",
    "paused",
    "failed",
    "completed",
    "cancelled",
}


@dataclass(frozen=True)
class DubQueueJob:
    project_id: str
    owner_user_id: int
    priority: int
    state: str
    stage: str
    attempts: int
    lease_owner: str | None
    lease_expires_at: float | None
    enqueued_at: float
    updated_at: float
    payload: dict[str, Any]
    last_error: str | None


class DubQueueError(RuntimeError):
    pass


def queue_db_path() -> Path:
    configured = os.getenv("DUB_QUEUE_DB", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else projects_root() / "queue.sqlite3"
    )


def _connect() -> sqlite3.Connection:
    path = queue_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_queue() -> None:
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dub_jobs (
                project_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                owner_user_id INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                state TEXT NOT NULL,
                stage TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_expires_at REAL,
                enqueued_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                payload_json TEXT NOT NULL,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dub_jobs_claim
            ON dub_jobs(state, priority DESC, enqueued_at ASC);
            """
        )


def _decode(row: sqlite3.Row | None) -> DubQueueJob | None:
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return DubQueueJob(
        project_id=str(row["project_id"]),
        owner_user_id=int(row["owner_user_id"]),
        priority=int(row["priority"]),
        state=str(row["state"]),
        stage=str(row["stage"]),
        attempts=int(row["attempts"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] else None,
        lease_expires_at=(
            float(row["lease_expires_at"])
            if row["lease_expires_at"] is not None
            else None
        ),
        enqueued_at=float(row["enqueued_at"]),
        updated_at=float(row["updated_at"]),
        payload=payload if isinstance(payload, dict) else {},
        last_error=str(row["last_error"]) if row["last_error"] else None,
    )


def get_job(project_id: str) -> DubQueueJob | None:
    project_id = validate_project_id(project_id)
    init_queue()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM dub_jobs WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return _decode(row)


def _default_priority(manifest: dict[str, Any]) -> int:
    profile = str((manifest.get("production") or {}).get("profile") or "")
    return 100 if profile == "shorts_premium" else 20


def enqueue_project(
    project_id: str,
    *,
    requested_by_user_id: int,
    priority: int | None = None,
) -> DubQueueJob:
    project_id = validate_project_id(project_id)
    manifest = load_project(project_id)
    owner = int(manifest.get("owner_user_id") or 0)
    if owner != int(requested_by_user_id):
        raise DubProjectError("Поставить проект в очередь может только владелец.")
    production = manifest.get("production") or {}
    preflight = manifest.get("preflight") or {}
    translation = manifest.get("translation") or {}
    if manifest.get("status") != "ready_for_production" or not production.get("ready"):
        raise DubQueueError("Проект ещё не прошёл успешный preflight.")
    if not preflight.get("ok"):
        raise DubQueueError("В manifest отсутствует успешный preflight.")
    if preflight.get("translation_contract_sha256") != translation.get(
        "contract_sha256"
    ):
        raise DubQueueError(
            "Перевод изменился после preflight; выполните проверку готовности снова."
        )

    now = time.time()
    selected_priority = (
        max(-1000, min(int(priority), 1000))
        if priority is not None
        else _default_priority(manifest)
    )
    payload = {
        "project_id": project_id,
        "translation_contract_sha256": translation.get("contract_sha256"),
        "preflight_checked_at": preflight.get("checked_at"),
        "profile": production.get("profile"),
    }
    init_queue()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT state FROM dub_jobs WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if current and str(current["state"]) in {"queued", "running"}:
            connection.execute("ROLLBACK")
            existing = get_job(project_id)
            if existing is None:
                raise DubQueueError("Очередь изменилась во время постановки задачи.")
            return existing
        connection.execute(
            """
            INSERT INTO dub_jobs (
                project_id, schema_version, owner_user_id, priority, state,
                stage, attempts, lease_owner, lease_expires_at, enqueued_at,
                updated_at, payload_json, last_error
            ) VALUES (?, ?, ?, ?, 'queued', 'pending', 0, NULL, NULL, ?, ?, ?, NULL)
            ON CONFLICT(project_id) DO UPDATE SET
                schema_version = excluded.schema_version,
                owner_user_id = excluded.owner_user_id,
                priority = excluded.priority,
                state = 'queued',
                stage = 'pending',
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json,
                last_error = NULL
            """,
            (
                project_id,
                SCHEMA_VERSION,
                owner,
                selected_priority,
                now,
                now,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        connection.execute("COMMIT")
    job = get_job(project_id)
    if job is None:
        raise DubQueueError("Не удалось прочитать созданную задачу.")
    return job


def _requeue_expired(connection: sqlite3.Connection, now: float) -> int:
    cursor = connection.execute(
        """
        UPDATE dub_jobs
        SET state = 'queued',
            stage = CASE WHEN stage = 'pending' THEN stage ELSE 'resume' END,
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = ?,
            last_error = CASE
                WHEN last_error IS NULL OR last_error = ''
                THEN 'Worker lease expired; job returned to queue.'
                ELSE last_error
            END
        WHERE state = 'running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < ?
        """,
        (now, now),
    )
    return int(cursor.rowcount or 0)


def claim_next(
    worker_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> DubQueueJob | None:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise DubQueueError("worker_id не задан.")
    lease_seconds = max(30, min(int(lease_seconds), 3600))
    now = time.time()
    init_queue()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _requeue_expired(connection, now)
        row = connection.execute(
            """
            SELECT project_id
            FROM dub_jobs
            WHERE state = 'queued'
            ORDER BY priority DESC, enqueued_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None
        project_id = str(row["project_id"])
        cursor = connection.execute(
            """
            UPDATE dub_jobs
            SET state = 'running',
                stage = CASE WHEN stage = 'pending' THEN 'starting' ELSE stage END,
                attempts = attempts + 1,
                lease_owner = ?,
                lease_expires_at = ?,
                updated_at = ?
            WHERE project_id = ? AND state = 'queued'
            """,
            (worker_id, now + lease_seconds, now, project_id),
        )
        if cursor.rowcount != 1:
            connection.execute("ROLLBACK")
            return None
        claimed = connection.execute(
            "SELECT * FROM dub_jobs WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        connection.execute("COMMIT")
    return _decode(claimed)


def heartbeat(
    project_id: str,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    stage: str | None = None,
) -> DubQueueJob:
    project_id = validate_project_id(project_id)
    worker_id = str(worker_id or "").strip()
    lease_seconds = max(30, min(int(lease_seconds), 3600))
    now = time.time()
    init_queue()
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE dub_jobs
            SET lease_expires_at = ?,
                updated_at = ?,
                stage = COALESCE(?, stage)
            WHERE project_id = ?
              AND state = 'running'
              AND lease_owner = ?
            """,
            (
                now + lease_seconds,
                now,
                str(stage).strip() if stage else None,
                project_id,
                worker_id,
            ),
        )
        if cursor.rowcount != 1:
            raise DubQueueError(
                "Lease задачи потерян или принадлежит другому worker."
            )
    job = get_job(project_id)
    if job is None:
        raise DubQueueError("Задача исчезла после heartbeat.")
    return job


def _finish(
    project_id: str,
    *,
    worker_id: str,
    state: str,
    stage: str,
    error: str | None = None,
) -> DubQueueJob:
    project_id = validate_project_id(project_id)
    if state not in {"completed", "failed", "paused", "cancelled", "queued"}:
        raise DubQueueError(f"Недопустимое конечное состояние: {state}")
    now = time.time()
    init_queue()
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE dub_jobs
            SET state = ?,
                stage = ?,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?,
                last_error = ?
            WHERE project_id = ?
              AND (
                    lease_owner = ?
                    OR state IN ('queued', 'paused', 'failed')
                  )
            """,
            (
                state,
                stage,
                now,
                str(error)[:4000] if error else None,
                project_id,
                str(worker_id or ""),
            ),
        )
        if cursor.rowcount != 1:
            raise DubQueueError(
                "Задачу нельзя завершить: lease принадлежит другому worker."
            )
    job = get_job(project_id)
    if job is None:
        raise DubQueueError("Задача исчезла после смены состояния.")
    return job


def complete(project_id: str, *, worker_id: str) -> DubQueueJob:
    return _finish(
        project_id,
        worker_id=worker_id,
        state="completed",
        stage="completed",
    )


def fail(
    project_id: str,
    *,
    worker_id: str,
    error: str,
    retryable: bool,
) -> DubQueueJob:
    return _finish(
        project_id,
        worker_id=worker_id,
        state="queued" if retryable else "failed",
        stage="resume" if retryable else "failed",
        error=error,
    )


def pause(project_id: str, *, worker_id: str) -> DubQueueJob:
    return _finish(
        project_id,
        worker_id=worker_id,
        state="paused",
        stage="paused",
    )


def cancel(project_id: str, *, requested_by_user_id: int) -> DubQueueJob:
    project_id = validate_project_id(project_id)
    manifest = load_project(project_id)
    if int(manifest.get("owner_user_id") or 0) != int(requested_by_user_id):
        raise DubQueueError("Отменить задачу может только владелец проекта.")
    now = time.time()
    init_queue()
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE dub_jobs
            SET state = 'cancelled',
                stage = 'cancelled',
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE project_id = ?
              AND state != 'completed'
            """,
            (now, project_id),
        )
        if cursor.rowcount != 1:
            raise DubQueueError("Задача не найдена или уже завершена.")
    job = get_job(project_id)
    if job is None:
        raise DubQueueError("Задача исчезла после отмены.")
    return job


def list_jobs(*, states: set[str] | None = None, limit: int = 100) -> list[DubQueueJob]:
    init_queue()
    limit = max(1, min(int(limit), 1000))
    selected = {
        str(state) for state in (states or set()) if state in VALID_STATES
    }
    with _connect() as connection:
        if selected:
            placeholders = ",".join("?" for _ in selected)
            rows = connection.execute(
                f"""
                SELECT * FROM dub_jobs
                WHERE state IN ({placeholders})
                ORDER BY priority DESC, enqueued_at ASC
                LIMIT ?
                """,
                (*sorted(selected), limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM dub_jobs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [job for row in rows if (job := _decode(row)) is not None]
