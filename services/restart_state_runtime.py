#!/usr/bin/env python3
"""Clear loop-bound state and bind final main-module runtime adapters.

``main.run_bot`` deliberately creates a brand-new asyncio event loop after an
unexpected failure. The legacy startup already rebuilds per-video and rate-limit
locks, but newer LiveDub layers also retain loop-bound tasks/Futures at module
scope. If the previous loop dies mid-delivery, those objects can otherwise make a
later request wait forever, suppress a fallback MP3, or leak its temporary copy.

A complete process crash loses the in-memory registry entirely, so stale files in
``mp3bot_livedub_deferred`` are also swept at startup. Confirmed-success TTL entries
are intentionally preserved: only unfinished work is discarded.

This required post-main binder also installs privacy-safe operator status before
``run_bot_async`` registers Telegram handlers. Keeping that binding here avoids a
second manifest mutation while preserving one explicit, fail-closed startup step.
"""
from __future__ import annotations

import functools
import logging
import tempfile
import threading
import time
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


def cleanup_orphaned_deferred_files(
    max_age_hours: int = 6,
    *,
    root: Path | None = None,
    now: float | None = None,
) -> int:
    """Delete deferred source-MP3 copies left by a previous dead process.

    The normal timeout is up to 35 minutes. A six-hour default therefore avoids
    touching legitimate work while bounding disk growth after kill/BSOD/reboot.
    ``root`` and ``now`` are injectable to keep the behavior deterministic in tests.
    """
    max_age_hours = max(1, min(int(max_age_hours), 24 * 30))
    directory = root or Path(tempfile.gettempdir()) / "mp3bot_livedub_deferred"
    if not directory.exists():
        return 0
    cutoff = (time.time() if now is None else float(now)) - max_age_hours * 3600
    deleted = 0
    try:
        for path in directory.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError:
                continue
        try:
            directory.rmdir()
        except OSError:
            pass
    except OSError:
        return deleted
    return deleted


def reset_cross_loop_state() -> dict[str, int]:
    """Reset every known loop-bound object and sweep process-crash leftovers."""
    audio_inflight = _reset_audio_coalescing()
    deferred_source, companion_marks = _reset_source_audio_dedupe()
    orphan_files = cleanup_orphaned_deferred_files()
    return {
        "audio_inflight": audio_inflight,
        "deferred_source": deferred_source,
        "companion_marks": companion_marks,
        "orphan_files": orphan_files,
    }


def install_restart_state_runtime(main_module: ModuleType) -> None:
    """Wrap ``run_bot_async`` and bind operator status exactly once."""
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
                        "🧹 Restart state: audio_inflight=%d, deferred_source=%d, "
                        "companion_marks=%d, orphan_files=%d cleared",
                        cleared["audio_inflight"],
                        cleared["deferred_source"],
                        cleared["companion_marks"],
                        cleared["orphan_files"],
                    )
                return await current(*args, **kwargs)

            clean_start._mp3bot_restart_state = True  # type: ignore[attr-defined]
            main_module.run_bot_async = clean_start

        from services.operator_runtime_status import install_operator_runtime_status

        install_operator_runtime_status(main_module)
        _INSTALLED = True
        logger.info(
            "🔄 Restart state runtime: loop/crash leftovers and operator status guarded"
        )
