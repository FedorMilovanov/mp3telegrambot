#!/usr/bin/env python3
"""Source-owned video-mode dispatcher.

Handlers and playlist processing import this boundary directly. No runtime
installer rebinding is required to route Factory or translation-editorial jobs.
"""
from __future__ import annotations

import asyncio
from typing import Any

from services.latency_trace import begin_latency_trace, finish_latency_trace


def _effective_user_id(update: Any) -> int:
    user = getattr(update, "effective_user", None)
    return int(getattr(user, "id", 0) or 0)


async def _reply_factory_requirement(update: Any, *, silent_errors: bool) -> None:
    if silent_errors:
        return
    message = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if message is None:
        return
    try:
        await message.reply_text(
            "❌ SHORTS FACTORY MAX требует faster-whisper large-v3."
        )
    except Exception:
        pass


async def process_single_video(
    url,
    update,
    status_msg=None,
    progress_prefix="",
    context=None,
    silent_errors: bool = False,
):
    """Dispatch exactly one video according to the persisted user mode."""
    from handlers.mode_command import EDITORIAL_MODE, get_user_mode

    user_id = _effective_user_id(update)
    mode = await get_user_mode(user_id) if user_id else "rus"
    trace_token = begin_latency_trace(mode)
    outcome = "error"

    try:
        if mode == "shorts_max":
            from services.shorts_video import HAS_FASTER_WHISPER

            if not HAS_FASTER_WHISPER:
                await _reply_factory_requirement(update, silent_errors=silent_errors)
                outcome = "rejected:no_whisper"
                return False
            from pipelines.shorts_factory import process_shorts_factory

            result = await process_shorts_factory(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )
        elif mode == EDITORIAL_MODE:
            from services.translation_editorial_runner import (
                process_translation_editorial_only,
            )

            result = await process_translation_editorial_only(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )
        else:
            from pipelines.main_pipeline import process_single_video as process_main_video

            result = await process_main_video(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )

        outcome = "ok" if result is not False else "failed"
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:
        outcome = f"error:{type(exc).__name__}"
        raise
    finally:
        finish_latency_trace(trace_token, outcome=outcome)


__all__ = ["process_single_video"]
