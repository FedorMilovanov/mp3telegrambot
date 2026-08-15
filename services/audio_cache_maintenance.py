#!/usr/bin/env python3
"""Source-owned maintenance for cached MP3 artifacts."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


def _active_video_ids() -> set[str]:
    try:
        import core.globals as globals_module

        with globals_module._video_locks_mutex:
            return {
                str(video_id)
                for video_id, lock in globals_module._video_processing_locks.items()
                if lock.locked()
            }
    except Exception:
        return set()


def _belongs_to_active_video(path: Path, active_ids: Iterable[str]) -> bool:
    name = path.name
    return any(
        name == f"{video_id}.mp3" or name.startswith(f"{video_id}_")
        for video_id in active_ids
    )


def cleanup_stale_cached_audio(max_age_days: int | None = None) -> int:
    """Delete expired MP3 cache files without touching active processing."""
    try:
        import core.database as database
        import core.globals as globals_module

        if max_age_days is None:
            raw = os.getenv("AUDIO_CACHE_TTL_DAYS", "").strip()
            max_age_days = int(raw) if raw else int(database.CACHE_TTL_DAYS)
        max_age_days = max(1, min(int(max_age_days), 3650))
        root = Path(globals_module.DOWNLOAD_DIR)
    except (TypeError, ValueError, OSError):
        return 0

    if not root.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    active_ids = _active_video_ids()
    deleted = 0
    for path in root.glob("*.mp3"):
        try:
            if not path.is_file() or ".part-" in path.name:
                continue
            if _belongs_to_active_video(path, active_ids):
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            deleted += 1
        except OSError:
            continue
    if deleted:
        logger.info(
            "🧹 Audio cache: удалено %d MP3 старше %d дней",
            deleted,
            max_age_days,
        )
    return deleted


__all__ = ["cleanup_stale_cached_audio"]
