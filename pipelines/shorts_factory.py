#!/usr/bin/env python3
"""Standalone maximum-quality Shorts/Highlights extraction mode."""
from __future__ import annotations

import asyncio
import html
import json
import logging
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
from pipelines.factory_short_delivery import process_and_send_factory_shorts
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.shorts_factory_candidates import factory_ai_data
from services.shorts_factory_execution_guard import (
    enforce_factory_preflight,
    enforce_factory_translation_preflight,
    factory_language_needs_translation,
    resolve_factory_spoken_language,
)
from services.shorts_factory_full_video import send_factory_full_translation_if_enabled
from services.shorts_factory_media import validated_factory_source_duration
from services.shorts_factory_publication import enrich_factory_candidates
from services.shorts_factory_source import (
    _factory_livedub_timeout_seconds,
    create_factory_plan_from_supported_audio,
    download_factory_audio_source,
    download_factory_video_source,
    prepare_factory_translation_video,
)
from services.shorts_factory_timing import align_factory_livedub_candidates
from services.translation_editorial_factory import (
    factory_editorial_pack_enabled,
    prepare_factory_editorial_review,
    send_factory_editorial_files,
)

logger = logging.getLogger(__name__)
FACTORY_LONG_PUBLIC_MAX_SEC = 900.0

_download_factory_audio = download_factory_audio_source
download_video_for_shorts = download_factory_video_source
_prepare_translation_video = prepare_factory_translation_video
create_factory_plan = create_factory_plan_from_supported_audio
_validated_source_duration = validated_factory_source_duration
_shift_candidates_for_livedub = align_factory_livedub_candidates


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _factory_source_timeout_seconds() -> int:
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

    command = list(YTDLP_BASE_ARGS) + ["--dump-json", "--no-playlist", url]
    process = await run_cancellable_process(command, timeout=240, text=True)
    if process.returncode != 0:
        raise RuntimeError((process.stderr or "yt-dlp metadata error")[-800:])
    for line in (process.stdout or "").splitlines():
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


def _translation_backend() -> str:
    backend = os.getenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex_live").strip().lower()
    aliases = {
        "yandex": "yandex_live",
        "yandex_live": "yandex_live",
        "live": "yandex_live",
    }
    return aliases.get(backend, backend)


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
        raise RuntimeError("Не удалось сохранить общий источник для Factory")
    return destination


def _plan_message(plan: dict[str, Any], *, translation_required: bool) -> str:
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
    """Analyze speech first, then select and render the proved source route."""
    del context, progress_prefix
    url = get_youtube_video_url(url)
    mp3_path: Path | None = None
    persistent_source_path: Path | None = None
    keep_source = False
    workdir = Path(tempfile.mkdtemp(prefix="shorts_factory_"))
    source_task: asyncio.Task | None = None

    try:
        enforce_factory_preflight()
        _cleanup_expired_factory_sources()
        if status_msg is None:
            status_msg = await update.message.reply_text(
                "🧠 SHORTS FACTORY MAX: получаю метаданные…"
            )
        else:
            await _safe_status(status_msg, "🧠 SHORTS FACTORY MAX: получаю метаданные…")

        info = await _load_video_info(url)
        if info.get("is_live") or info.get("live_status") in {
            "is_live",
            "is_upcoming",
            "post_live",
        }:
            raise RuntimeError(
                "Live-трансляцию можно нарезать только после завершения обработки записи"
            )

        try:
            duration = int(float(info.get("duration") or 0))
        except (TypeError, ValueError, OverflowError):
            duration = 0
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
        metadata_language = str(info.get("language") or "").strip().lower()

        await _safe_status(
            status_msg,
            "🎧 SHORTS FACTORY MAX: готовлю компактное анализ-аудио для Gemini…",
        )
        mp3_path = await _download_factory_audio(
            url,
            media_id,
            status_msg=status_msg,
        )

        await _safe_status(
            status_msg,
            "🧠 Gemini MAX слушает весь материал: отбор, редактура и проверка границ…",
        )
        plan = await create_factory_plan(
            mp3_path,
            title=title or full_title,
            performer=performer or channel_name,
            duration=duration,
            source_language=metadata_language,
            status_msg=status_msg,
        )
        spoken_language = resolve_factory_spoken_language(plan, info)
        translation_required = factory_language_needs_translation(spoken_language)

        await _safe_status(
            status_msg,
            "🎙 План и язык речи доказаны. Подготавливаю единый источник для всех вырезок…",
        )
        if translation_required:
            enforce_factory_translation_preflight()
            source_task = asyncio.create_task(
                _prepare_translation_video(
                    url,
                    workdir,
                    duration,
                    spoken_language,
                ),
                name=f"shorts-factory-yandex-{media_id}",
            )
        else:
            source_task = asyncio.create_task(
                download_video_for_shorts(url, media_id, workdir=workdir),
                name=f"shorts-factory-source-{media_id}",
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
                    "Яндекс LiveDub «Живые голоса» недоступен или не прошёл "
                    "обязательную локальную проверку для этого источника. "
                    "Нарезка иностранного оригинала и собственный нейроперевод "
                    f"намеренно не выполняются. Причина: {str(exc)[:240]}"
                ) from exc
            raise

        if not source_video_path or not Path(source_video_path).is_file():
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
            from services.shorts_factory_timing import prepare_factory_ru_boundary_evidence

            await _safe_status(
                status_msg,
                "🛡 Проверяю границы по фактической русской LiveDub-речи…",
            )
            ru_boundary_evidence = await prepare_factory_ru_boundary_evidence(
                url=url,
                workdir=workdir,
                source_language=spoken_language,
            )
            render_shorts = _shift_candidates_for_livedub(
                shorts_candidates,
                source_duration=render_source_duration,
                evidence=ru_boundary_evidence,
                candidate_kind="short",
            )
            render_longs = _shift_candidates_for_livedub(
                long_candidates,
                source_duration=render_source_duration,
                evidence=ru_boundary_evidence,
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

        shorts_sent = 0
        longs_sent = 0
        if render_shorts:
            shorts_sent = await process_and_send_factory_shorts(
                url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title or full_title,
                performer=performer or channel_name,
                source_duration=render_source_duration,
                ai_data=ai_data,
                candidates=render_shorts,
                source_video_path=persistent_source_path,
                update=update,
            )
            if shorts_sent <= 0:
                raise RuntimeError(
                    "SHORTS FACTORY не доставил ни одного Short с обязательными субтитрами"
                )

        if render_longs:
            enriched_longs = await enrich_factory_candidates(
                render_longs,
                call_kwargs={
                    "mp3_path": mp3_path,
                    "ai_data": ai_data,
                    "title": title or full_title,
                    "performer": performer or channel_name,
                    "duration": render_source_duration,
                },
                kind="long",
            )
            longs_sent = await process_and_send_clips(
                url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title or full_title,
                performer=performer or channel_name,
                duration=render_source_duration,
                ai_data=ai_data,
                update=update,
                livedub_video_path=persistent_source_path,
                candidates_override=enriched_longs,
                public_max_seconds=FACTORY_LONG_PUBLIC_MAX_SEC,
                factory_publication=True,
                snap_to_silence=False,
            )
            if longs_sent <= 0:
                raise RuntimeError("SHORTS FACTORY не доставил ни одного длинного клипа")

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
                    source_language=spoken_language,
                    translated_video_path=persistent_source_path,
                    shorts_candidates=render_shorts,
                    long_candidates=render_longs,
                    ai_data=ai_data,
                )
                keep_source = True
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
            "delivered_longs=%d aligned_longs=%d/%d yandex=%s spoken_language=%s "
            "full_video=%s",
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
            spoken_language,
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
        if persistent_source_path is not None and not keep_source:
            try:
                persistent_source_path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["FACTORY_LONG_PUBLIC_MAX_SEC", "process_shorts_factory"]
