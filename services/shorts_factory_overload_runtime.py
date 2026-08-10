#!/usr/bin/env python3
"""Factory-only Gemini overload recovery and lossless retry cache."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.globals import DOWNLOAD_DIR

logger = logging.getLogger(__name__)

FACTORY_HTTP_TIMEOUT_MS = 900_000
FACTORY_CACHE_DIR = DOWNLOAD_DIR / "factory_retry_cache"
FACTORY_CACHE_POLICY = "lossless-analysis-retry-cache-v1"
STATUS_MESSAGE: ContextVar[Any | None] = ContextVar("factory_overload_status", default=None)


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


def heartbeat_seconds() -> float:
    return _env_float("SHORTS_FACTORY_PROGRESS_HEARTBEAT_SEC", 45.0, 15.0, 120.0)


async def safe_status(text: str) -> None:
    status = STATUS_MESSAGE.get()
    if status is None:
        return
    try:
        await status.edit_text(str(text)[:4000])
    except Exception:
        pass


async def await_with_heartbeat(
    awaitable: Awaitable[Any], *, label: str, heartbeat: float | None = None
) -> Any:
    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    started = loop.time()
    interval = heartbeat or heartbeat_seconds()
    while True:
        done, _pending = await asyncio.wait({task}, timeout=interval)
        if task in done:
            return await task
        elapsed = int(loop.time() - started)
        await safe_status(
            f"{label}\n⏱ Работа продолжается: {elapsed // 60} мин {elapsed % 60:02d} сек"
        )


def _factory_api_keys() -> list[str]:
    from core import globals as core_globals

    keys = [
        str(getattr(core_globals, "GEMINI_API_KEY", "") or "").strip(),
        str(getattr(core_globals, "GEMINI_API_KEY_2", "") or "").strip(),
        str(getattr(core_globals, "GEMINI_API_KEY_3", "") or "").strip(),
        str(getattr(core_globals, "GEMINI_API_KEY_4", "") or "").strip(),
    ]
    return list(dict.fromkeys(key for key in keys if key))


def factory_gemini_clients() -> list[Any]:
    """Keep 900s HIGH-pass time while disabling hidden SDK HTTP retries."""
    from core import globals as core_globals

    if not core_globals.HAS_GEMINI or core_globals.genai is None or core_globals.types is None:
        return []
    options = core_globals.types.HttpOptions(
        timeout=FACTORY_HTTP_TIMEOUT_MS,
        retry_options=core_globals.types.HttpRetryOptions(attempts=1),
    )
    return [
        core_globals.genai.Client(api_key=key, http_options=options)
        for key in _factory_api_keys()
    ]


def _exception_status_code(exc: BaseException) -> int | None:
    for name in ("code", "status_code", "status"):
        try:
            value = int(getattr(exc, name, None))
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            return value
    text = str(exc or "").casefold()
    for code in (429, 500, 502, 503, 504):
        if str(code) in text:
            return code
    return None


def factory_retryable_service_error(exc: BaseException) -> bool:
    if _exception_status_code(exc) in {429, 500, 502, 503, 504}:
        return True
    text = str(exc or "").casefold()
    return any(
        marker in text
        for marker in ("unavailable", "high demand", "resource exhausted", "temporarily unavailable")
    )


def factory_overload_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return _exception_status_code(exc) == 503 or "high demand" in text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_key(url: str, media_id: str) -> str:
    data = f"{str(media_id).strip()}\0{str(url).strip()}".encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()[:24]


def copy_or_link(source: Path, destination: Path) -> None:
    """Create one exact destination and remove partial copy failures."""
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


def cleanup_retry_cache() -> None:
    FACTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    valid: list[tuple[float, Path, dict[str, Any]]] = []
    referenced: set[str] = set()
    for meta in FACTORY_CACHE_DIR.glob("*.json"):
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
            created = float(payload.get("created_at") or 0.0)
            filename = Path(str(payload.get("filename") or "")).name
            media = FACTORY_CACHE_DIR / filename
            if created <= 0 or now - created > cache_ttl_seconds() or not media.is_file():
                media.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                continue
            referenced.add(filename)
            valid.append((created, meta, payload))
        except Exception:
            meta.unlink(missing_ok=True)
    valid.sort(key=lambda item: item[0], reverse=True)
    for _created, meta, payload in valid[cache_max_items() :]:
        filename = Path(str(payload.get("filename") or "")).name
        (FACTORY_CACHE_DIR / filename).unlink(missing_ok=True)
        referenced.discard(filename)
        meta.unlink(missing_ok=True)
    for path in FACTORY_CACHE_DIR.iterdir():
        if path.is_file() and path.suffix != ".json" and path.name not in referenced:
            path.unlink(missing_ok=True)


async def _cached_analysis_audio(url: str, media_id: str) -> Path | None:
    from services.media_delivery_probe import probe_media_async
    from services.shorts_factory_source import factory_audio_probe_is_usable

    cleanup_retry_cache()
    meta = _cache_meta(_cache_key(url, media_id))
    if not meta.is_file():
        return None
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
        if payload.get("policy") != FACTORY_CACHE_POLICY:
            return None
        if time.time() - float(payload.get("created_at") or 0.0) > cache_ttl_seconds():
            return None
        cached = FACTORY_CACHE_DIR / Path(str(payload.get("filename") or "")).name
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
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, _cache_meta(key))
    cleanup_retry_cache()


async def download_factory_audio_with_retry_cache(
    url: str,
    media_id: str,
    *,
    original_downloader: Callable[[str, str], Awaitable[Path]],
) -> Path:
    cached = await _cached_analysis_audio(url, media_id)
    if cached is not None:
        await safe_status(
            "♻️ SHORTS FACTORY MAX: использую уже проверенное lossless-аудио прошлого запуска; повторная загрузка не нужна."
        )
        return cached
    prepared = Path(await original_downloader(url, media_id))
    await _store_analysis_audio(url, media_id, prepared)
    return prepared


async def create_factory_plan_resumable(
    audio_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
) -> dict[str, Any]:
    """Same 3-pass 3.6/HIGH contract; only transient stage retries resume."""
    import services.shorts_factory_candidates as candidates
    from services.shorts_factory_quality_gate import (
        apply_factory_quality_gate,
        validated_factory_plan_language,
    )
    from services.shorts_factory_source import factory_audio_mime_type

    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")
    clients = factory_gemini_clients()
    if not clients or candidates.types is None:
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini 3.6")
    model = candidates.shorts_factory_model()
    mime_type = factory_audio_mime_type(audio_path)
    file_size = audio_path.stat().st_size
    scout = judged = None
    last_error: BaseException | None = None
    overloaded: set[int] = set()

    for index, client in enumerate(clients, 1):
        uploaded_name = ""
        try:
            await safe_status(f"🧠 Gemini 3.6 MAX · ключ {index}/{len(clients)}: готовлю аудио…")
            if file_size <= 18 * 1024 * 1024:
                audio_part = candidates.types.Part.from_bytes(
                    data=audio_path.read_bytes(), mime_type=mime_type
                )
            else:
                uploaded = await await_with_heartbeat(
                    client.aio.files.upload(
                        file=audio_path,
                        config=candidates.types.UploadFileConfig(
                            mime_type=mime_type,
                            display_name=(f"Shorts Factory MAX — {performer} — {title}")[:500],
                        ),
                    ),
                    label=f"⬆️ Gemini 3.6 · ключ {index}/{len(clients)}: загружаю lossless-аудио…",
                )
                uploaded = await await_with_heartbeat(
                    candidates._wait_uploaded_file(client, uploaded),
                    label=f"⏳ Gemini 3.6 · ключ {index}/{len(clients)}: сервер обрабатывает аудио…",
                )
                audio_part = uploaded
                uploaded_name = str(getattr(uploaded, "name", "") or "")

            if scout is None:
                scout = await await_with_heartbeat(
                    candidates._run_pass(
                        client,
                        model=model,
                        audio_part=audio_part,
                        prompt=candidates._scout_prompt(title, performer, duration, source_language),
                        max_tokens=32000,
                    ),
                    label=f"🧠 Gemini 3.6 HIGH · ключ {index}/{len(clients)} · проход 1/3…",
                )
            if judged is None:
                judged = await await_with_heartbeat(
                    candidates._run_pass(
                        client,
                        model=model,
                        audio_part=audio_part,
                        prompt=candidates._judge_prompt(scout, duration),
                        max_tokens=28000,
                    ),
                    label=f"🧠 Gemini 3.6 HIGH · ключ {index}/{len(clients)} · проход 2/3…",
                )
            audited = await await_with_heartbeat(
                candidates._run_pass(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=candidates._boundary_prompt(judged, duration),
                    max_tokens=28000,
                ),
                label=f"🧠 Gemini 3.6 HIGH · ключ {index}/{len(clients)} · проход 3/3…",
            )
            plan = candidates.validate_factory_plan(audited, duration, require_verified=True)
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError("Three-pass Gemini review produced no verified candidates")
            plan.update(
                model=model,
                thinking_level="high",
                review_passes=3,
                strict_quality=True,
                audio_mime_type=mime_type,
            )
            gated = apply_factory_quality_gate(plan)
            gated.setdefault("metadata", {})["language"] = validated_factory_plan_language(gated)
            if not gated.get("shorts_candidates") and not gated.get("long_candidates"):
                raise RuntimeError("No candidates passed the final Factory MAX quality gate")
            return gated
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            transient = factory_retryable_service_error(exc)
            if factory_overload_error(exc):
                overloaded.add(index)
            logger.warning(
                "Shorts Factory polished client %d/%d failed: %s: %s",
                index,
                len(clients),
                type(exc).__name__,
                str(exc)[:500],
            )
            if transient:
                await safe_status(
                    f"⚠️ Gemini 3.6 временно недоступна на ключе {index}/{len(clients)}. "
                    "Переключаю ключ без повторения уже завершённых проходов…"
                )
                continue
            scout = judged = None
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    if len(overloaded) == len(clients):
        raise RuntimeError(
            "Gemini 3.6 сейчас перегружена (503/high demand) на всех доступных ключах. "
            "Качество не понижено: 3.5/2.x не использовались. Lossless-аудио сохранено "
            f"в retry-кэше примерно на {cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."
        )
    raise RuntimeError(f"All Gemini clients failed strict Shorts Factory review: {last_error}")


__all__ = [
    "STATUS_MESSAGE",
    "await_with_heartbeat",
    "cache_ttl_seconds",
    "cleanup_retry_cache",
    "copy_or_link",
    "create_factory_plan_resumable",
    "download_factory_audio_with_retry_cache",
    "factory_gemini_clients",
    "factory_overload_error",
    "factory_retryable_service_error",
    "safe_status",
]
