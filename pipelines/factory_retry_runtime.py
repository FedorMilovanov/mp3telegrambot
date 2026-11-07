"""App-owned delayed recovery from genuine Gemini 503 for Shorts Factory only.

No separate daemon, fabricated API acknowledgements, or unbounded sleeps:
the scheduler runs under the actual Telegram Application lifecycle and forwards
messages via the real Bot API, preserving the delivery ledger's real receipts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from types import SimpleNamespace
from typing import Any

from services.shorts_factory_retry_store import FactoryDeferredJob, FactoryDeferredRetryStore

logger = logging.getLogger(__name__)

_RETRY_TASK_KEY = "factory_deferred_retry_task"
_RETRY_POLL_SECONDS = 20.0


class _FactoryScheduledMessage:
    """Minimal send adapter backed by the actual Bot, not a fabricated API reply."""

    def __init__(self, bot: Any, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = int(chat_id)
        self.chat = SimpleNamespace(id=chat_id)

    async def reply_text(self, text: str, **kwargs: Any) -> Any:
        return await self.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)

    async def reply_video(self, **kwargs: Any) -> Any:
        return await self.bot.send_video(chat_id=self.chat_id, **kwargs)

    async def reply_document(self, **kwargs: Any) -> Any:
        return await self.bot.send_document(chat_id=self.chat_id, **kwargs)


def _retry_update(bot: Any, job: FactoryDeferredJob) -> Any:
    message = _FactoryScheduledMessage(bot, job.chat_id)
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=job.user_id),
    )


async def _run_due_job(application: Any, store: FactoryDeferredRetryStore, job: FactoryDeferredJob) -> None:
    from core.user_modes import get_user_mode
    from pipelines.shorts_factory import process_shorts_factory

    if application.bot_data.get("stop_requested", False):
        return
    try:
        current_mode = await get_user_mode(job.user_id)
        if current_mode != "shorts_max":
            await asyncio.to_thread(store.mark_blocked, job.scope)
            logger.warning("Factory deferred job paused: user has switched mode since original request")
            return

        update = _retry_update(application.bot, job)
        logger.info(
            "Factory deferred HIGH retry begin: window=%d/3 stage=%s",
            job.windows_used + 1,
            job.stage,
        )
        ok = await process_shorts_factory(
            job.source_url,
            update,
            status_msg=None,
            silent_errors=False,
        )
        if ok:
            await asyncio.to_thread(store.mark_completed, job.scope)
            logger.info("Factory deferred recovery completed with confirmed delivery")
        else:
            row = await asyncio.to_thread(store.status, job.scope)
            if row is not None and row["status"] == "running":
                await asyncio.to_thread(store.mark_blocked, job.scope)
                logger.warning("Factory deferred job blocked after non-overload failure")
    except asyncio.CancelledError:
        # Leave an in-flight owner for explicit recovery after a crash/stop.
        raise
    except Exception:
        logger.exception("Factory deferred job failed outside recognized 503 state")
        await asyncio.to_thread(store.mark_blocked, job.scope)


async def _retry_loop(application: Any, store: FactoryDeferredRetryStore) -> None:
    while not application.bot_data.get("stop_requested", False):
        try:
            job = await asyncio.to_thread(store.claim_due)
            if job is None:
                await asyncio.sleep(_RETRY_POLL_SECONDS)
                continue
            await _run_due_job(application, store, job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Factory durable retry loop encountered a nonfatal error")
            await asyncio.sleep(60)


def factory_deferred_retries_enabled() -> bool:
    enabled = (os.getenv("SHORTS_FACTORY_DEFERRED_RETRY_ENABLED", "1") or "1").strip().lower()
    return enabled not in {"0", "no", "off", "false"}


def start_factory_retry_services(application: Any) -> bool:
    """Start exactly one scheduler under PTB Application.create_task ownership."""
    if not factory_deferred_retries_enabled():
        return False
    existing = application.bot_data.get(_RETRY_TASK_KEY)
    if existing is not None and not existing.done():
        return True
    store = FactoryDeferredRetryStore()
    task = application.create_task(_retry_loop(application, store), name="factory-deferred-503-retry")
    application.bot_data[_RETRY_TASK_KEY] = task
    logger.info("Factory durable provider-503 retry scheduler started (max three real-request windows)")
    return True


async def stop_factory_retry_services(application: Any) -> None:
    """Stop the scheduler before PTB Application.stop awaits its tracked tasks."""
    task: asyncio.Task[Any] | None = application.bot_data.pop(_RETRY_TASK_KEY, None)
    if task is None:
        return
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    elif not task.cancelled() and task.exception() is not None:
        logger.warning("Factory retry scheduler terminated with an exception")


__all__ = ["factory_deferred_retries_enabled", "start_factory_retry_services", "stop_factory_retry_services"]
