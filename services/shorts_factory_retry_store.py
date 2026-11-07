"""Durable bounded retry calendar for real provider-side Factory 503.

Retry windows are **not** individual HTTP attempts. Each resumed HIGH pass
keeps its independently enforced five-actual-request budget. Local circuit
rejections, quota 429, schema failures and uncertain Telegram deliveries never
enter this calendar.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.shorts_factory_delivery_ledger import DEFAULT_JOURNAL_PATH

logger = logging.getLogger(__name__)

MAX_PROVIDER_WINDOWS = 3  # original request + at most two deferred windows
MAX_RETRY_HORIZON_SECONDS = 6 * 3600
RETRY_BASE_DELAYS = (5 * 60, 15 * 60)


@dataclass(frozen=True)
class FactoryDeferredJob:
    scope: str
    chat_id: int
    user_id: int
    source_id: str
    source_url: str
    windows_used: int
    retry_after: float
    stage: str
    worker_owner: str
    worker_pid: int


class FactoryDeferredRetryStore:
    def __init__(self, path: Path = DEFAULT_JOURNAL_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_deferred_jobs (
                scope TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('scheduled','running','done','blocked','canceled','exhausted')
                ),
                windows_used INTEGER NOT NULL,
                stage TEXT NOT NULL,
                created_at REAL NOT NULL,
                deadline_at REAL NOT NULL,
                retry_after REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_actual_requests INTEGER NOT NULL,
                worker_owner TEXT,
                worker_pid INTEGER
            )
            """
        )
        return conn

    @staticmethod
    def _delay(scope: str, used: int) -> float:
        if used < 1 or used >= MAX_PROVIDER_WINDOWS:
            raise ValueError("No more provider windows are permitted")
        base = RETRY_BASE_DELAYS[used - 1]
        variation = (int.from_bytes(hashlib.sha256(f"{scope}:{used}".encode()).digest()[:2], "big") % 21) - 10
        return base * (1.0 + variation / 100.0)

    def record_real_overload(
        self,
        *,
        scope: str,
        chat_id: int,
        user_id: int,
        source_id: str,
        source_url: str,
        stage: str,
        actual_requests: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not scope or chat_id == 0 or user_id <= 0 or not source_id or not source_url:
            raise ValueError("Full Factory job provenance is required for deferred retry")
        if not stage or actual_requests <= 0:
            raise ValueError("Only a confirmed real provider request window may be deferred")
        current = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = conn.execute(
                    """
                    SELECT status,windows_used,created_at,deadline_at,chat_id,user_id,source_id
                    FROM factory_deferred_jobs WHERE scope=?
                    """,
                    (scope,),
                ).fetchone()
                if prior:
                    state, used, created, deadline, prior_chat, prior_user, prior_source = prior
                    if (prior_chat, prior_user, prior_source) != (chat_id, user_id, source_id):
                        raise RuntimeError("Factory retry provenance changed across attempts")
                    if state in ("done", "blocked", "canceled", "exhausted"):
                        conn.execute("COMMIT")
                        return {"status": state, "windows_used": used, "retry_after": 0.0}
                    used += 1
                else:
                    used, created = 1, current
                    deadline = created + MAX_RETRY_HORIZON_SECONDS

                delay = self._delay(scope, used) if used < MAX_PROVIDER_WINDOWS else 0.0
                next_retry = current + delay
                state = "scheduled" if used < MAX_PROVIDER_WINDOWS and next_retry < deadline else "exhausted"
                retry_after = next_retry if state == "scheduled" else 0.0
                conn.execute(
                    """
                    INSERT INTO factory_deferred_jobs(
                        scope,chat_id,user_id,source_id,source_url,status,windows_used,
                        stage,created_at,deadline_at,retry_after,updated_at,last_actual_requests
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope) DO UPDATE SET
                        status=excluded.status,windows_used=excluded.windows_used,
                        stage=excluded.stage,retry_after=excluded.retry_after,
                        updated_at=excluded.updated_at,last_actual_requests=excluded.last_actual_requests,
                        worker_owner=NULL,worker_pid=NULL
                    """,
                    (
                        scope,
                        chat_id,
                        user_id,
                        source_id,
                        source_url,
                        state,
                        used,
                        stage,
                        created,
                        deadline,
                        retry_after,
                        current,
                        actual_requests,
                    ),
                )
                conn.execute("COMMIT")
                return {"status": state, "windows_used": used, "retry_after": retry_after}
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _has_uncertain_send(self, conn: sqlite3.Connection, scope: str) -> bool:
        # The shared delivery schema is created on the original Factory run.
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='factory_delivery'").fetchone()
        if not exists:
            return False
        return (
            conn.execute(
                "SELECT 1 FROM factory_delivery WHERE scope=? AND state='sending' LIMIT 1",
                (scope,),
            ).fetchone()
            is not None
        )

    def _is_active_elsewhere(self, conn: sqlite3.Connection, scope: str) -> bool:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='factory_job_lease'").fetchone()
        if not exists:
            return False
        return conn.execute("SELECT 1 FROM factory_job_lease WHERE scope=?", (scope,)).fetchone() is not None

    def claim_due(self, *, now: float | None = None) -> FactoryDeferredJob | None:
        current = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE factory_deferred_jobs SET status='exhausted',updated_at=?
                    WHERE status='scheduled' AND deadline_at<=?
                    """,
                    (current, current),
                )
                rows = conn.execute(
                    """
                    SELECT scope,chat_id,user_id,source_id,source_url,windows_used,retry_after,stage
                    FROM factory_deferred_jobs
                    WHERE status='scheduled' AND retry_after<=?
                    ORDER BY retry_after ASC LIMIT 12
                    """,
                    (current,),
                ).fetchall()
                for row in rows:
                    scope = str(row[0])
                    if self._has_uncertain_send(conn, scope):
                        conn.execute(
                            "UPDATE factory_deferred_jobs SET status='blocked',updated_at=? WHERE scope=?",
                            (current, scope),
                        )
                        logger.warning("Factory deferred retry blocked: Telegram delivery is unconfirmed")
                        continue
                    if self._is_active_elsewhere(conn, scope):
                        # Do not consume a window when a user manually runs
                        # this exact source in another process.
                        conn.execute(
                            "UPDATE factory_deferred_jobs SET retry_after=?,updated_at=? WHERE scope=?",
                            (current + 60, current, scope),
                        )
                        continue
                    owner = uuid.uuid4().hex
                    pid = os.getpid()
                    updated = conn.execute(
                        """
                        UPDATE factory_deferred_jobs
                        SET status='running',updated_at=?,worker_owner=?,worker_pid=?
                        WHERE scope=? AND status='scheduled'
                        """,
                        (current, owner, pid, scope),
                    )
                    if updated.rowcount == 1:
                        conn.execute("COMMIT")
                        return FactoryDeferredJob(
                            scope=str(row[0]),
                            chat_id=int(row[1]),
                            user_id=int(row[2]),
                            source_id=str(row[3]),
                            source_url=str(row[4]),
                            windows_used=int(row[5]),
                            retry_after=float(row[6]),
                            stage=str(row[7]),
                            worker_owner=owner,
                            worker_pid=pid,
                        )
                conn.execute("COMMIT")
                return None
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def mark_completed(self, scope: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE factory_deferred_jobs SET status='done',retry_after=0,updated_at=?
                WHERE scope=? AND status IN ('scheduled','running','blocked','exhausted','canceled')
                """,
                (time.time(), scope),
            )

    def mark_blocked(self, scope: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE factory_deferred_jobs SET status='blocked',retry_after=0,updated_at=?
                WHERE scope=? AND status='running'
                """,
                (time.time(), scope),
            )

    def cancel(self, scope: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT status FROM factory_deferred_jobs WHERE scope=?", (scope,)).fetchone()
                if row is None:
                    raise ValueError("Factory deferred job does not exist")
                if row[0] not in ("scheduled", "blocked"):
                    raise RuntimeError(
                        f"Cannot cancel Factory job in state {row[0]}; running work must be stopped first"
                    )
                conn.execute(
                    """
                    UPDATE factory_deferred_jobs SET status='canceled',retry_after=0,updated_at=?
                    WHERE scope=? AND status IN ('scheduled','blocked')
                    """,
                    (time.time(), scope),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def operator_reset_crashed_running(
        self,
        scope: str,
        *,
        expected_owner: str,
        expected_pid: int,
        verified_stopped: bool,
        now: float | None = None,
    ) -> None:
        if not verified_stopped or not expected_owner or expected_pid <= 0:
            raise ValueError("Manual recovery requires exact owner/PID and independent proof the worker stopped")
        current = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status,deadline_at,worker_owner,worker_pid FROM factory_deferred_jobs WHERE scope=?",
                    (scope,),
                ).fetchone()
                if row is None or row[0] != "running":
                    raise RuntimeError("Only a crashed running Factory retry can be reconciled")
                if (row[2], row[3]) != (expected_owner, expected_pid):
                    raise RuntimeError("Factory deferred running owner/PID changed since operator inspection")
                if self._is_active_elsewhere(conn, scope):
                    raise RuntimeError("Factory job lease still exists; independently reconcile that first")
                if self._has_uncertain_send(conn, scope):
                    raise RuntimeError("Uncertain Telegram sends must be reconciled before retry")
                state = "scheduled" if current < row[1] else "exhausted"
                changed = conn.execute(
                    """
                    UPDATE factory_deferred_jobs
                    SET status=?,retry_after=?,updated_at=?,worker_owner=NULL,worker_pid=NULL
                    WHERE scope=? AND status='running' AND worker_owner=? AND worker_pid=?
                    """,
                    (state, current if state == "scheduled" else 0.0, current, scope, expected_owner, expected_pid),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("Factory deferred running job changed during operator reconciliation")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def status(self, scope: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT status,windows_used,retry_after,stage,created_at,deadline_at,
                    last_actual_requests,chat_id,user_id,source_id,worker_owner,worker_pid
                FROM factory_deferred_jobs WHERE scope=?
                """,
                (scope,),
            ).fetchone()
            if row is None:
                return None
            return {
                "status": row[0],
                "windows_used": row[1],
                "retry_after": row[2],
                "stage": row[3],
                "created_at": row[4],
                "deadline_at": row[5],
                "last_actual_requests": row[6],
                "chat_id": row[7],
                "user_id": row[8],
                "source_id": row[9],
                "worker_owner": row[10],
                "worker_pid": row[11],
            }


__all__ = ["FactoryDeferredRetryStore", "FactoryDeferredJob", "MAX_PROVIDER_WINDOWS"]
