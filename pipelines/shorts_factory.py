#!/usr/bin/env python3
"""Standalone maximum-quality Shorts/Highlights extraction mode."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from core.url_utils import get_youtube_video_url
from core.utils import parse_title
from pipelines.clips import process_and_send_clips
from pipelines.shorts import process_and_send_shorts
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.shorts_factory_candidates import create_factory_plan, factory_ai_data
from services.shorts_factory_full_video import send_factory_full_translation_if_enabled
from services.shorts_factory_runtime import (
    factory_completed_delivery_counts,
    factory_render_context,
)
from services.shorts_factory_source import _factory_livedub_timeout_seconds
from services.shorts_video import download_video_for_shorts
from services.translation_editorial_factory import (
    factory_editorial_pack_enabled,
    prepare_factory_editorial_review,
    send_factory_editorial_files,
)

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _factory_source_timeout_seconds() -> int:
    """Use the same bounded timeout owner as the production LiveDub source."""
    return _factory_livedub_timeout_seconds()


async def _safe_status(status_msg, text: str) -> None:
    if not status_msg:
        return
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass


async def _load_video_info(url: str) -> dict[str, Any]:
    try:
        from pipelines.main_pipeline import _ytdlp_info_inprocess

        info = await _ytdlp_info_inprocess(url, 240)
        if isinstance(info, dict):
            return info
    except Exception as exc:
        logger.info("Shorts Factory metadata in-process fallback: %s", exc)

    cmd = list(YTDLP_BASE_ARGS) + ["--dump-json", "--no-playlist", url]
    proc = await run_cancellable_process(cmd, timeout=240, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "yt-dlp metadata error")[-800:])
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    raise RuntimeError("yt-dlp did not return video metadata")


def _media_id(info: dict[str, Any], url: str) -> str:
    value = str(info.get("id") or "").strip()
    if value:
        return re.sub(r"[^A-Za-z0-9_-]", "", value)[:80]
    return str(abs(hash(url)))


async def _download_factory_audio(url: str, media_id: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    output_template = DOWNLOAD_DIR / f"{media_id}_factory.%(ext)s"
    cmd = list(YTDLP_BASE_ARGS) + [
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--no-playlist",
        "--output",
        str(output_template),
        url,
    ]
    proc = await run_cancellable_process(cmd, timeout=1800, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "yt-dlp audio download error")[-900:])
    candidates = sorted(
        DOWNLOAD_DIR.glob(f"{media_id}_factory*.mp3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates or candidates[0].stat().st_size < 1024:
        raise RuntimeError("Audio download completed without a usable MP3")
    return candidates[0]


def _looks_russian(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(text or "")))


def _source_needs_translation(info: dict[str, Any]) -> bool:
    language = str(info.get("language") or "").strip().lower()
    if language.startswith(("ru", "uk", "be")):
        return False
    if language:
        return True
    title = str(info.get("title") or "")
    return bool(title and not _looks_russian(title))


def _translation_backend() -> str:
    """Reserved provider seam; only Yandex LiveDub is enabled today."""
    backend = os.getenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex_live").strip().lower()
    aliases = {
        "yandex": "yandex_live",
        "yandex_live": "yandex_live",
        "live": "yandex_live",
    }
    return aliases.get(backend, backend)


async def _prepare_translation_video(
    url: str,
    workdir: Path,
    duration: int,
    source_language: str,
) -> Path:
    backend = _translation_backend()
    if backend != "yandex_live":
        raise RuntimeError(
            "SHORTS FACTORY сейчас поддерживает только Яндекс «Живые голоса». "
            f"Backend {backend!r} оставлен для будущего расширения, но ещё не реализован."
        )
    if not _env_bool("SHORTS_FACTORY_LIVEDUB", True):
        raise RuntimeError(
            "Для иностранного источника SHORTS_FACTORY_LIVEDUB должен быть включён: "
            "собственный нейроперевод в этом режиме запрещён."
        )

    from services.yandex_live_dub import get_live_dub_video

    translated = await get_live_dub_video(
        url,
        workdir,
        duration=float(duration),
        lang=source_language,
    )
    if not translated or not translated.exists():
        raise RuntimeError("Яндекс LiveDub не вернул готовое переведённое видео")
    return translated


def _shift_candidates_for_livedub(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int,
    candidate_kind: str,
) -> list[dict[str, Any]]:
    """Fail closed unless the required runtime installs speech-proven alignment."""
    del candidates, source_duration, candidate_kind
    raise RuntimeError(
        "SHORTS FACTORY translated boundary aligner is not installed; "
        "refusing heuristic original-timeline cuts"
    )


def _cleanup_expired_factory_sources() -> None:
    retention_hours = float(
        os.getenv("SHORTS_FACTORY_SOURCE_RETENTION_HOURS", "24") or "24"
    )
    cutoff = time.time() - max(1.0, retention_hours) * 3600.0
    for path in DOWNLOAD_DIR.glob("*_factory_source.*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _persist_factory_source(source_path: Path, media_id: str) -> Path:
    """Keep one source for interactive trim buttons and reuse by long clips."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix.lower() or ".mp4"
    destination = DOWNLOAD_DIR / f"{media_id}_factory_source{suffix}"
    try:
        if source_path.resolve() == destination.resolve():
            return destination
    except OSError:
        pass

    for old in DOWNLOAD_DIR.glob(f"{media_id}_factory_source.*"):
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        shutil.move(str(source_path), str(destination))
    except (OSError, shutil.Error):
        shutil.copy2(source_path, destination)
    if not destination.exists() or destination.stat().st_size < 1024:
        raise RuntimeError("Не удалось сохранить общий источник для рендера и trim-кнопок")
    return destination


async def _validated_source_duration(source_path: Path, expected_duration: int) -> int:
    """Return the real render timeline and reject truncated video/audio sources."""
    probe = await probe_media_async(source_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("Общий Factory-источник не прошёл media probe (нужны video+audio)")
    assert probe is not None
    if probe.duration + 3.0 < float(expected_duration):
        raise RuntimeError(
            "Общий Factory-источник обрезан: "
            f"ожидалось около {expected_duration:.0f}с, получено {probe.duration:.1f}с"
        )
    return max(1, int(math.ceil(probe.duration)))


def _plan_message(plan: dict[str, Any], *, translation_required: bool) -> str:
    """Render only the publication-ready candidate plan shown to the operator."""
    meta = plan.get("metadata") or {}
    shorts = plan.get("shorts_candidates") or []
    longs = plan.get("long_candidates") or []
    title = html.escape(str(meta.get("title_ru") or "SHORTS FACTORY MAX"))
    author = html.escape(str(meta.get("author_ru") or ""))
    model = html.escape(str(plan.get("model") or "Gemini"))

    lines = [
        "🧠 <b>SHORTS FACTORY MAX — финальный план</b>",
        "",
        f"🎬 <b>{title}</b>" + (f" — {author}" if author else ""),
        f"🤖 {model} · thinking HIGH · три независимых прохода",
    ]
    if translation_required:
        lines.extend(
            [
                "🎙 Озвучка фрагментов: только Яндекс LiveDub «Живые голоса».",
                "🛡 Таймкоды ниже уже прошли проверку границ по фактической русской речи.",
            ]
        )

    if shorts:
        lines.extend(["", "⚡ <b>SHORTS HIGHLIGHTS · 35 сек — 3 мин</b>"])
        for index, item in enumerate(shorts, 1):
            lines.append(
                f"{index}. <b>{html.escape(str(item['title']))}</b> "
                f"<code>{html.escape(str(item['start']))}–{html.escape(str(item['end']))}</code>"
            )
            hook = str(item.get("hook") or "").strip()
            if hook:
                lines.append(f"   {html.escape(hook)}")

    if longs:
        lines.extend(["", "🎞 <b>LONG HIGHLIGHTS · 5–15 минут</b>"])
        for index, item in enumerate(longs, 1):
            lines.append(
                f"{index}. <b>{html.escape(str(item['title']))}</b> "
                f"<code>{html.escape(str(item['start']))}–{html.escape(str(item['end']))}</code>"
            )

    lines.extend(
        [
            "",
            "✂️ Сейчас бот автоматически вырежет выбранные фрагменты.",
            "Короткие ролики получат вшитые субтитры; длинные будут без них.",
        ]
    )
    return "\n".join(lines)[:4000]


async def process_shorts_factory(
    url,
    update,
    status_msg=None,
    progress_prefix="",
    context=None,
    silent_errors: bool = False,
):
    """Analyze only for extraction, then render Shorts and 5–15 minute clips."""
    del context, progress_prefix
    url = get_youtube_video_url(url)
    mp3_path: Path | None = None
    persistent_source_path: Path | None = None
    keep_source_for_trim = False
    workdir = Path(tempfile.mkdtemp(prefix="shorts_factory_"))
    source_task: asyncio.Task | None = None

    try:
        _cleanup_expired_factory_sources()
        if status_msg is None:
            status_msg = await update.message.reply_text(
                "🧠 SHORTS FACTORY MAX: получаю метаданные…"
            )
        else:
            await _safe_status(status_msg, "🧠 SHORTS FACTORY MAX: получаю метаданные…")

        info = await _load_video_info(url)
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise RuntimeError("Live-трансляцию можно нарезать только после завершения")

        duration = int(float(info.get("duration") or 0))
        if duration <= 0:
            raise RuntimeError("Не удалось определить длительность видео")
        max_duration = int(os.getenv("SHORTS_FACTORY_MAX_SOURCE_SEC", "10800") or "10800")
        if duration > max_duration:
            raise RuntimeError(
                f"Источник {duration // 60} мин превышает лимит режима {max_duration // 60} мин"
            )

        media_id = _media_id(info, url)
        full_title = str(info.get("title") or "Видео").strip()
        channel_name = str(info.get("channel") or info.get("uploader") or "").strip()
        performer, title = parse_title(full_title, channel_name)
        source_language = str(info.get("language") or "").strip().lower()
        translation_required = _source_needs_translation(info)

        if translation_required:
            source_task = asyncio.create_task(
                _prepare_translation_video(url, workdir, duration, source_language),
                name=f"shorts-factory-yandex-{media_id}",
            )
        else:
            source_task = asyncio.create_task(
                download_video_for_shorts(url, media_id, workdir=workdir),
                name=f"shorts-factory-source-{media_id}",
            )

        await _safe_status(
            status_msg,
            "🎧 SHORTS FACTORY MAX: скачиваю аудио без потерь для точного анализа…",
        )
        mp3_path = await _download_factory_audio(url, media_id)

        await _safe_status(
            status_msg,
            "🧠 Gemini MAX слушает весь материал: отбор, редактура и проверка границ…",
        )
        plan = await create_factory_plan(
            mp3_path,
            title=title or full_title,
            performer=performer or channel_name,
            duration=duration,
            source_language=source_language,
        )

        await _safe_status(
            status_msg,
            "🎙 План готов. Подготавливаю единый источник для всех вырезок…",
        )
        source_timeout = _factory_source_timeout_seconds()
        try:
            source_video_path = await asyncio.wait_for(source_task, timeout=source_timeout)
        except asyncio.TimeoutError as exc:
            source_task.cancel()
            if translation_required:
                raise RuntimeError(
                    f"Яндекс LiveDub не завершился за {source_timeout // 60} мин. "
                    "Нейроперевод намеренно не используется."
                ) from exc
            raise RuntimeError(
                f"Исходное видео не скачалось за {source_timeout // 60} мин."
            ) from exc
        except Exception as exc:
            if translation_required:
                raise RuntimeError(
                    "Яндекс LiveDub «Живые голоса» недоступен для этого источника. "
                    "Нарезка иностранного оригинала и собственный нейроперевод намеренно не выполняются. "
                    f"Причина: {str(exc)[:240]}"
                ) from exc
            raise

        if not source_video_path or not Path(source_video_path).exists():
            raise RuntimeError("Не удалось получить общий видеоисточник для Factory")
        persistent_source_path = _persist_factory_source(Path(source_video_path), media_id)
        render_source_duration = await _validated_source_duration(
            persistent_source_path,
            expected_duration=duration,
        )

        shorts_candidates = plan.get("shorts_candidates") or []
        long_candidates = plan.get("long_candidates") or []
        render_shorts = shorts_candidates
        render_longs = long_candidates
        if translation_required:
            from services.shorts_factory_timing import (
                factory_ru_boundary_context,
                prepare_factory_ru_boundary_evidence,
            )

            await _safe_status(
                status_msg,
                "🛡 Проверяю границы по фактической русской LiveDub-речи…",
            )
            ru_boundary_evidence = await prepare_factory_ru_boundary_evidence(
                url=url,
                workdir=workdir,
                source_language=source_language,
            )
            with factory_ru_boundary_context(ru_boundary_evidence):
                render_shorts = _shift_candidates_for_livedub(
                    shorts_candidates,
                    source_duration=render_source_duration,
                    candidate_kind="short",
                )
                render_longs = _shift_candidates_for_livedub(
                    long_candidates,
                    source_duration=render_source_duration,
                    candidate_kind="long",
                )
            if not render_shorts and not render_longs:
                raise RuntimeError(
                    "Ни один выбранный фрагмент не прошёл доказанную проверку "
                    "границ русской LiveDub-речи; публикация оригинальных таймкодов запрещена"
                )

        render_plan = dict(plan)
        render_plan["shorts_candidates"] = render_shorts
        render_plan["long_candidates"] = render_longs
        ai_data = factory_ai_data(
            render_plan,
            title=title or full_title,
            performer=performer or channel_name,
        )
        if not silent_errors:
            await update.message.reply_text(
                _plan_message(render_plan, translation_required=translation_required),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await _safe_status(
            status_msg,
            "✂️ Рендерю SHORTS HIGHLIGHTS и длинные фрагменты…",
        )

        with factory_render_context(render_shorts, render_longs):
            if render_shorts:
                await process_and_send_shorts(
                    url=url,
                    media_id=media_id,
                    mp3_path=mp3_path,
                    title=title or full_title,
                    performer=performer or channel_name,
                    duration=render_source_duration,
                    ai_data=ai_data,
                    update=update,
                    workdir=workdir,
                    livedub_video_path=persistent_source_path,
                )
                keep_source_for_trim = True
            if render_longs:
                await process_and_send_clips(
                    url=url,
                    media_id=media_id,
                    mp3_path=mp3_path,
                    title=title or full_title,
                    performer=performer or channel_name,
                    duration=render_source_duration,
                    ai_data=ai_data,
                    update=update,
                    livedub_video_path=persistent_source_path,
                )

        shorts_sent, longs_sent = factory_completed_delivery_counts()
        plan_meta = render_plan.get("metadata") or {}
        full_video_sent = await send_factory_full_translation_if_enabled(
            update,
            persistent_source_path,
            title=str(plan_meta.get("title_ru") or title or full_title),
            duration=render_source_duration,
            translation_required=translation_required,
            silent_errors=silent_errors,
        )

        if translation_required and factory_editorial_pack_enabled():
            await _safe_status(
                status_msg,
                "🔎 Ролики готовы. Собираю полный редакционный пакет: original SRT ↔ Russian Whisper…",
            )
            try:
                pack_path, review_path, markdown_path = await prepare_factory_editorial_review(
                    url=url,
                    media_id=media_id,
                    title=title or full_title,
                    performer=performer or channel_name,
                    duration=render_source_duration,
                    source_language=source_language,
                    translated_video_path=persistent_source_path,
                    shorts_candidates=render_shorts,
                    long_candidates=render_longs,
                    ai_data=ai_data,
                )
                keep_source_for_trim = True
                if not silent_errors:
                    await send_factory_editorial_files(
                        update,
                        pack_path=pack_path,
                        review_path=review_path,
                        markdown_path=markdown_path,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as editorial_exc:
                logger.exception(
                    "Factory editorial review pack failed media_id=%s: %s",
                    media_id,
                    editorial_exc,
                )
                if not silent_errors:
                    await update.message.reply_text(
                        "⚠️ Ролики готовы, но редакционный ZIP не собрался. "
                        f"Публикационные файлы не потеряны. Причина: {str(editorial_exc)[:300]}"
                    )

        await _safe_status(
            status_msg,
            f"✅ SHORTS FACTORY MAX завершён: {shorts_sent} Shorts, "
            f"{longs_sent} длинных фрагмента.",
        )
        logger.info(
            "Shorts Factory MAX done media_id=%s original=%ss source=%ss "
            "delivered_shorts=%d aligned_shorts=%d/%d "
            "delivered_longs=%d aligned_longs=%d/%d yandex=%s full_video=%s",
            media_id,
            duration,
            render_source_duration,
            shorts_sent,
            len(render_shorts),
            len(shorts_candidates),
            longs_sent,
            len(render_longs),
            len(long_candidates),
            translation_required,
            full_video_sent,
        )
        return bool(shorts_sent or longs_sent or full_video_sent)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Shorts Factory MAX failed: %s", exc)
        if not silent_errors:
            message = f"❌ SHORTS FACTORY MAX: {str(exc)[:500]}"
            if status_msg:
                await _safe_status(status_msg, message)
            else:
                await update.message.reply_text(message)
        return False
    finally:
        if source_task is not None and not source_task.done():
            source_task.cancel()
            try:
                await source_task
            except BaseException:
                pass
        if mp3_path is not None:
            try:
                mp3_path.unlink(missing_ok=True)
            except OSError:
                pass
        if persistent_source_path is not None and not keep_source_for_trim:
            try:
                persistent_source_path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)
