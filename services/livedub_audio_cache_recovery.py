#!/usr/bin/env python3
"""Recoverable persistence backend for LiveDub companion audio file IDs.

This module is a normal service, not a runtime installer.  The companion cache
calls it directly, so corruption recovery and exact-generation verification do
not depend on import order or monkey-patching private functions.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 500


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


def _saved_at(item: tuple[str, Any]) -> float:
    value = item[1]
    if not isinstance(value, dict):
        return 0.0
    try:
        return float(value.get("saved_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _expected_generation(data: dict[str, Any]) -> dict[str, Any]:
    return dict(sorted(data.items(), key=_saved_at, reverse=True)[:_MAX_ENTRIES])


def _atomic_copy(source: Path, target: Path) -> bool:
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
        logger.warning(
            "[LiveDubAudioCache] atomic copy failed %s -> %s: %s",
            source,
            target,
            exc,
        )
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        with temp.open("wb") as dst:
            dst.write(encoded)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, path)
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
        "[LiveDubAudioCache] primary recovered from backup: entries=%d restored=%s",
        len(data),
        restored,
    )
    return data


def load_recoverable_cache(path: Path) -> dict[str, Any]:
    """Read a bounded cache, restoring the last validated generation if needed."""
    path = Path(path)
    primary = _read_mapping(path)
    if primary is not None:
        return primary
    recovered = _recover_primary(path, _backup_path(path))
    return recovered if recovered is not None else {}


def save_recoverable_cache(path: Path, data: dict[str, Any]) -> None:
    """Persist newest 500 entries and prove the exact generation reached disk."""
    path = Path(path)
    expected = _expected_generation(data)
    backup = _backup_path(path)
    previous = _read_mapping(path)
    if previous is not None:
        _atomic_copy(path, backup)

    try:
        _atomic_write(path, expected)
        written = _read_mapping(path)
        if written == expected:
            return
        raise RuntimeError("persisted LiveDub audio cache differs from requested generation")
    except BaseException:
        recovered = _recover_primary(path, backup)
        if recovered is None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def validate_livedub_audio_cache_backend() -> str:
    """Startup contract used by tests/diagnostics; performs no mutation."""
    if _MAX_ENTRIES != 500:
        raise RuntimeError("unexpected LiveDub audio cache retention contract")
    return "source-owned recoverable audio cache; exact-generation verification"


# Compatibility name for old diagnostics. It no longer installs or replaces anything.
def install_livedub_audio_cache_recovery() -> str:
    return validate_livedub_audio_cache_backend()


__all__ = [
    "load_recoverable_cache",
    "save_recoverable_cache",
    "validate_livedub_audio_cache_backend",
]
