#!/usr/bin/env python3
"""Standalone high-quality Shorts/Highlights extraction mode."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from core.url_utils import get_youtube_video_url
from core.utils import parse_title
from pipelines.clips import process_and_send_clips
from pipelines.shorts import process_and_send_shorts
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.shorts_factory_candidates import create_factory_plan, factory_ai_data
from services.shorts_factory_runtime import factory_render_context

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


def _should_try_livedub(info: dict[str, Any]) -> bool:
    if not _env_bool("SHORTS_FACTORY_LIVEDUB", True):
        return False
    language = str(info.get("language") or "").strip().lower()
    if language.startswith(("ru", "uk", "be")):
        return False
    if language.startswith("en"):
        return True
    title = str(info.get("title") or "")
    return bool(title and not _looks_russian(title))


async def _prepare_livedub(
    url: str,
    workdir: Path,
    duration: int,
    source_language: str,
) -> Path | None:
    from services.yandex_live_dub import get_live_dub_video

    try:
        return await get_live_dub_video(
            url,
            workdir,
            duration=float(duration),
            lang=source_language,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Shorts Factory LiveDub unavailable: %s", exc)
        return None


def _plan_message(plan: dict[str, Any], *, livedub_requested: bool) -> str:
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
        f"🤖 {model} · thinking HIGH · двойная редакторская проверка",
    ]
    if livedub_requested:
        lines.append("🎙 Для англоязычного источника параллельно готовится Яндекс LiveDub.")

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
    workdir = Path(tempfile.mkdtemp(prefix="shorts_factory_"))
    livedub_task: asyncio.Task | None = None

    try:
        if status_msg is None:
            status_msg = await update.message.reply_text("🧠 SHORTS FACTORY MAX: получаю метаданные…")
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
        livedub_requested = _should_try_livedub(info)

        if livedub_requested:
            livedub_task = asyncio.create_task(
                _prepare_livedub(url, workdir, duration, source_language),
                name=f"shorts-factory-livedub-{media_id}",
            )

        await _safe_status(
            status_msg,
            "🎧 SHORTS FACTORY MAX: скачиваю аудио без потерь для точного анализа…",
        )
        mp3_path = await _download_factory_audio(url, media_id)

        await _safe_status(
            status_msg,
            "🧠 Gemini MAX слушает весь материал: глубокий поиск фрагментов и проверка границ…",
        )
        plan = await create_factory_plan(
            mp3_path,
            title=title or full_title,
            performer=performer or channel_name,
            duration=duration,
            source_language=source_language,
        )
        ai_data = factory_ai_data(plan, title=title or full_title, performer=performer or channel_name)

        if not silent_errors:
            await update.message.reply_text(
                _plan_message(plan, livedub_requested=livedub_requested),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        livedub_video_path = None
        if livedub_task is not None:
            await _safe_status(
                status_msg,
                "🎙 План готов. Жду Яндекс LiveDub, затем режу русские версии…",
            )
            livedub_timeout = int(os.getenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "1800") or "1800")
            try:
                livedub_video_path = await asyncio.wait_for(livedub_task, timeout=livedub_timeout)
            except asyncio.TimeoutError:
                livedub_task.cancel()
                logger.warning("Shorts Factory LiveDub exceeded %ss", livedub_timeout)
                livedub_video_path = None
            if livedub_video_path is None and not silent_errors:
                await update.message.reply_text(
                    "⚠️ Яндекс LiveDub для этого источника недоступен. Нарезка продолжится с оригинальной дорожкой."
                )

        await _safe_status(
            status_msg,
            "✂️ Рендерю SHORTS HIGHLIGHTS и длинные фрагменты…",
        )

        shorts_candidates = plan.get("shorts_candidates") or []
        long_candidates = plan.get("long_candidates") or []
        with factory_render_context(shorts_candidates, long_candidates):
            if shorts_candidates:
                await process_and_send_shorts(
                    url=url,
                    media_id=media_id,
                    mp3_path=mp3_path,
                    title=title or full_title,
                    performer=performer or channel_name,
                    duration=duration,
                    ai_data=ai_data,
                    update=update,
                    workdir=workdir,
                    livedub_video_path=livedub_video_path,
                )
            if long_candidates:
                await process_and_send_clips(
                    url=url,
                    media_id=media_id,
                    mp3_path=mp3_path,
                    title=title or full_title,
                    performer=performer or channel_name,
                    duration=duration,
                    ai_data=ai_data,
                    update=update,
                    livedub_video_path=livedub_video_path,
                )

        await _safe_status(
            status_msg,
            f"✅ SHORTS FACTORY MAX завершён: {len(shorts_candidates)} Shorts, "
            f"{len(long_candidates)} длинных фрагмента.",
        )
        logger.info(
            "Shorts Factory MAX done media_id=%s duration=%ss shorts=%d longs=%d livedub=%s",
            media_id,
            duration,
            len(shorts_candidates),
            len(long_candidates),
            bool(livedub_video_path),
        )
        return bool(shorts_candidates or long_candidates)

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
        if livedub_task is not None and not livedub_task.done():
            livedub_task.cancel()
            try:
                await livedub_task
            except (asyncio.CancelledError, Exception):
                pass
        if mp3_path is not None:
            try:
                mp3_path.unlink(missing_ok=True)
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)
