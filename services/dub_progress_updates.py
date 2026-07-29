#!/usr/bin/env python3
"""One durable Telegram progress message per Dub Studio job.

Progress milestones are durable database events, but they must not become a chain
of 25/50/75 percent messages.  The first milestone creates a status card and
stores its Telegram message id in the project's metadata.  Later milestones for
the same job edit that card.  A replacement message is created only when the old
one was permanently deleted or can no longer be edited.
"""
from __future__ import annotations

import html
import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
_METADATA_KEY = "dub_progress_message_v1"
_ACTIVE_PROJECT_STATES = {"queued", "rendering", "cancelling"}
_PERMANENT_EDIT_ERRORS = (
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
    "message identifier is not specified",
    "message_id_invalid",
)


def _progress_message_ref(project: dict[str, Any], event: dict[str, Any]) -> tuple[int, int] | None:
    metadata = project.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    ref = metadata.get(_METADATA_KEY)
    if not isinstance(ref, dict):
        return None
    try:
        event_job_id = int(event.get("job_id") or 0)
        ref_job_id = int(ref.get("job_id") or 0)
        chat_id = int(ref.get("chat_id") or 0)
        message_id = int(ref.get("message_id") or 0)
    except (TypeError, ValueError):
        return None
    if event_job_id <= 0 or ref_job_id != event_job_id or chat_id <= 0 or message_id <= 0:
        return None
    return chat_id, message_id


def _store_progress_message_ref(
    store: Any,
    *,
    project_id: str,
    job_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    from services.dub_studio import utc_now

    with store.connect() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM dub_projects WHERE id=?",
            (str(project_id),),
        ).fetchone()
        if row is None:
            return
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata[_METADATA_KEY] = {
            "job_id": int(job_id),
            "chat_id": int(chat_id),
            "message_id": int(message_id),
        }
        conn.execute(
            "UPDATE dub_projects SET metadata_json=?, updated_at=? WHERE id=?",
            (
                json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                utc_now(),
                str(project_id),
            ),
        )
        conn.commit()


def _permanent_edit_failure(exc: BaseException) -> bool:
    message = str(exc or "").casefold()
    return any(marker in message for marker in _PERMANENT_EDIT_ERRORS)


def _progress_text(event: dict[str, Any], project: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    progress = max(0, min(int(payload.get("progress") or project.get("progress") or 0), 99))
    stage = str(payload.get("stage") or project.get("stage") or "CPU-рендер")
    project_id = str(project["id"])
    title = str(event.get("project_title") or project.get("title") or project_id)
    return (
        f"⚙️ <b>Dub Studio — {progress}%</b>\n\n"
        f"{html.escape(title)}\n"
        f"<code>{html.escape(project_id)}</code>\n\n"
        f"Этап: <code>{html.escape(stage[:180])}</code>\n"
        "CPU-рендер продолжается; проценты обновляются в этом сообщении.\n\n"
        f"<code>/dubstatus {html.escape(project_id)}</code>"
    )


async def _notify_progress_milestone(
    application: Any,
    store: Any,
    event: dict[str, Any],
    project: dict[str, Any],
) -> None:
    if str(project.get("status") or "") not in _ACTIVE_PROJECT_STATES:
        store.mark_event_delivered(int(event["id"]))
        return

    text = _progress_text(event, project)
    owner_chat_id = int(event["owner_chat_id"])
    job_id = int(event.get("job_id") or 0)
    project_id = str(project["id"])
    existing = _progress_message_ref(project, event)

    if existing is not None:
        chat_id, message_id = existing
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
            )
            store.mark_event_delivered(int(event["id"]))
            return
        except Exception as exc:
            low = str(exc or "").casefold()
            if "message is not modified" in low:
                store.mark_event_delivered(int(event["id"]))
                return
            if not _permanent_edit_failure(exc):
                raise
            logger.info(
                "Dub progress message %s can no longer be edited; creating one replacement",
                message_id,
            )

    sent = await application.bot.send_message(
        chat_id=owner_chat_id,
        text=text,
        parse_mode="HTML",
    )
    _store_progress_message_ref(
        store,
        project_id=project_id,
        job_id=job_id,
        chat_id=owner_chat_id,
        message_id=int(sent.message_id),
    )
    store.mark_event_delivered(int(event["id"]))


def install_dub_progress_updates() -> None:
    """Replace milestone spam with one editable, restart-safe progress card."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        import services.dub_studio_runtime as runtime

        runtime._notify_progress_milestone = _notify_progress_milestone
        _INSTALLED = True
        logger.info("⚙️ Dub Studio progress: one editable message per job enabled")


__all__ = [
    "install_dub_progress_updates",
    "_permanent_edit_failure",
    "_progress_message_ref",
]
