#!/usr/bin/env python3
"""Install Dub Studio handlers, notifier and detached local worker."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import subprocess
import sys
import threading
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_BUILD = None
_ORIGINAL_START = None
_GENERIC_RECIPE = "generic_short_v1"
_WORKER_RUNTIME = "dub-worker-quality-v4.3"


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
        logger.info("Legacy Dub Studio worker is busy; upgrade deferred until the job finishes")
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
            logger.warning("Could not replace legacy Dub Studio worker: %s", exc)

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
        logger.info("🎙 Dub Studio worker autostart requested: %s", root)
        return True
    except Exception as exc:
        logger.warning("⚠️ Dub Studio worker autostart failed: %s", exc)
        return False


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
                [[InlineKeyboardButton("📎 Продолжить старый проект", callback_data=f"dubwiz|translation|{project_id}")]]
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
            "Перевод прошёл полный многоступенчатый контроль. "
            "Отправляю основной MP4, перевод и субтитры.\n\n"
            f"Версия только с русским голосом: <code>/dubsend {html.escape(project_id)} all</code>"
        )
    elif action == "render_direct":
        text = (
            "✅ <b>Ролик с вашим готовым SRT готов</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            "Русский текст использован без проверки и переписывания Gemini. "
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
            text="⚠️ Не все результаты отправились:\n" + "\n".join(failures[:6]) + f"\n\nПовторить: /dubsend {project_id}",
        )
    return True


def _undelivered_notification_events(store: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Read terminal events plus sparse durable progress milestones."""
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
        result.append(item)
    return result


async def _notify_progress_milestone(
    application: Any,
    store: Any,
    event: dict[str, Any],
    project: dict[str, Any],
) -> None:
    # If the bot was offline until after completion, do not replay stale progress
    # immediately before the terminal success/failure notification.
    if str(project.get("status") or "") not in {"queued", "rendering", "cancelling"}:
        store.mark_event_delivered(int(event["id"]))
        return
    payload = event.get("payload") or {}
    progress = max(0, min(int(payload.get("progress") or project.get("progress") or 0), 99))
    stage = str(payload.get("stage") or project.get("stage") or "CPU-рендер")
    project_id = str(project["id"])
    title = str(event.get("project_title") or project_id)
    text = (
        f"⚙️ <b>Dub Studio — {progress}%</b>\n\n"
        f"{html.escape(title)}\n"
        f"<code>{html.escape(project_id)}</code>\n\n"
        f"Этап: <code>{html.escape(stage[:180])}</code>\n"
        "CPU-рендер продолжается; это контрольный рубеж, а не оценка по времени.\n\n"
        f"<code>/dubstatus {html.escape(project_id)}</code>"
    )
    await application.bot.send_message(
        chat_id=int(event["owner_chat_id"]),
        text=text,
        parse_mode="HTML",
    )
    store.mark_event_delivered(int(event["id"]))


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
                    if event_type == "job_succeeded" and str(project.get("recipe_id")) == _GENERIC_RECIPE:
                        delivered = await _notify_generic_success(application, store, event, project)
                        if delivered:
                            store.mark_event_delivered(int(event["id"]))
                            ensure_worker_running()
                            continue
                    icon = {"job_succeeded": "✅", "job_failed": "❌", "job_cancelled": "🚫"}.get(event_type, "ℹ️")
                    title = str(event.get("project_title") or project_id)
                    message = str(event.get("message") or "")
                    extra = ""
                    if str(project.get("recipe_id")) == _GENERIC_RECIPE and event_type == "job_failed":
                        extra = "\nПроверьте точный этап: <code>/dubstatus " + html.escape(project_id) + "</code>"
                    text = (
                        f"{icon} <b>Dub Studio</b>\n\n"
                        f"{html.escape(title)}\n"
                        f"<code>{html.escape(project_id)}</code>\n\n"
                        f"{html.escape(message[:1200])}{extra}\n\n"
                        f"<code>/dubstatus {html.escape(project_id)}</code>\n"
                        f"<code>/dubfiles {html.escape(project_id)}</code>\n"
                        f"<code>/dubsend {html.escape(project_id)}</code>"
                    )
                    await application.bot.send_message(chat_id=int(event["owner_chat_id"]), text=text, parse_mode="HTML")
                except Exception as exc:
                    logger.warning("Dub Studio notification failed for event %s: %s", event["id"], exc)
                    continue
                store.mark_event_delivered(int(event["id"]))
                ensure_worker_running()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Dub Studio notification loop: %s", exc)
        await asyncio.sleep(5)


def install_dub_studio_runtime() -> None:
    global _INSTALLED, _ORIGINAL_BUILD, _ORIGINAL_START
    if _INSTALLED or not enabled():
        return
    with _LOCK:
        if _INSTALLED:
            return
        from telegram.ext import Application, ApplicationBuilder
        from handlers.dub_audio_repair import register_dub_audio_repair_handlers
        from handlers.dub_commands import register_dub_handlers
        from handlers.dub_delivery import register_dub_delivery_handlers
        from handlers.dub_health import register_dub_health_handler
        from handlers.dub_quickstart import register_dub_quickstart_handler
        from handlers.dub_wizard import register_dub_wizard_handlers

        _ORIGINAL_BUILD = ApplicationBuilder.build
        _ORIGINAL_START = Application.start

        def build_with_dub(self: Any) -> Any:
            application = _ORIGINAL_BUILD(self)
            register_dub_wizard_handlers(application)
            register_dub_health_handler(application)
            register_dub_handlers(application)
            register_dub_audio_repair_handlers(application)
            register_dub_delivery_handlers(application)
            register_dub_quickstart_handler(application)
            return application

        async def start_with_dub(self: Any) -> None:
            await _ORIGINAL_START(self)
            if not self.bot_data.get("dub_studio_notification_task"):
                task = self.create_task(_notification_loop(self), name="dub-studio-notifications")
                self.bot_data["dub_studio_notification_task"] = task

        ApplicationBuilder.build = build_with_dub
        Application.start = start_with_dub
        ensure_worker_running()
        _INSTALLED = True
        logger.info("🎙 Dub Studio runtime: Gemini MAX + direct SRT + audio repair + delivery + worker enabled")


__all__ = ["enabled", "ensure_worker_running", "install_dub_studio_runtime"]
