#!/usr/bin/env python3
"""Make the dual-MP3 Telegram file-id cache recoverable.

``livedub_audio_companion`` stores up to 500 video→audio pairs in one JSON file.
The legacy loader returned an empty mapping for any read/JSON error. A subsequent
successful delivery could then overwrite the damaged file with one new entry,
permanently discarding every previously cached pair.

This adapter keeps one validated previous generation in ``.bak``. Reads recover a
corrupt/oversized primary from that backup; writes back up only a valid primary,
run the established pruning/atomic-save implementation, verify the result and
restore the backup if the new generation is invalid. Cache failures remain
non-fatal to user delivery.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False
_DEFAULT_MAX_BYTES = 16 * 1024 * 1024


def _max_bytes() -> int:
    raw = os.getenv("LIVEDUB_AUDIO_CACHE_MAX_BYTES", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_MAX_BYTES
    except ValueError:
        value = _DEFAULT_MAX_BYTES
    return max(64 * 1024, min(value, 64 * 1024 * 1024))


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _read_mapping(path: Path, *, max_bytes: int | None = None) -> dict[str, Any] | None:
    """Read one bounded UTF-8 JSON object, distinguishing invalid from empty."""
    limit = _max_bytes() if max_bytes is None else int(max_bytes)
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > limit:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _atomic_copy(source: Path, target: Path) -> bool:
    """Copy one validated cache generation using fsync + atomic replace."""
    temp = target.with_name(
        f"{target.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, temp.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, target)
        return True
    except OSError as exc:
        logger.warning("[LiveDubAudioCache] atomic copy failed %s -> %s: %s", source, target, exc)
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _recover_primary(path: Path, backup: Path) -> dict[str, Any] | None:
    data = _read_mapping(backup)
    if data is None:
        return None
    restored = _atomic_copy(backup, path)
    logger.warning(
        "[LiveDubAudioCache] primary cache recovered from backup: %s entries=%d restored=%s",
        path,
        len(data),
        restored,
    )
    return data


def _install_cache_recovery() -> None:
    import services.livedub_audio_companion as companion

    current_load: Callable[[], dict[str, Any]] = companion._load_cache
    current_save: Callable[[dict[str, Any]], None] = companion._save_cache
    if getattr(current_load, "_mp3bot_cache_recovery", False):
        return

    def resilient_load() -> dict[str, Any]:
        path = companion._cache_path()
        primary = _read_mapping(path)
        if primary is not None:
            return primary
        recovered = _recover_primary(path, _backup_path(path))
        if recovered is not None:
            return recovered
        # Preserve custom/test loaders, but never trust a non-dict result.
        try:
            fallback = current_load()
            return fallback if isinstance(fallback, dict) else {}
        except Exception as exc:
            logger.warning("[LiveDubAudioCache] legacy loader failed: %s", str(exc)[:180])
            return {}

    def resilient_save(data: dict[str, Any]) -> None:
        path = companion._cache_path()
        backup = _backup_path(path)
        previous = _read_mapping(path)
        if previous is not None:
            _atomic_copy(path, backup)

        try:
            current_save(data)
        except Exception as exc:
            logger.warning("[LiveDubAudioCache] base save raised: %s", str(exc)[:180])

        if _read_mapping(path) is not None:
            return

        recovered = _recover_primary(path, backup)
        if recovered is None:
            logger.error(
                "[LiveDubAudioCache] new cache generation invalid and no valid backup exists: %s",
                path,
            )
        else:
            logger.error(
                "[LiveDubAudioCache] invalid new generation rolled back to %d cached videos",
                len(recovered),
            )

    resilient_load._mp3bot_cache_recovery = True  # type: ignore[attr-defined]
    resilient_save._mp3bot_cache_recovery = True  # type: ignore[attr-defined]
    resilient_load.__wrapped__ = current_load  # type: ignore[attr-defined]
    resilient_save.__wrapped__ = current_save  # type: ignore[attr-defined]
    companion._load_cache = resilient_load
    companion._save_cache = resilient_save


def install_livedub_audio_cache_recovery() -> None:
    """Install after the base companion defines its cache helpers."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        _install_cache_recovery()
        _INSTALLED = True
        logger.info("💾 LiveDub audio cache: validated backup + self-recovery enabled")
