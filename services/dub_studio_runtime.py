#!/usr/bin/env python3
"""Source-owned Dub Studio composition, notifier and detached local worker.

The production Application explicitly registers handlers and starts notification
services. No PTB class methods are replaced at runtime.
"""
from __future__ import annotations

from core.media_title_policy import canonical_media_title

import asyncio
import html
import json
import logging
import os
import subprocess
import sys
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
_GENERIC_RECIPE = "generic_short_v1"
_WORKER_RUNTIME = "dub-worker-quality-v4.5"
_PROGRESS_METADATA_KEY = "dub_progress_message_v1"
_ACTIVE_PROJECT_STATES = {"queued", "rendering", "cancelling"}
_PERMANENT_EDIT_ERRORS = (
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
    "message identifier is not specified",
    "message_id_invalid",
)


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _flag("DUB_STUDIO_ENABLED", os.name == "nt")


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def ensure_worker_running() -> bool:
    if not enabled() or not _flag("DUB_STUDIO_AUTOSTART_WORKER", True):
        return False
    from services.dub_studio import DubStore, repo_root, studio_root, worker_is_fresh

    store = DubStore()
    worker = store.latest_worker()
    worker_pid = int((worker or {}).get("pid") or 0)
    worker_details = (worker or {}).get("details") or {}
    fresh_running = worker_is_fresh(worker) and _pid_running(worker_pid)
    if fresh_running and worker_details.get("runtime") == _WORKER_RUNTIME:
        return True
    if fresh_running and str((worker or {}).get("status") or "") == "busy":
        logger.info("Older Dub Studio worker is busy; replacement deferred until job end")
        return True
    if fresh_running and os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(worker_pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            logger.warning("Could not replace old Dub Studio worker: %s", exc)

    root = studio_root()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "worker-supervisor.log"
    command = [
        sys.executable,
        "-m",
        "tools.voxcpm2.dub_worker_hardened",
        "--root",
        str(root),
    ]
    kwargs: dict[str, Any] = {"cwd": str(repo_root()), "env": dict(os.environ)}
    kwargs["env"].setdefault("PYTHONUTF8", "1")
    kwargs["env"].setdefault("PYTHONIOENCODING", "utf-8")
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kwargs["start_new_session"] = True

    try:
        with log_path.open("ab") as log_file:
            subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
        logger.info("🎙 Dub Studio worker v4.5 autostart requested: %s", root)
        return True
    except Exception as exc:
        logger.warning("⚠️ Dub Studio worker autostart failed: %s", exc)
        return False


def _progress_message_ref(
    project: dict[str, Any],
    event: dict[str, Any],
) -> tuple[int, int] | None:
    metadata = project.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    ref = metadata.get(_PROGRESS_METADATA_KEY)
    if not isinstance(ref, dict):
        return None
    try:
        event_job_id = int(event.get("job_id") or 0)
        ref_job_id = int(ref.get("job_id") or 0)
        chat_id = int(ref.get("chat_id") or 0)
        message_id = int(ref.get("message_id") or 0)
    except (TypeError, ValueError):
        return None
    if (
        event_job_id <= 0
        or ref_job_id != event_job_id
        or chat_id <= 0
        or message_id <= 0
    ):
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
        metadata[_PROGRESS_METADATA_KEY] = {
            "job_id": int(job_id),
            "chat_id": int(chat_id),
            "message_id": int(message_id),
        }
        conn.execute(
            "UPDATE dub_projects SET metadata_json=?, updated_at=? WHERE id=?",
            (
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
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
    progress = max(
        0,
        min(int(payload.get("progress") or project.get("progress") or 0), 99),
    )
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
                "Dub progress message %s cannot be edited; creating one replacement",
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


async def _finalize_progress_card(
    application: Any,
    event: dict[str, Any],
    project: dict[str, Any],
) -> None:
    ref = _progress_message_ref(project, event)
    if ref is None:
        return
    chat_id, message_id = ref
    event_type = str(event.get("event_type") or "")
    icon, heading = {
        "job_succeeded": ("✅", "готово"),
        "job_failed": ("❌", "ошибка"),
        "job_cancelled": ("🚫", "отменено"),
    }.get(event_type, ("ℹ️", "завершено"))
    project_id = str(project["id"])
    title = str(event.get("project_title") or project.get("title") or project_id)
    text = (
        f"{icon} <b>Dub Studio — {heading}</b>\n\n"
        f"{html.escape(title)}\n"
        f"<code>{html.escape(project_id)}</code>\n\n"
        f"<code>/dubstatus {html.escape(project_id)}</code>"
    )
    try:
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        if "message is not modified" not in str(exc or "").casefold():
            logger.info("Could not finalize Dub progress card %s: %s", message_id, exc)


async def _notify_generic_success(
    application: Any,
    store: Any,
    event: dict[str, Any],
    project: dict[str, Any],
) -> bool:
    from handlers.dub_delivery import send_project_outputs

    job = store.get_job(int(event["job_id"])) if event.get("job_id") else None
    action = str((job or {}).get("action") or "")
    project_id = str(project["id"])
    chat_id = int(event["owner_chat_id"])

    if action == "prepare_custom":
        text = (
            "✅ <b>Старый этап подготовки завершён</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n\n"
            "Этот legacy-проект использует прежний шаблонный режим. Для новых роликов "
            "откройте <code>/dub</code> и выберите «Мой готовый перевод — SRT»."
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "📎 Продолжить старый проект",
                        callback_data=f"dubwiz|translation|{project_id}",
                    )
                ]]
            ),
        )
        _sent, failures = await send_project_outputs(application.bot, chat_id, project)
        if failures:
            await application.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Не все подготовительные файлы отправились:\n" + "\n".join(failures[:6]),
            )
        return True

    if action in {"render_gemini", "render"}:
        text = (
            "✅ <b>Gemini MAX: русский дубляж готов</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            "Перевод прошёл многоступенчатый контроль; звук — прямой Clean Expressive renderer. "
            "Отправляю основной MP4, перевод и субтитры.\n\n"
            f"Версия только с русским голосом: <code>/dubsend {html.escape(project_id)} all</code>"
        )
    elif action == "render_direct":
        text = (
            "✅ <b>Ролик с вашим готовым SRT готов</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            "Русский текст использован без переписывания Gemini. "
            "Отправляю основной MP4 и сопутствующие файлы.\n\n"
            f"Версия только с русским голосом: <code>/dubsend {html.escape(project_id)} all</code>"
        )
    elif action == "repair_audio":
        text = (
            "✅ <b>Аудиоремонт Dub Studio завершён</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            "Перевод, заголовок и субтитры использованы повторно — Gemini не запускался. "
            "Отправляю обновлённый MP4 и аудиоматериалы.\n\n"
            f"Проверить реплики: <code>/dubsegments {html.escape(project_id)}</code>"
        )
    elif action == "render_custom":
        text = (
            "✅ <b>Legacy-ролик с пользовательским переводом готов</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            "Отправляю основной MP4 и сопутствующие файлы."
        )
    else:
        return False

    await application.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    _sent, failures = await send_project_outputs(application.bot, chat_id, project)
    if failures:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Не все результаты отправились:\n"
                + "\n".join(failures[:6])
                + f"\n\nПовторить: /dubsend {project_id}"
            ),
        )
    return True


def _undelivered_notification_events(store: Any, limit: int = 20) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.*, p.owner_chat_id, p.title AS project_title
            FROM dub_events e
            JOIN dub_projects p ON p.id=e.project_id
            WHERE e.delivered_at=''
              AND (
                e.event_type IN ('job_succeeded','job_failed','job_cancelled')
                OR e.event_type LIKE 'job_progress_%'
              )
            ORDER BY e.id LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            payload = json.loads(str(item.pop("payload_json", "{}") or "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        item["payload"] = payload if isinstance(payload, dict) else {}
        if item.get("project_title"):
            item["project_title"] = canonical_media_title(item["project_title"])
        result.append(item)
    return result


async def _notification_loop(application: Any) -> None:
    from services.dub_studio import DubStore

    store = DubStore()
    while True:
        try:
            for event in _undelivered_notification_events(store, limit=20):
                event_type = str(event["event_type"])
                project_id = str(event["project_id"])
                project = store.get_project(project_id)
                try:
                    if event_type.startswith("job_progress_"):
                        await _notify_progress_milestone(application, store, event, project)
                        continue

                    await _finalize_progress_card(application, event, project)
                    if (
                        event_type == "job_succeeded"
                        and str(project.get("recipe_id")) == _GENERIC_RECIPE
                    ):
                        delivered = await _notify_generic_success(
                            application,
                            store,
                            event,
                            project,
                        )
                        if delivered:
                            store.mark_event_delivered(int(event["id"]))
                            ensure_worker_running()
                            continue

                    icon = {
                        "job_succeeded": "✅",
                        "job_failed": "❌",
                        "job_cancelled": "🚫",
                    }.get(event_type, "ℹ️")
                    title = str(event.get("project_title") or project_id)
                    message = str(event.get("message") or "")
                    extra = ""
                    if (
                        str(project.get("recipe_id")) == _GENERIC_RECIPE
                        and event_type == "job_failed"
                    ):
                        extra = (
                            "\nПроверьте точный этап: <code>/dubstatus "
                            + html.escape(project_id)
                            + "</code>"
                        )
                    text = (
                        f"{icon} <b>Dub Studio</b>\n\n"
                        f"{html.escape(title)}\n"
                        f"<code>{html.escape(project_id)}</code>\n\n"
                        f"{html.escape(message[:1200])}{extra}\n\n"
                        f"<code>/dubstatus {html.escape(project_id)}</code>\n"
                        f"<code>/dubfiles {html.escape(project_id)}</code>\n"
                        f"<code>/dubsend {html.escape(project_id)}</code>"
                    )
                    await application.bot.send_message(
                        chat_id=int(event["owner_chat_id"]),
                        text=text,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.warning(
                        "Dub Studio notification failed for event %s: %s",
                        event["id"],
                        exc,
                    )
                    continue
                store.mark_event_delivered(int(event["id"]))
                ensure_worker_running()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Dub Studio notification loop: %s", exc)
        await asyncio.sleep(5)


def register_dub_studio(application: Any) -> bool:
    """Register Dub Studio handlers on this concrete Application instance."""
    if not enabled():
        return False
    from handlers.dub_audio_repair import register_dub_audio_repair_handlers
    from handlers.dub_commands import register_dub_handlers
    from handlers.dub_delivery import register_dub_delivery_handlers
    from handlers.dub_health import register_dub_health_handler
    from handlers.dub_quickstart import register_dub_quickstart_handler
    from handlers.dub_wizard import register_dub_wizard_handlers
    from handlers.dub_multicommand import register_dub_multicommand_handler

    register_dub_wizard_handlers(application)
    register_dub_health_handler(application)
    register_dub_handlers(application)
    register_dub_audio_repair_handlers(application)
    register_dub_delivery_handlers(application)
    register_dub_quickstart_handler(application)
    register_dub_multicommand_handler(application)
    ensure_worker_running()
    logger.info("🎙 Dub Studio v4.5 handlers registered on Application")
    return True


def start_dub_studio_services(application: Any) -> bool:
    """Start the request-independent notifier after Application.start()."""
    if not enabled():
        return False
    if application.bot_data.get("dub_studio_notification_task"):
        return True
    task = application.create_task(
        _notification_loop(application),
        name="dub-studio-notifications",
    )
    application.bot_data["dub_studio_notification_task"] = task
    logger.info("🎙 Dub Studio notification service started")
    return True


__all__ = [
    "enabled",
    "ensure_worker_running",
    "register_dub_studio",
    "start_dub_studio_services",
]
