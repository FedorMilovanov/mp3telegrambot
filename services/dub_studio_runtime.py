#!/usr/bin/env python3
"""Install Dub Studio handlers, notifier and detached local worker."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import subprocess
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)
_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_BUILD = None
_ORIGINAL_START = None


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
    if worker_is_fresh(worker) and _pid_running(int(worker.get("pid") or 0)):
        return True

    root = studio_root()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "worker-supervisor.log"
    command = [
        sys.executable,
        "-m",
        "tools.voxcpm2.dub_worker",
        "--root",
        str(root),
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(repo_root()),
        "env": dict(os.environ),
    }
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


async def _notification_loop(application: Any) -> None:
    from services.dub_studio import DubStore

    store = DubStore()
    while True:
        try:
            for event in store.undelivered_terminal_events(limit=20):
                event_type = str(event["event_type"])
                icon = {
                    "job_succeeded": "✅",
                    "job_failed": "❌",
                    "job_cancelled": "🚫",
                }.get(event_type, "ℹ️")
                project_id = str(event["project_id"])
                title = str(event.get("project_title") or project_id)
                message = str(event.get("message") or "")
                text = (
                    f"{icon} <b>Dub Studio</b>\n\n"
                    f"{html.escape(title)}\n"
                    f"<code>{html.escape(project_id)}</code>\n\n"
                    f"{html.escape(message[:1200])}\n\n"
                    f"<code>/dubstatus {html.escape(project_id)}</code>\n"
                    f"<code>/dubfiles {html.escape(project_id)}</code>\n"
                    f"<code>/dubsend {html.escape(project_id)}</code>"
                )
                try:
                    await application.bot.send_message(
                        chat_id=int(event["owner_chat_id"]),
                        text=text,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.warning("Dub Studio notification failed for event %s: %s", event["id"], exc)
                    continue
                store.mark_event_delivered(int(event["id"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Dub Studio notification loop: %s", exc)
        await asyncio.sleep(5)


def install_dub_studio_runtime() -> None:
    """Patch PTB construction before main.run_bot_async builds the Application."""
    global _INSTALLED, _ORIGINAL_BUILD, _ORIGINAL_START
    if _INSTALLED or not enabled():
        return
    with _LOCK:
        if _INSTALLED:
            return
        from telegram.ext import Application, ApplicationBuilder
        from handlers.dub_commands import register_dub_handlers
        from handlers.dub_delivery import register_dub_delivery_handlers

        _ORIGINAL_BUILD = ApplicationBuilder.build
        _ORIGINAL_START = Application.start

        def build_with_dub(self: Any) -> Any:
            application = _ORIGINAL_BUILD(self)
            register_dub_handlers(application)
            register_dub_delivery_handlers(application)
            return application

        async def start_with_dub(self: Any) -> None:
            await _ORIGINAL_START(self)
            if not self.bot_data.get("dub_studio_notification_task"):
                task = self.create_task(
                    _notification_loop(self),
                    name="dub-studio-notifications",
                )
                self.bot_data["dub_studio_notification_task"] = task

        ApplicationBuilder.build = build_with_dub
        Application.start = start_with_dub
        ensure_worker_running()
        _INSTALLED = True
        logger.info("🎙 Dub Studio runtime: handlers + delivery + worker + notifications enabled")


__all__ = ["enabled", "ensure_worker_running", "install_dub_studio_runtime"]
