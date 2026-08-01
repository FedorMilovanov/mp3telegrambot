"""Single-event-loop process lifecycle for the production bot entrypoint."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

BOT_LIFECYCLE_POLICY = "single-event-loop-external-supervisor-v1"


def _health_port() -> int:
    raw = os.environ.get("PORT", "10000").strip() or "10000"
    try:
        port = int(raw)
    except ValueError:
        logger.warning("PORT задан некорректно — использую 10000")
        port = 10_000
    if not 1 <= port <= 65_535:
        raise ValueError(f"PORT вне диапазона: {port}")
    return port


def _serve_health(main_module: ModuleType) -> None:
    app = getattr(main_module, "flask_app", None)
    if app is None:
        raise RuntimeError("main.flask_app is required for HTTP health checks.")
    port = _health_port()
    try:
        from waitress import serve
    except ImportError as exc:
        environment = (
            os.getenv("ENV") or os.getenv("APP_ENV") or ""
        ).strip().casefold()
        if environment in {"prod", "production"}:
            raise RuntimeError(
                "waitress is required in production; install requirements.txt"
            ) from exc
        logger.warning(
            "waitress не установлен — health-check использует Flask dev server"
        )
        app.run(host="0.0.0.0", port=port, use_reloader=False)
        return
    logger.info("HTTP health-check: waitress на порту %d", port)
    serve(app, host="0.0.0.0", port=port, threads=4)


def _start_health_thread(main_module: ModuleType) -> threading.Thread | None:
    disabled = os.getenv("DISABLE_HEALTH_CHECK", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if disabled:
        logger.info("Локальный режим: HTTP health-check отключён")
        return None

    def target() -> None:
        try:
            _serve_health(main_module)
        except BaseException as exc:
            logger.critical(
                "HTTP health-check завершился: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    thread = threading.Thread(
        target=target,
        name="mp3bot-health-server",
        daemon=True,
    )
    thread.start()
    return thread


def run_bot_process(main_module: ModuleType) -> int:
    """Run exactly one bot loop and let the process supervisor restart failures."""
    runner = getattr(main_module, "run_bot_async", None)
    if not callable(runner):
        raise RuntimeError("main.run_bot_async is required.")
    _start_health_thread(main_module)
    result: Any = asyncio.run(runner())
    if result in {None, "stop_requested"}:
        logger.info("Bot lifecycle completed: %s", result or "normal")
        return 0
    if result == "singleton_conflict":
        logger.error("Bot lifecycle rejected duplicate process.")
        return 2
    raise RuntimeError(f"Unexpected run_bot_async result: {result!r}")


__all__ = [
    "BOT_LIFECYCLE_POLICY",
    "run_bot_process",
]
