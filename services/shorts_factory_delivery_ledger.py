"""Crash-safe Telegram delivery journal for user-triggered Shorts Factory jobs.

Telegram Bot API does not accept an idempotency key for sendVideo/sendDocument.
This ledger therefore enforces *at-most-once automatic send attempts*: an intent
is committed before network I/O; any unacknowledged attempt stays ambiguous
until an operator reconciles it. It never assumes a timeout means failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from core.globals import DOWNLOAD_DIR

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_PATH = DOWNLOAD_DIR / "factory_delivery_ledger.sqlite3"
_SCHEMA_VERSION = 1


class FactoryDeliveryReconciliationRequired(RuntimeError):
    """The prior send may have succeeded: never issue another automatically."""


class FactoryJobAlreadyRunning(RuntimeError):
    """A second worker cannot work on the same Telegram chat/source job."""


@dataclass(frozen=True)
class FactoryDeliveryReceipt:
    message_id: int
    newly_delivered: bool


def _hash_identity(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def factory_delivery_media_sha256(path: Path) -> str:
    """Bind an attempted send to exact rendered bytes, not only timecodes."""
    return await asyncio.to_thread(_file_sha256, Path(path))


def _request_chat_id(update: Any) -> int:
    message = getattr(update, "effective_message", None) or getattr(update, "message", None)
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id == 0:
        raise RuntimeError("Factory publication cannot verify Telegram destination chat; delivery is blocked")
    return chat_id


class FactoryDeliveryJournal:
    """Atomic per-chat/per-source/per-artifact at-most-once sending.

    SQLite BEGIN IMMEDIATE serializes independent processes. An existing
    'sending' row (including after a crash) cannot be automatically reclaimed:
    the prior remote Telegram outcome is unknown. A confirmed row is reusable
    only with the same semantic artifact fingerprint.
    """

    def __init__(
        self,
        *,
        chat_id: int,
        source_id: str,
        source_url: str,
        path: Path = DEFAULT_JOURNAL_PATH,
    ) -> None:
        self.chat_id = int(chat_id)
        if not self.chat_id or not source_id.strip() or not source_url.strip():
            raise ValueError("Factory delivery scope requires chat, source id and URL")
        host = (urlsplit(source_url.strip()).hostname or "").casefold()
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            host = "youtube.com"
        if not host:
            raise ValueError("Factory publication requires a verifiable source host")
        self.scope = _hash_identity(
            {
                "chat_id": self.chat_id,
                "source_id": source_id.strip(),
                "source_host": host,
                "version": _SCHEMA_VERSION,
            }
        )
        self.path = Path(path)

    @classmethod
    def for_update(cls, update: Any, *, media_id: str, source_url: str) -> FactoryDeliveryJournal:
        return cls(chat_id=_request_chat_id(update), source_id=media_id, source_url=source_url)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_delivery (
                scope TEXT NOT NULL,
                artifact_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('sending','delivered')),
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                attempted_at REAL NOT NULL,
                confirmed_at REAL,
                error_type TEXT,
                PRIMARY KEY (scope, artifact_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_job_lease (
                scope TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                process_id INTEGER NOT NULL,
                acquired_at REAL NOT NULL
            )
            """
        )
        return connection

    def acquire_job_lease(self) -> str:
        """Acquire an exclusive durable job owner before any costly processing.

        A crashed owner is intentionally *not* taken over automatically: its
        delivery stage may be uncertain. Manual operator reconciliation is
        required before a fresh owner can safely start.
        """
        owner = uuid.uuid4().hex
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT process_id,acquired_at FROM factory_job_lease WHERE scope=?",
                    (self.scope,),
                ).fetchone()
                if existing is not None:
                    raise FactoryJobAlreadyRunning(
                        "Shorts Factory source/chat is owned by an existing process or needs "
                        "operator recovery after an interrupted run"
                    )
                connection.execute(
                    "INSERT INTO factory_job_lease(scope,owner,process_id,acquired_at) VALUES (?,?,?,?)",
                    (self.scope, owner, os.getpid(), time.time()),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return owner

    def job_lease_status(self) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT owner,process_id,acquired_at FROM factory_job_lease WHERE scope=?",
                (self.scope,),
            ).fetchone()
            if row is None:
                return None
            return {"owner": row[0], "process_id": row[1], "acquired_at": row[2]}

    def release_job_lease(self, owner: str) -> None:
        if not owner:
            raise ValueError("Missing Factory job lease owner")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM factory_job_lease WHERE scope=? AND owner=?",
                (self.scope, owner),
            )
            if cursor.rowcount != 1:
                raise FactoryJobAlreadyRunning("Factory job lease owner changed or has already been released")

    @staticmethod
    def _artifact_fingerprint(semantic: dict[str, Any]) -> str:
        return _hash_identity(semantic)

    def _reserve(self, artifact_key: str, fingerprint: str) -> FactoryDeliveryReceipt | None:
        if not artifact_key or len(artifact_key) > 100:
            raise ValueError("Invalid Factory artifact key")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT fingerprint,state,message_id,chat_id FROM factory_delivery "
                    "WHERE scope=? AND artifact_key=?",
                    (self.scope, artifact_key),
                ).fetchone()
                if existing is not None:
                    prior_fingerprint, state, prior_message_id, chat_id = existing
                    if chat_id != self.chat_id or prior_fingerprint != fingerprint:
                        raise FactoryDeliveryReconciliationRequired(
                            f"Factory artifact {artifact_key}: prior deliverable fingerprint differs; "
                            "operator reconciliation required"
                        )
                    if state == "delivered" and isinstance(prior_message_id, int) and prior_message_id > 0:
                        connection.execute("COMMIT")
                        return FactoryDeliveryReceipt(message_id=prior_message_id, newly_delivered=False)
                    raise FactoryDeliveryReconciliationRequired(
                        f"Factory artifact {artifact_key}: Telegram send was attempted but no verified receipt exists; "
                        "do not retry automatically"
                    )
                connection.execute(
                    """
                    INSERT INTO factory_delivery
                    (scope,artifact_key,fingerprint,state,chat_id,message_id,attempted_at)
                    VALUES (?,?,?,'sending',?,NULL,?)
                    """,
                    (self.scope, artifact_key, fingerprint, self.chat_id, time.time()),
                )
                connection.execute("COMMIT")
                return None
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def _record_error(self, artifact_key: str, fingerprint: str, name: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE factory_delivery SET error_type=?
                WHERE scope=? AND artifact_key=? AND fingerprint=? AND state='sending'
                """,
                (name[:120], self.scope, artifact_key, fingerprint),
            )

    def _confirm(self, artifact_key: str, fingerprint: str, message_id: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE factory_delivery
                    SET state='delivered', message_id=?, confirmed_at=?, error_type=NULL
                    WHERE scope=? AND artifact_key=? AND fingerprint=? AND state='sending'
                    """,
                    (message_id, time.time(), self.scope, artifact_key, fingerprint),
                )
                if cursor.rowcount != 1:
                    raise FactoryDeliveryReconciliationRequired(
                        f"Factory artifact {artifact_key}: persisted delivery ownership changed during confirmation"
                    )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def status(self, artifact_key: str) -> dict[str, Any] | None:
        """Operator-readable facts only; does not assume a pending row failed."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT fingerprint,state,message_id,attempted_at,confirmed_at,error_type
                FROM factory_delivery WHERE scope=? AND artifact_key=?
                """,
                (self.scope, artifact_key),
            ).fetchone()
            if row is None:
                return None
            return {
                "fingerprint": row[0],
                "state": row[1],
                "message_id": row[2],
                "attempted_at": row[3],
                "confirmed_at": row[4],
                "error_type": row[5],
            }

    def operator_reconcile_delivery(
        self,
        artifact_key: str,
        *,
        expected_fingerprint: str,
        verified_message_id: int | None,
        operator_verified: bool,
    ) -> None:
        """Explicitly resolve an ambiguous Telegram result after human review.

        verified_message_id is the actually observed Telegram message ID.
        None means a human positively verified non-delivery and accepts a
        new send attempt. Neither outcome can be inferred from a timeout.
        """
        if not operator_verified or len(expected_fingerprint) != 64:
            raise ValueError("Operator verification and exact checkpoint fingerprint required")
        if verified_message_id is not None and (not isinstance(verified_message_id, int) or verified_message_id <= 0):
            raise ValueError("Telegram confirmation requires a positive message ID")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT fingerprint,state,chat_id FROM factory_delivery WHERE scope=? AND artifact_key=?",
                    (self.scope, artifact_key),
                ).fetchone()
                if row is None or row != (expected_fingerprint, "sending", self.chat_id):
                    raise FactoryDeliveryReconciliationRequired(
                        "Factory delivery row is missing, confirmed already or changed since inspection"
                    )
                if verified_message_id is not None:
                    updated = connection.execute(
                        """
                        UPDATE factory_delivery SET state='delivered',message_id=?,confirmed_at=?,error_type=NULL
                        WHERE scope=? AND artifact_key=? AND fingerprint=? AND state='sending'
                        """,
                        (verified_message_id, time.time(), self.scope, artifact_key, expected_fingerprint),
                    )
                else:
                    updated = connection.execute(
                        """
                        DELETE FROM factory_delivery
                        WHERE scope=? AND artifact_key=? AND fingerprint=? AND state='sending'
                        """,
                        (self.scope, artifact_key, expected_fingerprint),
                    )
                if updated.rowcount != 1:
                    raise FactoryDeliveryReconciliationRequired("Factory delivery changed during reconciliation")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def operator_release_stale_job_lease(
        self,
        *,
        expected_owner: str,
        expected_process_id: int,
        operator_verified_stopped: bool,
    ) -> None:
        """Reclaim only an exact, externally confirmed terminated job owner."""
        if not operator_verified_stopped or not expected_owner or expected_process_id <= 0:
            raise ValueError("Operator verification and exact prior process identity are required")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                removed = connection.execute(
                    "DELETE FROM factory_job_lease WHERE scope=? AND owner=? AND process_id=?",
                    (self.scope, expected_owner, expected_process_id),
                )
                if removed.rowcount != 1:
                    raise FactoryJobAlreadyRunning("Factory lease changed or is still owned by another process")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    async def send_once(
        self,
        artifact_key: str,
        *,
        semantic: dict[str, Any],
        sender: Callable[[], Awaitable[Any]],
    ) -> FactoryDeliveryReceipt:
        fingerprint = self._artifact_fingerprint(semantic)
        existing = await asyncio.to_thread(self._reserve, artifact_key, fingerprint)
        if existing is not None:
            logger.info("Factory Telegram delivery suppressed as already confirmed: artifact=%s", artifact_key)
            return existing

        # After this point an uncertain result may have been accepted remotely.
        # Even cancellation and API timeout must never release the reservation.
        try:
            result = await sender()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await asyncio.to_thread(self._record_error, artifact_key, fingerprint, type(exc).__name__)
            except (OSError, sqlite3.Error):
                logger.warning("Factory delivery error bookkeeping failed; durable send intent remains")
            raise FactoryDeliveryReconciliationRequired(
                f"Factory artifact {artifact_key}: send outcome is unconfirmed; "
                "operator must verify Telegram before any retry"
            ) from exc

        message_id = getattr(result, "message_id", None)
        chat_id = getattr(result, "chat_id", None)
        if chat_id is None:
            chat_id = getattr(getattr(result, "chat", None), "id", None)
        if (
            not isinstance(message_id, int)
            or isinstance(message_id, bool)
            or message_id <= 0
            or chat_id != self.chat_id
        ):
            raise FactoryDeliveryReconciliationRequired(
                f"Factory artifact {artifact_key}: API returned no valid Telegram message receipt; "
                "automatic retry blocked"
            )
        await asyncio.to_thread(self._confirm, artifact_key, fingerprint, message_id)
        return FactoryDeliveryReceipt(message_id=message_id, newly_delivered=True)


__all__ = [
    "DEFAULT_JOURNAL_PATH",
    "FactoryDeliveryJournal",
    "FactoryDeliveryReceipt",
    "FactoryDeliveryReconciliationRequired",
    "FactoryJobAlreadyRunning",
    "factory_delivery_media_sha256",
]
