#!/usr/bin/env python3
"""Clear loop-bound LiveDub state before an internal polling restart.

``main.run_bot`` deliberately creates a brand-new asyncio event loop after an
unexpected failure.  The legacy startup already rebuilds per-video and rate-limit
locks, but newer LiveDub layers also retain loop-bound tasks/Futures at module
scope.  If the previous loop dies mid-delivery, those objects can otherwise make
a later request wait forever, suppress a fallback MP3, or leak its temporary copy.

Confirmed-success TTL entries are intentionally preserved: only unfinished work
from the dead loop is discarded.
"""
from __future__ import annotations

import functools
import logging
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


def _reset_audio_coalescing() -> int:
    """Release unfinished dual-MP3 followers without erasing confirmed success."""
    try:
        from services import livedub_quality_runtime as quality
    except Exception:
        return 0

    with quality._AUDIO_LOCK:
        pending = list(quality._AUDIO_INFLIGHT.values())
        quality._AUDIO_INFLIGHT.clear()

    released = 0
    for future in pending:
        try:
            if not future.done():
                future.set_result(False)
            released += 1
        except Exception:
            continue
    return released


def _cancel_timeout_task(task: Any) -> None:
    if task is None:
        return
    try:
        if not task.done():
            task.cancel()
    except Exception:
        # A Task owned by a loop that has already been closed may reject calls.
        pass


def _unlink_deferred_copy(entry: dict[str, Any]) -> None:
    value = entry.get("audio_path")
    if not value:
        return
    try:
        Path(value).unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def _reset_source_audio_dedupe() -> tuple[int, int]:
    """Drop deferred source MP3 work that belonged to the previous Bot instance."""
    try:
        from services import livedub_audio_dedupe as dedupe
    except Exception:
        return 0, 0

    with dedupe._STATE_LOCK:
        entries = list(dedupe._PENDING.values())
        companion_marks = len(dedupe._COMPANION_OK)
        dedupe._PENDING.clear()
        dedupe._COMPANION_OK.clear()

    for entry in entries:
        _cancel_timeout_task(entry.get("timeout_task"))
        _unlink_deferred_copy(entry)
    return len(entries), companion_marks


def reset_cross_loop_state() -> dict[str, int]:
    """Reset every known module-level object tied to the previous asyncio loop."""
    audio_inflight = _reset_audio_coalescing()
    deferred_source, companion_marks = _reset_source_audio_dedupe()
    return {
        "audio_inflight": audio_inflight,
        "deferred_source": deferred_source,
        "companion_marks": companion_marks,
    }


def install_restart_state_runtime(main_module: ModuleType) -> None:
    """Wrap ``run_bot_async`` so every newly created loop begins cleanly."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return

        current = getattr(main_module, "run_bot_async")
        if not getattr(current, "_mp3bot_restart_state", False):

            @functools.wraps(current)
            async def clean_start(*args: Any, **kwargs: Any):
                cleared = reset_cross_loop_state()
                total = sum(cleared.values())
                if total:
                    logger.warning(
                        "🧹 Restart state: cleared audio_inflight=%d, "
                        "deferred_source=%d, companion_marks=%d from previous loop",
                        cleared["audio_inflight"],
                        cleared["deferred_source"],
                        cleared["companion_marks"],
                    )
                return await current(*args, **kwargs)

            clean_start._mp3bot_restart_state = True  # type: ignore[attr-defined]
            main_module.run_bot_async = clean_start

        _INSTALLED = True
        logger.info("🔄 Restart state runtime: loop-bound LiveDub state guarded")
