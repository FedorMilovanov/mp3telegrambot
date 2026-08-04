#!/usr/bin/env python3
"""Standalone maximum-quality Shorts/Highlights extraction mode."""
from __future__ import annotations

import asyncio
import copy
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


def _pad_candidates_for_livedub(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int,
    pre_roll: float,
    post_roll: float,
) -> list[dict[str, Any]]:
    """Protect delayed Yandex phrases from being cut at clip boundaries."""
    out = copy.deepcopy(candidates)
    for item in out:
        start = max(0.0, float(item.get("start_seconds", 0)) - pre_roll)
        end = min(float(source_duration), float(item.get("end_seconds", 0)) + post_roll)
        if end <= start:
            continue
        item["start_seconds"] = start
        item["end_seconds"] = end
        item["duration_seconds"] = end - start
        item["start"] = _format_seconds(start)
        item["end"] = _format_seconds(end)
    return out


def _format_seconds(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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
        f"🤖 {model} · thinking HIGH · двойная редакторская проверка",
    ]
    if translation_required:
        lines.append("🎙 Озвучка фрагментов: только Яндекс LiveDub «Живые голоса».")

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
    translation_task: asyncio.Task | None = None

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
        translation_required = _source_needs_translation(info)

        if translation_required:
            translation_task = asyncio.create_task(
                _prepare_translation_video(url, workdir, duration, source_language),
                name=f"shorts-factory-yandex-{media_id}",
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

        translated_video_path: Path | None = None
        if translation_task is not None:
            await _safe_status(
                status_msg,
                "🎙 План готов. Жду Яндекс LiveDub «Живые голоса»…",
            )
            translation_timeout = int(
                os.getenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "1800") or "1800"
            )
            try:
                translated_video_path = await asyncio.wait_for(
                    translation_task,
                    timeout=translation_timeout,
                )
            except asyncio.TimeoutError as exc:
                translation_task.cancel()
                raise RuntimeError(
                    f"Яндекс LiveDub не завершился за {translation_timeout // 60} мин. "
                    "Нейроперевод намеренно не используется."
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    "Яндекс LiveDub «Живые голоса» недоступен для этого источника. "
                    "Нарезка иностранного оригинала и собственный нейроперевод намеренно не выполняются. "
                    f"Причина: {str(exc)[:240]}"
                ) from exc

        if not silent_errors:
            await update.message.reply_text(
                _plan_message(plan, translation_required=translation_required),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await _safe_status(
            status_msg,
            "✂️ Рендерю SHORTS HIGHLIGHTS и длинные фрагменты…",
        )

        shorts_candidates = plan.get("shorts_candidates") or []
        long_candidates = plan.get("long_candidates") or []
        render_shorts = shorts_candidates
        render_longs = long_candidates
        if translated_video_path is not None:
            render_longs = _pad_candidates_for_livedub(
                long_candidates,
                source_duration=duration,
                pre_roll=float(os.getenv("SHORTS_FACTORY_LIVEDUB_PREROLL_SEC", "1.0") or "1.0"),
                post_roll=float(os.getenv("SHORTS_FACTORY_LIVEDUB_POSTROLL_SEC", "2.5") or "2.5"),
            )

        with factory_render_context(render_shorts, render_longs):
            if render_shorts:
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
                    livedub_video_path=translated_video_path,
                )
            if render_longs:
                await process_and_send_clips(
                    url=url,
                    media_id=media_id,
                    mp3_path=mp3_path,
                    title=title or full_title,
                    performer=performer or channel_name,
                    duration=duration,
                    ai_data=ai_data,
                    update=update,
                    livedub_video_path=translated_video_path,
                )

        await _safe_status(
            status_msg,
            f"✅ SHORTS FACTORY MAX завершён: {len(shorts_candidates)} Shorts, "
            f"{len(long_candidates)} длинных фрагмента.",
        )
        logger.info(
            "Shorts Factory MAX done media_id=%s duration=%ss shorts=%d longs=%d yandex=%s",
            media_id,
            duration,
            len(shorts_candidates),
            len(long_candidates),
            bool(translated_video_path),
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
        if translation_task is not None and not translation_task.done():
            translation_task.cancel()
            try:
                await translation_task
            except BaseException:
                pass
        if mp3_path is not None:
            try:
                mp3_path.unlink(missing_ok=True)
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)
