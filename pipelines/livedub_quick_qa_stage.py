"""Run lightweight LiveDub QA and optional audio repair before delivery."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

QuickQaResult = tuple[Path, str, str]


def _path_exists(path: Path) -> bool:
    return path.exists()


def _discover_quick_qa_paths(workdir: Path) -> tuple[Path | None, Path | None]:
    srt_candidates = sorted(
        (path for path in workdir.glob("*.srt") if path.name != "gemini_subs.srt"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    original_candidates = sorted(
        workdir.glob("original_video.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    quick_srt = srt_candidates[0] if srt_candidates else None
    original = original_candidates[0] if original_candidates else None
    return quick_srt, original


async def run_livedub_quick_qa(
    *,
    video_path: Path,
    workdir: Path | None,
    duration: int,
    video_url: str,
    bot: Any,
    chat_id: Any,
    reply_to: Any,
) -> QuickQaResult:
    """Return ``(video_path, report_html, skip_note)`` for ENG Quick QA."""
    report_html = ""
    skip_note = ""
    try:
        max_duration = int(os.getenv("LIVEDUB_QUICK_QA_MAX_DURATION", "120") or "120")
    except ValueError:
        max_duration = 120

    if duration and duration <= max_duration:
        try:
            from services.livedub_info import get_light_model
            from services.livedub_qa import format_qa_report, run_translation_qa

            quick_srt = None
            original = None
            clean_ru = None
            if workdir is not None and await asyncio.to_thread(_path_exists, workdir):
                quick_srt, original = await asyncio.to_thread(_discover_quick_qa_paths, workdir)
                try:
                    from services.livedub_mix import find_pro_tracks

                    clean_ru = find_pro_tracks(workdir)[1]
                except Exception:
                    clean_ru = None

            if original and workdir is not None:
                quick_qa = await run_translation_qa(
                    dub_video_path=video_path,
                    original_audio_path=original,
                    ai_data=None,
                    duration=duration,
                    model_name=(os.getenv("LIVEDUB_QUICK_QA_MODEL", "").strip() or get_light_model()),
                    dub_srt_path=quick_srt,
                    dub_audio_path=clean_ru,
                    thinking_level=(os.getenv("LIVEDUB_QUICK_QA_THINKING", "minimal").strip() or "minimal"),
                )
                majors = [
                    issue for issue in ((quick_qa or {}).get("issues") or []) if str(issue.get("severity")) == "major"
                ]
                if majors:
                    logger.warning(
                        "[LiveDubQuickQA] major issues=%d — пробую автофикс до отправки",
                        len(majors),
                    )
                    try:
                        from services.livedub_mix import apply_qa_audio_fixes

                        fixed = await apply_qa_audio_fixes(workdir, (quick_qa or {}).get("issues") or [])
                        if fixed and fixed.exists():
                            video_path = fixed
                            await bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "⚠️ Быстрая проверка нашла серьёзное искажение перевода. "
                                    "Отправляю версию, где проблемный русский фрагмент вырезан, "
                                    "а оригинал поднят."
                                ),
                                reply_to_message_id=reply_to,
                            )
                    except Exception as exc:
                        logger.warning("[LiveDubQuickQA] автофикс не удался: %s", str(exc)[:160])
                if quick_qa:
                    report_html = format_qa_report(quick_qa, video_url=video_url)
                else:
                    skip_note = "Проверка перевода не удалась (Gemini не дал ответ)"
            else:
                logger.warning("[LiveDubQuickQA] skipped: оригинальная дорожка не сохранилась в workdir")
                skip_note = "Проверка перевода пропущена: оригинальная дорожка не сохранилась"
        except Exception as exc:
            logger.info("[LiveDubQuickQA] skip: %s", str(exc)[:160])
            skip_note = "Проверка перевода не удалась (внутренняя ошибка)"
    else:
        logger.info("[LiveDubQuickQA] skipped: duration=%s > max=%s", duration, max_duration)
        skip_note = f"Проверка перевода пропущена: ролик длиннее {max_duration} сек"

    return video_path, report_html, skip_note
