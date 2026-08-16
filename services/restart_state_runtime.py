#!/usr/bin/env python3
"""Cross-loop cleanup and restart diagnostics for the production bot.

The process lifecycle calls :func:`reset_cross_loop_state` directly before
``asyncio.run``.  No bot runner is replaced at runtime.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path


def _reset_audio_coalescing() -> int:
    from services.livedub_delivery_coordinator import reset_delivery_runtime_state

    return reset_delivery_runtime_state()


def cleanup_orphaned_deferred_files(
    max_age_hours: int = 6,
    *,
    root: Path | None = None,
    now: float | None = None,
) -> int:
    """Delete request-scoped source-MP3 copies left by a dead process."""
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
    """Release unfinished coordinator work and sweep crash-leftover temp files."""
    return {
        "audio_inflight": _reset_audio_coalescing(),
        # SourceAudioDeferral is request-owned now; no global registry remains.
        "deferred_source": 0,
        "companion_marks": 0,
        "orphan_files": cleanup_orphaned_deferred_files(),
    }


__all__ = [
    "cleanup_orphaned_deferred_files",
    "reset_cross_loop_state",
]
