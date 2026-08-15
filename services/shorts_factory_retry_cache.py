#!/usr/bin/env python3
"""Verified Factory analysis-audio retry cache.

The cache is request-agnostic. Progress messages are passed explicitly and no
ContextVar, installer or runtime rebinding is used.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.globals import DOWNLOAD_DIR
from services.shorts_factory_capacity import safe_status

logger = logging.getLogger(__name__)

FACTORY_CACHE_DIR = DOWNLOAD_DIR / "factory_retry_cache"
FACTORY_CACHE_POLICY = "lossless-analysis-retry-cache-v1"
_CACHE_LOCK = threading.RLock()
_ACTIVE_CACHE_PATHS: dict[str, int] = {}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


def cache_ttl_seconds() -> float:
    return _env_float("SHORTS_FACTORY_RETRY_CACHE_HOURS", 6.0, 1.0, 24.0) * 3600.0


def cache_max_items() -> int:
    try:
        value = int(os.getenv("SHORTS_FACTORY_RETRY_CACHE_MAX_ITEMS", "2") or "2")
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 4))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_key(url: str, media_id: str) -> str:
    data = f"{str(media_id).strip()}\0{str(url).strip()}".encode(
        "utf-8", errors="replace"
    )
    return hashlib.sha256(data).hexdigest()[:24]


def copy_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    created = False
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            created = True
            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _cache_meta(key: str) -> Path:
    return FACTORY_CACHE_DIR / f"{key}.json"


def _cache_path_key(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def _set_cache_path_active(path: Path, active: bool) -> None:
    key = _cache_path_key(path)
    with _CACHE_LOCK:
        if active:
            _ACTIVE_CACHE_PATHS[key] = _ACTIVE_CACHE_PATHS.get(key, 0) + 1
            return
        remaining = _ACTIVE_CACHE_PATHS.get(key, 0) - 1
        if remaining > 0:
            _ACTIVE_CACHE_PATHS[key] = remaining
        else:
            _ACTIVE_CACHE_PATHS.pop(key, None)


def cleanup_retry_cache() -> None:
    FACTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with _CACHE_LOCK:
        protected = set(_ACTIVE_CACHE_PATHS)
    valid: list[tuple[float, Path, dict[str, Any]]] = []
    referenced: set[str] = set()
    active_valid: set[str] = set()
    for meta in FACTORY_CACHE_DIR.glob("*.json"):
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
            created = float(payload.get("created_at") or 0.0)
            filename = Path(str(payload.get("filename") or "")).name
            media = FACTORY_CACHE_DIR / filename
            media_key = _cache_path_key(media)
            if media_key in protected:
                referenced.add(filename)
                active_valid.add(filename)
                valid.append((created, meta, payload))
                continue
            if (
                created <= 0
                or now - created > cache_ttl_seconds()
                or not media.is_file()
            ):
                media.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                continue
            referenced.add(filename)
            valid.append((created, meta, payload))
        except Exception:
            meta.unlink(missing_ok=True)
    valid.sort(key=lambda item: item[0], reverse=True)
    inactive_kept = 0
    for _created, meta, payload in valid:
        filename = Path(str(payload.get("filename") or "")).name
        if filename in active_valid:
            continue
        inactive_kept += 1
        if inactive_kept <= cache_max_items():
            continue
        (FACTORY_CACHE_DIR / filename).unlink(missing_ok=True)
        referenced.discard(filename)
        meta.unlink(missing_ok=True)
    for path in FACTORY_CACHE_DIR.iterdir():
        if (
            path.is_file()
            and path.suffix != ".json"
            and path.name not in referenced
            and _cache_path_key(path) not in protected
        ):
            path.unlink(missing_ok=True)


async def _cached_analysis_audio(url: str, media_id: str) -> Path | None:
    from services.media_delivery_probe import probe_media_async
    from services.shorts_factory_source import factory_audio_probe_is_usable

    cleanup_retry_cache()
    meta = _cache_meta(_cache_key(url, media_id))
    if not meta.is_file():
        return None
    cached: Path | None = None
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
        if payload.get("policy") != FACTORY_CACHE_POLICY:
            return None
        if time.time() - float(payload.get("created_at") or 0.0) > cache_ttl_seconds():
            return None
        cached = FACTORY_CACHE_DIR / Path(str(payload.get("filename") or "")).name
        _set_cache_path_active(cached, True)
        if not cached.is_file() or cached.stat().st_size != int(payload.get("size_bytes") or 0):
            return None
        if await asyncio.to_thread(_sha256_file, cached) != str(payload.get("sha256") or ""):
            return None
        if not factory_audio_probe_is_usable(await probe_media_async(cached)):
            return None
        ephemeral = DOWNLOAD_DIR / (
            f"{media_id}_factory_retry_{uuid.uuid4().hex[:10]}{cached.suffix.lower()}"
        )
        await asyncio.to_thread(copy_or_link, cached, ephemeral)
        logger.info("Shorts Factory retry cache hit media_id=%s file=%s", media_id, cached.name)
        return ephemeral
    except Exception as exc:
        logger.warning("Shorts Factory retry cache rejected media_id=%s: %s", media_id, exc)
        return None
    finally:
        if cached is not None:
            _set_cache_path_active(cached, False)
            cleanup_retry_cache()


async def _store_analysis_audio(url: str, media_id: str, source: Path) -> None:
    from services.media_delivery_probe import probe_media_async
    from services.shorts_factory_source import factory_audio_probe_is_usable

    source = Path(source)
    if not source.is_file() or source.stat().st_size < 1024:
        return
    probe = await probe_media_async(source)
    if not factory_audio_probe_is_usable(probe):
        return
    key = _cache_key(url, media_id)
    sha = await asyncio.to_thread(_sha256_file, source)
    FACTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    final = FACTORY_CACHE_DIR / f"{key}_{sha[:12]}{source.suffix.lower()}"
    _set_cache_path_active(final, True)
    try:
        if not final.exists():
            try:
                await asyncio.to_thread(copy_or_link, source, final)
            except FileExistsError:
                pass
        payload = {
            "policy": FACTORY_CACHE_POLICY,
            "created_at": time.time(),
            "url": str(url),
            "media_id": str(media_id),
            "filename": final.name,
            "size_bytes": final.stat().st_size,
            "duration_seconds": float(getattr(probe, "duration", 0.0) or 0.0),
            "sha256": sha,
        }
        temp = _cache_meta(key).with_suffix(f".{uuid.uuid4().hex}.tmp")
        _set_cache_path_active(temp, True)
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, _cache_meta(key))
        finally:
            _set_cache_path_active(temp, False)
            temp.unlink(missing_ok=True)
    finally:
        _set_cache_path_active(final, False)
        cleanup_retry_cache()


async def download_factory_audio_with_retry_cache(
    url: str,
    media_id: str,
    *,
    original_downloader: Callable[[str, str], Awaitable[Path]],
    status_msg: Any = None,
) -> Path:
    cached = await _cached_analysis_audio(url, media_id)
    if cached is not None:
        await safe_status(
            status_msg,
            "♻️ SHORTS FACTORY MAX: использую уже проверенное analysis-аудио "
            "прошлого запуска; повторная подготовка не нужна.",
        )
        return cached
    prepared = Path(await original_downloader(url, media_id))
    await _store_analysis_audio(url, media_id, prepared)
    return prepared


__all__ = [
    "FACTORY_CACHE_DIR",
    "FACTORY_CACHE_POLICY",
    "cache_max_items",
    "cache_ttl_seconds",
    "cleanup_retry_cache",
    "copy_or_link",
    "download_factory_audio_with_retry_cache",
]
