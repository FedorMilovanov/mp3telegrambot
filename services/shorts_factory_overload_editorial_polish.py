#!/usr/bin/env python3
"""Factory-only overload handling, editorial handoff and operator progress.

Keeps the MAX quality contract intact: Gemini 3.6 Flash, HIGH thinking and all
three planning passes.  This runtime only changes retry ownership, progress,
expensive-input reuse and the active editorial integration seams.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.globals import DOWNLOAD_DIR

logger = logging.getLogger(__name__)

_FACTORY_HTTP_TIMEOUT_MS = 900_000
_FACTORY_CACHE_DIR = DOWNLOAD_DIR / "factory_retry_cache"
_FACTORY_PENDING_DIR = DOWNLOAD_DIR / "translation_editorial_pending"
_FACTORY_CACHE_POLICY = "lossless-analysis-retry-cache-v1"
_EDITORIAL_MODE = "translation_editorial"
_INSTALLED = False

_JOB_STATE: ContextVar[dict[str, Any] | None] = ContextVar(
    "factory_overload_editorial_job_state", default=None
)
_STATUS_MESSAGE: ContextVar[Any | None] = ContextVar(
    "factory_overload_editorial_status", default=None
)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


def _cache_ttl_seconds() -> float:
    return _env_float("SHORTS_FACTORY_RETRY_CACHE_HOURS", 6.0, 1.0, 24.0) * 3600.0


def _cache_max_items() -> int:
    try:
        value = int(os.getenv("SHORTS_FACTORY_RETRY_CACHE_MAX_ITEMS", "2") or "2")
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 4))


def _heartbeat_seconds() -> float:
    return _env_float("SHORTS_FACTORY_PROGRESS_HEARTBEAT_SEC", 45.0, 15.0, 120.0)


async def _safe_status(text: str) -> None:
    status = _STATUS_MESSAGE.get()
    if status is None:
        return
    try:
        await status.edit_text(str(text)[:4000])
    except Exception:
        pass


async def _await_with_heartbeat(
    awaitable: Awaitable[Any], *, label: str, heartbeat: float | None = None
) -> Any:
    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    started = loop.time()
    interval = heartbeat or _heartbeat_seconds()
    while True:
        done, _pending = await asyncio.wait({task}, timeout=interval)
        if task in done:
            return await task
        elapsed = int(loop.time() - started)
        await _safe_status(
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


def _factory_gemini_clients() -> list[Any]:
    """Factory clients: 900s request timeout, one HTTP attempt per API key."""
    from core import globals as core_globals

    if (
        not core_globals.HAS_GEMINI
        or core_globals.genai is None
        or core_globals.types is None
    ):
        return []
    retry = core_globals.types.HttpRetryOptions(attempts=1)
    http_options = core_globals.types.HttpOptions(
        timeout=_FACTORY_HTTP_TIMEOUT_MS,
        retry_options=retry,
    )
    return [
        core_globals.genai.Client(api_key=key, http_options=http_options)
        for key in _factory_api_keys()
    ]


def _exception_status_code(exc: BaseException) -> int | None:
    for name in ("code", "status_code", "status"):
        raw = getattr(exc, name, None)
        try:
            value = int(raw)
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
    code = _exception_status_code(exc)
    if code in {429, 500, 502, 503, 504}:
        return True
    text = str(exc or "").casefold()
    return any(
        marker in text
        for marker in (
            "unavailable",
            "high demand",
            "resource exhausted",
            "temporarily unavailable",
        )
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
    raw = f"{str(media_id).strip()}\0{str(url).strip()}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


def _copy_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)


def _cache_meta(key: str) -> Path:
    return _FACTORY_CACHE_DIR / f"{key}.json"


def _cleanup_retry_cache() -> None:
    _FACTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    valid: list[tuple[float, Path, dict[str, Any]]] = []
    for meta in _FACTORY_CACHE_DIR.glob("*.json"):
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
            created = float(payload.get("created_at") or 0.0)
            media = _FACTORY_CACHE_DIR / Path(str(payload.get("filename") or "")).name
            if created <= 0 or now - created > _cache_ttl_seconds() or not media.is_file():
                media.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                continue
            valid.append((created, meta, payload))
        except Exception:
            meta.unlink(missing_ok=True)
    valid.sort(key=lambda item: item[0], reverse=True)
    for _created, meta, payload in valid[_cache_max_items() :]:
        (_FACTORY_CACHE_DIR / Path(str(payload.get("filename") or "")).name).unlink(
            missing_ok=True
        )
        meta.unlink(missing_ok=True)


async def _cached_analysis_audio(url: str, media_id: str) -> Path | None:
    from services.media_delivery_probe import probe_media_async
    from services.shorts_factory_source import factory_audio_probe_is_usable

    _cleanup_retry_cache()
    key = _cache_key(url, media_id)
    meta = _cache_meta(key)
    if not meta.is_file():
        return None
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
        if payload.get("policy") != _FACTORY_CACHE_POLICY:
            return None
        if time.time() - float(payload.get("created_at") or 0.0) > _cache_ttl_seconds():
            return None
        cached = _FACTORY_CACHE_DIR / Path(str(payload.get("filename") or "")).name
        if not cached.is_file() or cached.stat().st_size != int(payload.get("size_bytes") or 0):
            return None
        if await asyncio.to_thread(_sha256_file, cached) != str(payload.get("sha256") or ""):
            return None
        probe = await probe_media_async(cached)
        if not factory_audio_probe_is_usable(probe):
            return None
        ephemeral = DOWNLOAD_DIR / (
            f"{media_id}_factory_retry_{uuid.uuid4().hex[:10]}{cached.suffix.lower()}"
        )
        await asyncio.to_thread(_copy_or_link, cached, ephemeral)
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
    _FACTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    final = _FACTORY_CACHE_DIR / f"{key}_{sha[:12]}{source.suffix.lower()}"
    if not final.exists():
        try:
            await asyncio.to_thread(_copy_or_link, source, final)
        except FileExistsError:
            pass
    payload = {
        "policy": _FACTORY_CACHE_POLICY,
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
    _cleanup_retry_cache()


async def download_factory_audio_with_retry_cache(
    url: str,
    media_id: str,
    *,
    original_downloader: Callable[[str, str], Awaitable[Path]],
) -> Path:
    cached = await _cached_analysis_audio(url, media_id)
    if cached is not None:
        await _safe_status(
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
    """Same MAX 3-pass review, with explicit key rotation and stage resume."""
    import services.shorts_factory_candidates as candidates
    from services.shorts_factory_quality_gate import (
        apply_factory_quality_gate,
        validated_factory_plan_language,
    )
    from services.shorts_factory_source import factory_audio_mime_type

    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")
    clients = _factory_gemini_clients()
    if not clients or candidates.types is None:
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini 3.6")

    model = candidates.shorts_factory_model()
    mime_type = factory_audio_mime_type(audio_path)
    file_size = audio_path.stat().st_size
    scout: dict[str, Any] | None = None
    judged: dict[str, Any] | None = None
    last_error: BaseException | None = None
    overload_clients: set[int] = set()

    for client_index, client in enumerate(clients, 1):
        uploaded_name = ""
        try:
            await _safe_status(
                f"🧠 Gemini 3.6 MAX · ключ {client_index}/{len(clients)}: готовлю аудио для трёх HIGH-проходов…"
            )
            if file_size <= 18 * 1024 * 1024:
                audio_part = candidates.types.Part.from_bytes(
                    data=audio_path.read_bytes(), mime_type=mime_type
                )
            else:
                uploaded = await _await_with_heartbeat(
                    client.aio.files.upload(
                        file=audio_path,
                        config=candidates.types.UploadFileConfig(
                            mime_type=mime_type,
                            display_name=(f"Shorts Factory MAX — {performer} — {title}")[:500],
                        ),
                    ),
                    label=f"⬆️ Gemini 3.6 · ключ {client_index}/{len(clients)}: загружаю lossless-аудио…",
                )
                uploaded = await _await_with_heartbeat(
                    candidates._wait_uploaded_file(client, uploaded),
                    label=f"⏳ Gemini 3.6 · ключ {client_index}/{len(clients)}: сервер обрабатывает аудиофайл…",
                )
                audio_part = uploaded
                uploaded_name = str(getattr(uploaded, "name", "") or "")

            if scout is None:
                scout = await _await_with_heartbeat(
                    candidates._run_pass(
                        client,
                        model=model,
                        audio_part=audio_part,
                        prompt=candidates._scout_prompt(title, performer, duration, source_language),
                        max_tokens=32000,
                    ),
                    label=f"🧠 Gemini 3.6 HIGH · ключ {client_index}/{len(clients)} · проход 1/3: анализирую весь материал…",
                )
            if judged is None:
                judged = await _await_with_heartbeat(
                    candidates._run_pass(
                        client,
                        model=model,
                        audio_part=audio_part,
                        prompt=candidates._judge_prompt(scout, duration),
                        max_tokens=28000,
                    ),
                    label=f"🧠 Gemini 3.6 HIGH · ключ {client_index}/{len(clients)} · проход 2/3: независимо перепроверяю кандидатов…",
                )
            audited = await _await_with_heartbeat(
                candidates._run_pass(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=candidates._boundary_prompt(judged, duration),
                    max_tokens=28000,
                ),
                label=f"🧠 Gemini 3.6 HIGH · ключ {client_index}/{len(clients)} · проход 3/3: проверяю точные границы…",
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
            language = validated_factory_plan_language(gated)
            gated.setdefault("metadata", {})["language"] = language
            if not gated.get("shorts_candidates") and not gated.get("long_candidates"):
                report = gated.get("quality_gate") or {}
                raise RuntimeError(
                    "Ни один фрагмент не прошёл финальный MAX-порог качества: "
                    f"Shorts>={report.get('min_short_score')}, long>={report.get('min_long_score')}"
                )
            return gated
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            transient = factory_retryable_service_error(exc)
            if factory_overload_error(exc):
                overload_clients.add(client_index)
            logger.warning(
                "Shorts Factory polished client %d/%d failed: %s: %s",
                client_index,
                len(clients),
                type(exc).__name__,
                str(exc)[:500],
            )
            if transient:
                await _safe_status(
                    f"⚠️ Gemini 3.6 временно недоступна на ключе {client_index}/{len(clients)}. "
                    "Переключаю ключ без повторения уже завершённых проходов…"
                )
                continue
            # Do not carry questionable model output after semantic/schema failures.
            scout = None
            judged = None
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    if len(overload_clients) == len(clients):
        raise RuntimeError(
            "Gemini 3.6 сейчас перегружена (503/high demand) на всех доступных ключах. "
            "Качество не понижено: 3.5/2.x не использовались. Lossless-аудио сохранено "
            f"в retry-кэше примерно на {_cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."
        )
    raise RuntimeError(f"All Gemini clients failed strict Shorts Factory review: {last_error}")


def deferred_factory_ai_data(plan: dict[str, Any], *, title: str, performer: str) -> dict[str, Any]:
    state = _JOB_STATE.get()
    if state is None:
        from services.shorts_factory_candidates import factory_ai_data as real_factory_ai_data

        return real_factory_ai_data(plan, title=title, performer=performer)
    holder: dict[str, Any] = {}
    state.update(
        plan=copy.deepcopy(plan),
        title=title,
        performer=performer,
        ai_data_holder=holder,
        aligned={},
    )
    return holder


def _candidate_duration(item: dict[str, Any]) -> float:
    try:
        return float(item.get("end_seconds") or 0.0) - float(item.get("start_seconds") or 0.0)
    except (TypeError, ValueError, OverflowError):
        return -1.0


def _role_for_alignment(candidates: list[dict[str, Any]], state: dict[str, Any]) -> str:
    if candidates:
        durations = [_candidate_duration(item) for item in candidates if isinstance(item, dict)]
        if len(durations) != len(candidates) or any(value <= 0 for value in durations):
            raise RuntimeError("Factory alignment received malformed candidate durations")
        if all(35.0 <= value <= 180.0 for value in durations):
            role = "short"
        elif all(300.0 <= value <= 900.0 for value in durations):
            role = "long"
        else:
            raise RuntimeError("Factory alignment candidate role is ambiguous; refusing implicit policy")
    else:
        role = "short" if "short" not in state.get("aligned", {}) else "long"
    if role in state.setdefault("aligned", {}):
        raise RuntimeError(f"Factory alignment role {role!r} was executed twice")
    return role


def _finalize_deferred_ai_data(state: dict[str, Any]) -> None:
    aligned = state.get("aligned") or {}
    if "short" not in aligned or "long" not in aligned:
        return
    from services.shorts_factory_candidates import factory_ai_data as real_factory_ai_data

    plan = copy.deepcopy(state.get("plan") or {})
    plan["shorts_candidates"] = copy.deepcopy(aligned["short"])
    plan["long_candidates"] = copy.deepcopy(aligned["long"])
    actual = real_factory_ai_data(
        plan,
        title=state.get("title") or "",
        performer=state.get("performer") or "",
    )
    holder = state.get("ai_data_holder")
    if isinstance(holder, dict):
        holder.clear()
        holder.update(actual)
    state["render_plan"] = plan


async def translation_video_with_boundary_evidence(
    url: str,
    workdir: Path,
    duration: int,
    source_language: str,
    *,
    original_prepare: Callable[..., Awaitable[Path]],
) -> Path:
    translated = await original_prepare(url, workdir, duration, source_language)
    from services.shorts_factory_timing import prepare_factory_ru_boundary_evidence

    evidence = await _await_with_heartbeat(
        prepare_factory_ru_boundary_evidence(
            url=url, workdir=workdir, source_language=source_language
        ),
        label="🛡 Яндекс-master готов. Доказываю границы по фактической VOT RU-речи…",
    )
    state = _JOB_STATE.get()
    if state is not None:
        state["ru_boundary_evidence"] = evidence
        state["source_language"] = source_language
    return Path(translated)


def role_aware_factory_alignment(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
    candidate_kind: str | None = None,
) -> list[dict[str, Any]]:
    from services.shorts_factory_timing import (
        align_candidates_to_ru_speech,
        align_factory_livedub_candidates,
    )

    state = _JOB_STATE.get()
    if state is None:
        return align_factory_livedub_candidates(
            candidates,
            source_duration=source_duration,
            candidate_kind=(candidate_kind or "short"),
        )
    role = candidate_kind or _role_for_alignment(candidates, state)
    evidence = state.get("ru_boundary_evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError("Exact VOT RU boundary evidence was not prepared by the active executor")
    aligned = align_candidates_to_ru_speech(
        candidates,
        source_duration=source_duration,
        speech_intervals=list(evidence.get("intervals") or []),
        delay_seconds=float(evidence.get("delay_seconds") or 0.0),
        source_speech_intervals=list(evidence.get("source_speech_intervals") or []),
        source_speech_proof=str(evidence.get("source_speech_proof") or "unavailable"),
        proof=str(evidence.get("proof") or ""),
        candidate_kind=role,
    )
    state.setdefault("aligned", {})[role] = copy.deepcopy(aligned)
    _finalize_deferred_ai_data(state)
    return aligned


def _cleanup_pending_sources() -> None:
    _FACTORY_PENDING_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _cache_ttl_seconds()
    for path in _FACTORY_PENDING_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def persist_factory_source_for_editorial(
    source_path: Path,
    media_id: str,
    *,
    original_persist: Callable[[Path, str], Path],
) -> Path:
    persisted = Path(original_persist(source_path, media_id))
    state = _JOB_STATE.get()
    if state is None:
        return persisted
    _cleanup_pending_sources()
    pending = _FACTORY_PENDING_DIR / (
        f"{str(media_id)[:80]}_{uuid.uuid4().hex[:12]}{persisted.suffix.lower() or '.mp4'}"
    )
    _copy_or_link(persisted, pending)
    state["media_id"] = str(media_id)
    state["editorial_source"] = pending
    return persisted


async def _send_editorial_after_factory(
    *, url: str, update: Any, silent_errors: bool, state: dict[str, Any]
) -> None:
    from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
    from services.translation_editorial_factory import (
        factory_editorial_pack_enabled,
        prepare_factory_editorial_review,
        send_factory_editorial_files,
    )

    if not factory_editorial_pack_enabled():
        return
    plan = state.get("render_plan") or state.get("plan") or {}
    metadata = plan.get("metadata") if isinstance(plan, dict) else {}
    language = str((metadata or {}).get("language") or state.get("source_language") or "").strip().lower()
    if language.startswith("ru"):
        return
    source = Path(state.get("editorial_source") or "")
    media_id = str(state.get("media_id") or "").strip()
    if not source.is_file() or not media_id:
        raise RuntimeError("Factory editorial handoff source was not preserved")
    probe = await probe_media_async(source)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("Factory editorial preserved source failed media probe")
    assert probe is not None
    aligned = state.get("aligned") or {}
    await _safe_status(
        "🔎 Нарезки готовы. Собираю полный пакет original SRT ↔ Russian Whisper large-v3…"
    )
    pack, review, markdown = await _await_with_heartbeat(
        prepare_factory_editorial_review(
            url=url,
            media_id=media_id,
            title=state.get("title") or "",
            performer=state.get("performer") or "",
            duration=float(probe.duration),
            source_language=language or "en",
            translated_video_path=source,
            shorts_candidates=list(aligned.get("short") or []),
            long_candidates=list(aligned.get("long") or []),
            ai_data=state.get("ai_data_holder"),
        ),
        label="🔎 Translation Editorial: Whisper large-v3 проверяет всю русскую дорожку…",
        heartbeat=60.0,
    )
    if not silent_errors:
        await send_factory_editorial_files(
            update,
            pack_path=pack,
            review_path=review,
            markdown_path=markdown,
        )
    source.unlink(missing_ok=True)


async def process_factory_with_editorial(
    original_process: Callable[..., Awaitable[bool]],
    url: str,
    update: Any,
    status_msg: Any = None,
    progress_prefix: str = "",
    context: Any = None,
    silent_errors: bool = False,
) -> bool:
    state: dict[str, Any] = {}
    state_token = _JOB_STATE.set(state)
    status_token = _STATUS_MESSAGE.set(status_msg)
    try:
        result = await original_process(
            url,
            update,
            status_msg=status_msg,
            progress_prefix=progress_prefix,
            context=context,
            silent_errors=silent_errors,
        )
        if result:
            try:
                await _send_editorial_after_factory(
                    url=url, update=update, silent_errors=silent_errors, state=state
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Factory editorial post-delivery pack failed: %s", exc)
                if not silent_errors:
                    try:
                        await update.message.reply_text(
                            "⚠️ Нарезки доставлены, но editorial ZIP не собрался. "
                            f"Исходник сохранён для повторной попытки. Причина: {str(exc)[:280]}"
                        )
                    except Exception:
                        pass
        return bool(result)
    finally:
        _STATUS_MESSAGE.reset(status_token)
        _JOB_STATE.reset(state_token)


async def process_translation_editorial_only(
    url: str,
    update: Any,
    status_msg: Any = None,
    progress_prefix: str = "",
    context: Any = None,
    silent_errors: bool = False,
) -> bool:
    """ENG Yandex → original SRT + Russian Whisper ZIP, no Gemini planner."""
    del progress_prefix, context
    import pipelines.shorts_factory as factory_module
    import services.shorts_video_impl as shorts_video_impl
    from core.utils import parse_title
    from services.shorts_factory_execution_guard import (
        enforce_factory_translation_preflight,
        factory_preflight_issues,
    )
    from services.shorts_factory_source import prepare_factory_translation_video
    from services.translation_editorial_factory import (
        prepare_factory_editorial_review,
        send_factory_editorial_files,
    )

    workdir = Path(tempfile.mkdtemp(prefix="translation_editorial_only_"))
    if status_msg is None:
        status_msg = await update.message.reply_text(
            "🔎 РЕДАКТОР ПЕРЕВОДА: получаю метаданные…"
        )
    status_token = _STATUS_MESSAGE.set(status_msg)
    try:
        info = await factory_module._load_video_info(url)
        duration = int(float(info.get("duration") or 0))
        if duration <= 0:
            raise RuntimeError("Не удалось определить длительность видео")
        language = str(info.get("language") or "").strip().lower()
        if language and not language.startswith("en"):
            raise RuntimeError(
                "Режим «ENG Редактор перевода» пока принимает только английский источник; "
                f"metadata language={language!r}."
            )
        free_gb = shutil.disk_usage(DOWNLOAD_DIR).free / (1024 ** 3)
        issues = factory_preflight_issues(
            gemini_available=True,
            whisper_available=bool(shorts_video_impl.HAS_FASTER_WHISPER),
            ffmpeg_available=bool(shutil.which("ffmpeg")),
            ffprobe_available=bool(shutil.which("ffprobe")),
            free_gb=free_gb,
            min_free_gb=2.0,
        )
        if issues:
            raise RuntimeError("Editorial preflight failed: " + "; ".join(issues))
        enforce_factory_translation_preflight()

        media_id = factory_module._media_id(info, url)
        full_title = str(info.get("title") or "Видео").strip()
        channel = str(info.get("channel") or info.get("uploader") or "").strip()
        performer, title = parse_title(full_title, channel)

        translated = await _await_with_heartbeat(
            prepare_factory_translation_video(url, workdir, duration, "en"),
            label="🎙 ENG Редактор: Яндекс переводит и собирает полный master…",
            heartbeat=60.0,
        )
        pack, review, markdown = await _await_with_heartbeat(
            prepare_factory_editorial_review(
                url=url,
                media_id=media_id,
                title=title or full_title,
                performer=performer or channel,
                duration=float(duration),
                source_language="en",
                translated_video_path=translated,
                shorts_candidates=[],
                long_candidates=[],
                ai_data=None,
            ),
            label="🔎 ENG Редактор: original SRT + Russian Whisper large-v3; работа продолжается…",
            heartbeat=60.0,
        )
        if not silent_errors:
            await send_factory_editorial_files(
                update,
                pack_path=pack,
                review_path=review,
                markdown_path=markdown,
            )
        await _safe_status(
            "✅ РЕДАКТОР ПЕРЕВОДА: пакет готов. Пришлите ZIP в ChatGPT для полной редакционной сверки."
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Translation editorial-only mode failed: %s", exc)
        if not silent_errors:
            text = f"❌ РЕДАКТОР ПЕРЕВОДА: {str(exc)[:500]}"
            try:
                await status_msg.edit_text(text)
            except Exception:
                await update.message.reply_text(text)
        return False
    finally:
        _STATUS_MESSAGE.reset(status_token)
        shutil.rmtree(workdir, ignore_errors=True)


def _install_mode_ui(mode_module: Any) -> None:
    if _EDITORIAL_MODE not in mode_module.VALID_MODES:
        mode_module.VALID_MODES = tuple(mode_module.VALID_MODES) + (_EDITORIAL_MODE,)
    mode_module.MODE_LABELS[_EDITORIAL_MODE] = (
        "🔎 ENG Редактор перевода — Yandex + Whisper → ZIP"
    )
    mode_module.MODE_BUTTON_LABELS[_EDITORIAL_MODE] = "🔎 ENG Редактор перевода"
    mode_module.MODE_DESCRIPTIONS[_EDITORIAL_MODE] = (
        "Без Gemini-нарезки: полный Yandex LiveDub → original SRT → Russian Whisper large-v3 → "
        "криптографически привязанный ZIP для ChatGPT/ручной редакции."
    )
    if getattr(mode_module._analysis_keyboard, "_translation_editorial_polished", False):
        return
    original_keyboard = mode_module._analysis_keyboard

    def editorial_keyboard(current: str):
        markup = original_keyboard(current)
        rows = [list(row) for row in markup.inline_keyboard]
        rows.insert(
            max(0, len(rows) - 1),
            [
                mode_module.InlineKeyboardButton(
                    mode_module._selected_label(_EDITORIAL_MODE, current),
                    callback_data=f"set_mode:{_EDITORIAL_MODE}",
                )
            ],
        )
        return mode_module.InlineKeyboardMarkup(rows)

    editorial_keyboard._translation_editorial_polished = True  # type: ignore[attr-defined]
    mode_module._analysis_keyboard = editorial_keyboard


def install_shorts_factory_overload_editorial_polish() -> bool:
    """Install after shorts-factory-max and preserve every non-Factory route."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import handlers.mode_command as mode_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts_factory as factory_module
    import services.shorts_factory_execution_guard as guard_module
    import services.shorts_factory_runtime as runtime_module

    _install_mode_ui(mode_module)

    original_downloader = factory_module._download_factory_audio

    async def cached_downloader(url: str, media_id: str) -> Path:
        return await download_factory_audio_with_retry_cache(
            url, media_id, original_downloader=original_downloader
        )

    factory_module._download_factory_audio = cached_downloader
    factory_module.create_factory_plan = create_factory_plan_resumable

    original_prepare = factory_module._prepare_translation_video

    async def prepared_with_evidence(url, workdir, duration, source_language):
        return await translation_video_with_boundary_evidence(
            url,
            workdir,
            duration,
            source_language,
            original_prepare=original_prepare,
        )

    factory_module._prepare_translation_video = prepared_with_evidence
    factory_module._shift_candidates_for_livedub = role_aware_factory_alignment

    original_persist = factory_module._persist_factory_source

    def persisted_for_editorial(source_path, media_id):
        return persist_factory_source_for_editorial(
            source_path, media_id, original_persist=original_persist
        )

    factory_module._persist_factory_source = persisted_for_editorial
    guard_module.factory_ai_data = deferred_factory_ai_data

    active_factory_process = factory_module.process_shorts_factory

    async def factory_process(
        url,
        update,
        status_msg=None,
        progress_prefix="",
        context=None,
        silent_errors=False,
    ):
        return await process_factory_with_editorial(
            active_factory_process,
            url,
            update,
            status_msg=status_msg,
            progress_prefix=progress_prefix,
            context=context,
            silent_errors=silent_errors,
        )

    factory_module.process_shorts_factory = factory_process

    previous_commands_process = commands_module.process_single_video
    previous_playlist_process = playlist_module.process_single_video

    def wrap_router(previous_process):
        async def polished_route(
            url,
            update,
            status_msg=None,
            progress_prefix="",
            context=None,
            silent_errors=False,
        ):
            user = getattr(update, "effective_user", None)
            user_id = int(getattr(user, "id", 0) or 0)
            mode = await mode_module.get_user_mode(user_id) if user_id else "rus"
            if mode == _EDITORIAL_MODE:
                return await process_translation_editorial_only(
                    url,
                    update,
                    status_msg=status_msg,
                    progress_prefix=progress_prefix,
                    context=context,
                    silent_errors=silent_errors,
                )
            if mode == "shorts_max":
                import services.shorts_video_impl as shorts_video_impl

                if not shorts_video_impl.HAS_FASTER_WHISPER:
                    if (
                        not silent_errors
                        and getattr(update, "effective_message", None) is not None
                    ):
                        await update.effective_message.reply_text(
                            "❌ SHORTS FACTORY MAX требует faster-whisper large-v3."
                        )
                    return False
                completion_token = runtime_module._FACTORY_COMPLETED_DELIVERIES.set(None)
                wrapped_status = (
                    runtime_module._FactoryStatusProxy(status_msg)
                    if status_msg is not None
                    else None
                )
                try:
                    result = await factory_module.process_shorts_factory(
                        url,
                        update,
                        status_msg=wrapped_status,
                        progress_prefix=progress_prefix,
                        context=context,
                        silent_errors=silent_errors,
                    )
                    shorts_sent, longs_sent = runtime_module.factory_completed_delivery_counts()
                    return bool(result and (shorts_sent or longs_sent))
                finally:
                    runtime_module._FACTORY_COMPLETED_DELIVERIES.reset(completion_token)
            return await previous_process(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )

        polished_route._factory_overload_editorial_polish = True  # type: ignore[attr-defined]
        return polished_route

    commands_module.process_single_video = wrap_router(previous_commands_process)
    playlist_module.process_single_video = wrap_router(previous_playlist_process)

    _cleanup_retry_cache()
    _cleanup_pending_sources()
    _INSTALLED = True
    logger.info(
        "Shorts Factory overload/editorial polish installed: Gemini 3.6/HIGH 3-pass preserved, "
        "Factory-only HTTP retry ownership, resumable key rotation, lossless retry cache, "
        "RU boundary evidence restored, post-alignment ai_data, editorial ZIP, standalone ENG editor"
    )
    return True


__all__ = [
    "create_factory_plan_resumable",
    "deferred_factory_ai_data",
    "download_factory_audio_with_retry_cache",
    "factory_overload_error",
    "factory_retryable_service_error",
    "install_shorts_factory_overload_editorial_polish",
    "process_translation_editorial_only",
    "role_aware_factory_alignment",
]
