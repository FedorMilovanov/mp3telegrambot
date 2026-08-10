#!/usr/bin/env python3
"""Active Factory boundary/editorial bridge and standalone ENG editor mode."""
from __future__ import annotations

import asyncio
import copy
import logging
import shutil
import tempfile
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.globals import DOWNLOAD_DIR
from services.shorts_factory_overload_runtime import (
    STATUS_MESSAGE,
    await_with_heartbeat,
    cache_max_items,
    cache_ttl_seconds,
    copy_or_link,
    safe_status,
)

logger = logging.getLogger(__name__)

EDITORIAL_MODE = "translation_editorial"
PENDING_DIR = DOWNLOAD_DIR / "translation_editorial_pending"
JOB_STATE: ContextVar[dict[str, Any] | None] = ContextVar(
    "factory_editorial_bridge_job", default=None
)
_PENDING_LOCK = threading.RLock()
_ACTIVE_PENDING: set[str] = set()


def _candidate_duration(item: dict[str, Any]) -> float:
    try:
        return float(item.get("end_seconds") or 0.0) - float(
            item.get("start_seconds") or 0.0
        )
    except (TypeError, ValueError, OverflowError):
        return -1.0


def _role_for_alignment(items: list[dict[str, Any]], state: dict[str, Any]) -> str:
    if items:
        durations = [
            _candidate_duration(item) for item in items if isinstance(item, dict)
        ]
        if len(durations) != len(items) or any(value <= 0 for value in durations):
            raise RuntimeError(
                "Factory alignment received malformed candidate durations"
            )
        if all(35.0 <= value <= 180.0 for value in durations):
            role = "short"
        elif all(300.0 <= value <= 900.0 for value in durations):
            role = "long"
        else:
            raise RuntimeError(
                "Factory alignment candidate role is ambiguous; "
                "refusing implicit policy"
            )
    else:
        role = "short" if "short" not in state.get("aligned", {}) else "long"
    if role in state.setdefault("aligned", {}):
        raise RuntimeError(f"Factory alignment role {role!r} was executed twice")
    return role


def _finish_ai_data(state: dict[str, Any]) -> None:
    aligned = state.get("aligned") or {}
    if "short" not in aligned or "long" not in aligned:
        return
    from services.shorts_factory_candidates import factory_ai_data

    plan = copy.deepcopy(state.get("plan") or {})
    plan["shorts_candidates"] = copy.deepcopy(aligned["short"])
    plan["long_candidates"] = copy.deepcopy(aligned["long"])
    actual = factory_ai_data(
        plan,
        title=state.get("title") or "",
        performer=state.get("performer") or "",
    )
    holder = state.get("ai_data_holder")
    if isinstance(holder, dict):
        holder.clear()
        holder.update(actual)
    state["render_plan"] = plan


async def _wait_for_boundary_evidence(
    *, url: str, workdir: Path, source_language: str
) -> dict[str, Any]:
    """Start proof as soon as exact VOT provenance appears, overlapping mix/download."""
    from services.livedub_ru_provenance import read_ru_audio_provenance
    from services.shorts_factory_timing import prepare_factory_ru_boundary_evidence

    loop = asyncio.get_running_loop()
    started = loop.time()
    while read_ru_audio_provenance(workdir) is None:
        if loop.time() - started > 7200:
            raise TimeoutError(
                "Exact VOT RU provenance did not appear within two hours"
            )
        await asyncio.sleep(1.0)
    return await prepare_factory_ru_boundary_evidence(
        url=url,
        workdir=workdir,
        source_language=source_language,
    )


async def translation_video_with_boundary_evidence(
    url: str,
    workdir: Path,
    duration: int,
    source_language: str,
    *,
    original_prepare: Callable[..., Awaitable[Path]],
) -> Path:
    evidence_task = asyncio.create_task(
        _wait_for_boundary_evidence(
            url=url,
            workdir=workdir,
            source_language=source_language,
        ),
        name="factory-boundary-proof-overlap",
    )
    try:
        translated = await original_prepare(url, workdir, duration, source_language)
        evidence = await await_with_heartbeat(
            evidence_task,
            label=(
                "🛡 Yandex master готов. "
                "Завершаю доказательство VOT RU-границ…"
            ),
        )
    except BaseException:
        if not evidence_task.done():
            evidence_task.cancel()
        await asyncio.gather(evidence_task, return_exceptions=True)
        raise
    state = JOB_STATE.get()
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

    state = JOB_STATE.get()
    if state is None:
        return align_factory_livedub_candidates(
            candidates,
            source_duration=source_duration,
            candidate_kind=(candidate_kind or "short"),
        )
    role = candidate_kind or _role_for_alignment(candidates, state)
    evidence = state.get("ru_boundary_evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError(
            "Exact VOT RU boundary evidence was not prepared by the active executor"
        )
    aligned = align_candidates_to_ru_speech(
        candidates,
        source_duration=source_duration,
        speech_intervals=list(evidence.get("intervals") or []),
        delay_seconds=float(evidence.get("delay_seconds") or 0.0),
        source_speech_intervals=list(
            evidence.get("source_speech_intervals") or []
        ),
        source_speech_proof=str(
            evidence.get("source_speech_proof") or "unavailable"
        ),
        proof=str(evidence.get("proof") or ""),
        candidate_kind=role,
    )
    state.setdefault("aligned", {})[role] = copy.deepcopy(aligned)
    _finish_ai_data(state)
    return aligned


def _pending_key(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def _set_pending_active(path: Path, active: bool) -> None:
    key = _pending_key(path)
    with _PENDING_LOCK:
        if active:
            _ACTIVE_PENDING.add(key)
        else:
            _ACTIVE_PENDING.discard(key)


def cleanup_pending_sources() -> None:
    """Bound inactive handoff masters without deleting another active job."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - cache_ttl_seconds()
    with _PENDING_LOCK:
        protected = set(_ACTIVE_PENDING)
    valid: list[tuple[float, Path]] = []
    for path in PENDING_DIR.iterdir():
        try:
            if not path.is_file() or _pending_key(path) in protected:
                continue
            modified = path.stat().st_mtime
            if modified < cutoff:
                path.unlink(missing_ok=True)
                continue
            valid.append((modified, path))
        except OSError:
            pass
    valid.sort(key=lambda item: item[0], reverse=True)
    for _modified, path in valid[cache_max_items() :]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def persist_source_for_editorial(
    source_path: Path,
    media_id: str,
    *,
    original_persist: Callable[[Path, str], Path],
) -> Path:
    persisted = Path(original_persist(source_path, media_id))
    state = JOB_STATE.get()
    if state is None:
        return persisted
    metadata = (state.get("plan") or {}).get("metadata") or {}
    language = str(
        metadata.get("language") or state.get("source_language") or ""
    ).lower()
    if language.startswith("ru"):
        return persisted
    cleanup_pending_sources()
    pending = PENDING_DIR / (
        f"{str(media_id)[:80]}_{uuid.uuid4().hex[:12]}"
        f"{persisted.suffix.lower() or '.mp4'}"
    )
    copy_or_link(persisted, pending)
    _set_pending_active(pending, True)
    state["media_id"] = str(media_id)
    state["editorial_source"] = pending
    cleanup_pending_sources()
    return persisted


async def _send_editorial_after_factory(
    *, url: str, update: Any, silent_errors: bool, state: dict[str, Any]
) -> None:
    from services.media_delivery_probe import (
        media_probe_is_deliverable,
        probe_media_async,
    )
    from services.translation_editorial_factory import (
        factory_editorial_pack_enabled,
        prepare_factory_editorial_review,
        send_factory_editorial_files,
    )

    del silent_errors
    if not factory_editorial_pack_enabled():
        return
    plan = state.get("render_plan") or state.get("plan") or {}
    metadata = plan.get("metadata") if isinstance(plan, dict) else {}
    language = str(
        (metadata or {}).get("language")
        or state.get("source_language")
        or ""
    ).lower()
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
    pack, review, markdown = await await_with_heartbeat(
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
        label=(
            "🔎 Translation Editorial: original SRT + "
            "Russian Whisper large-v3…"
        ),
        heartbeat=60.0,
    )
    await send_factory_editorial_files(
        update,
        pack_path=pack,
        review_path=review,
        markdown_path=markdown,
    )
    source.unlink(missing_ok=True)
    await safe_status(
        "✅ SHORTS FACTORY MAX + Translation Editorial завершены: "
        "ZIP готов для ChatGPT."
    )


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
    state_token = JOB_STATE.set(state)
    status_token = STATUS_MESSAGE.set(status_msg)
    result = False
    try:
        result = bool(
            await original_process(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )
        )
        if result:
            try:
                await _send_editorial_after_factory(
                    url=url,
                    update=update,
                    silent_errors=silent_errors,
                    state=state,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Factory editorial post-delivery pack failed: %s", exc
                )
                await safe_status(
                    "⚠️ Нарезки доставлены, но editorial ZIP не собрался; "
                    "master временно сохранён для диагностики."
                )
                if not silent_errors:
                    try:
                        await update.message.reply_text(
                            "⚠️ Нарезки доставлены, но editorial ZIP не собрался. "
                            f"Причина: {str(exc)[:280]}"
                        )
                    except Exception:
                        pass
        return result
    finally:
        pending_value = state.get("editorial_source")
        if pending_value:
            pending = Path(pending_value)
            _set_pending_active(pending, False)
            if not result:
                pending.unlink(missing_ok=True)
        cleanup_pending_sources()
        STATUS_MESSAGE.reset(status_token)
        JOB_STATE.reset(state_token)


async def process_translation_editorial_only(
    url: str,
    update: Any,
    status_msg: Any = None,
    progress_prefix: str = "",
    context: Any = None,
    silent_errors: bool = False,
) -> bool:
    """ENG Yandex master → original SRT + Russian Whisper ZIP; no Gemini planner."""
    del progress_prefix, context
    import pipelines.shorts_factory as factory_module
    import services.shorts_video_impl as shorts_video_impl
    from core.utils import parse_title
    from services.media_delivery_probe import (
        media_probe_is_deliverable,
        probe_media_async,
    )
    from services.shorts_factory_disk_guard import (
        mark_factory_analysis_audio_skipped,
    )
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
    token = STATUS_MESSAGE.set(status_msg)
    try:
        info = await factory_module._load_video_info(url)
        # The disk guard normally serializes maximum video behind analysis audio.
        # This mode intentionally has no analysis-audio phase. Release only that
        # ordering dependency; duration hints and the full video disk proof stay.
        mark_factory_analysis_audio_skipped(url)
        duration = int(float(info.get("duration") or 0))
        if duration <= 0:
            raise RuntimeError("Не удалось определить длительность видео")
        language = str(info.get("language") or "").strip().lower()
        if language and not language.startswith("en"):
            raise RuntimeError(
                "Режим «ENG Редактор перевода» принимает английский источник; "
                f"metadata language={language!r}."
            )
        free_gb = shutil.disk_usage(DOWNLOAD_DIR).free / (1024**3)
        issues = factory_preflight_issues(
            gemini_available=True,
            whisper_available=bool(shorts_video_impl.HAS_FASTER_WHISPER),
            ffmpeg_available=bool(shutil.which("ffmpeg")),
            ffprobe_available=bool(shutil.which("ffprobe")),
            free_gb=free_gb,
            min_free_gb=2.0,
        )
        if issues:
            raise RuntimeError(
                "Editorial preflight failed: " + "; ".join(issues)
            )
        enforce_factory_translation_preflight()
        media_id = factory_module._media_id(info, url)
        full_title = str(info.get("title") or "Видео").strip()
        channel = str(
            info.get("channel") or info.get("uploader") or ""
        ).strip()
        performer, title = parse_title(full_title, channel)

        translated = await await_with_heartbeat(
            prepare_factory_translation_video(url, workdir, duration, "en"),
            label=(
                "🎙 ENG Редактор: Яндекс переводит и собирает "
                "полный master…"
            ),
            heartbeat=60.0,
        )
        probe = await probe_media_async(translated)
        if not media_probe_is_deliverable(probe):
            raise RuntimeError("Yandex editorial master failed video+audio probe")
        assert probe is not None
        pack, review, markdown = await await_with_heartbeat(
            prepare_factory_editorial_review(
                url=url,
                media_id=media_id,
                title=title or full_title,
                performer=performer or channel,
                duration=float(probe.duration),
                source_language="en",
                translated_video_path=translated,
                shorts_candidates=[],
                long_candidates=[],
                ai_data=None,
            ),
            label=(
                "🔎 ENG Редактор: original SRT + "
                "Russian Whisper large-v3…"
            ),
            heartbeat=60.0,
        )
        await send_factory_editorial_files(
            update,
            pack_path=pack,
            review_path=review,
            markdown_path=markdown,
        )
        await safe_status(
            "✅ РЕДАКТОР ПЕРЕВОДА: ZIP готов. Пришлите его в ChatGPT."
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
        STATUS_MESSAGE.reset(token)
        shutil.rmtree(workdir, ignore_errors=True)


def install_mode_ui(mode_module: Any) -> None:
    if EDITORIAL_MODE not in mode_module.VALID_MODES:
        mode_module.VALID_MODES = tuple(mode_module.VALID_MODES) + (
            EDITORIAL_MODE,
        )
    mode_module.MODE_LABELS[EDITORIAL_MODE] = (
        "🔎 ENG Редактор перевода — Yandex + Whisper → ZIP"
    )
    mode_module.MODE_BUTTON_LABELS[EDITORIAL_MODE] = (
        "🔎 ENG Редактор перевода"
    )
    mode_module.MODE_DESCRIPTIONS[EDITORIAL_MODE] = (
        "Без Gemini-нарезки: Yandex LiveDub → original SRT → "
        "Russian Whisper large-v3 → ZIP для ChatGPT."
    )
    if getattr(
        mode_module._analysis_keyboard,
        "_translation_editorial_polished",
        False,
    ):
        return
    original = mode_module._analysis_keyboard

    def keyboard(current: str):
        markup = original(current)
        rows = [list(row) for row in markup.inline_keyboard]
        rows.insert(
            max(0, len(rows) - 1),
            [
                mode_module.InlineKeyboardButton(
                    mode_module._selected_label(EDITORIAL_MODE, current),
                    callback_data=f"set_mode:{EDITORIAL_MODE}",
                )
            ],
        )
        return mode_module.InlineKeyboardMarkup(rows)

    keyboard._translation_editorial_polished = True  # type: ignore[attr-defined]
    mode_module._analysis_keyboard = keyboard


__all__ = [
    "EDITORIAL_MODE",
    "JOB_STATE",
    "cleanup_pending_sources",
    "install_mode_ui",
    "persist_source_for_editorial",
    "process_factory_with_editorial",
    "process_translation_editorial_only",
    "role_aware_factory_alignment",
    "translation_video_with_boundary_evidence",
]
