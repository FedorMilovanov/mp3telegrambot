"""Run ENG Full semantic QA and size-aware autofix before video delivery."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FullQaResult = tuple[Path, dict[str, Any] | None, str]


async def run_livedub_full_qa(
    *,
    video_path: Path,
    workdir: Path | None,
    download_dir: Path,
    media_id: str,
    title_line: str,
    duration: int,
    autofix: bool,
    audio_part: Any,
    client: Any,
    bot: Any,
    chat_id: Any,
    reply_to: Any,
    get_cached: Callable[[str], Awaitable[Any]],
    validate_cache: Callable[[Any], tuple[bool, str]],
    max_file_size_mb: Callable[[], int],
) -> FullQaResult:
    """Return ``(video_path, qa_result, autofix_note)`` for ENG Full."""
    qa_result = None
    autofix_note = ""
    status = None
    with contextlib.suppress(Exception):
        status = await bot.send_message(
            chat_id=chat_id,
            text=("🔍 Проверяю точность перевода перед отправкой (Gemini сравнивает дубляж с оригиналом)…"),
            reply_to_message_id=reply_to,
        )

    try:
        from services.livedub_qa import run_translation_qa

        original_audio = None
        mp3 = download_dir / f"{media_id}.mp3"
        if mp3.exists():
            original_audio = mp3
        else:
            mp3_64 = download_dir / f"{media_id}_64.mp3"
            if mp3_64.exists():
                original_audio = mp3_64

        qa_ai_data = None
        try:
            cached = await get_cached(media_id)
            cache_ok, cache_reason = validate_cache(cached)
            if cache_ok:
                qa_ai_data = (cached or {}).get("ai_data")
            elif cached:
                logger.info("[LiveDubQA] stale cached analysis ignored: %s", cache_reason)
        except Exception as exc:
            logger.info("[LiveDubQA] cached analysis unavailable: %s", str(exc)[:160])

        dub_srt = None
        clean_ru = None
        try:
            if workdir and workdir.exists():
                srt_candidates = sorted(
                    (path for path in workdir.glob("*.srt") if path.name != "gemini_subs.srt"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                dub_srt = srt_candidates[0] if srt_candidates else None
                try:
                    from services.livedub_mix import find_pro_tracks

                    clean_ru = find_pro_tracks(workdir)[1]
                except Exception:
                    clean_ru = None
        except Exception:
            pass

        qa_part = None
        qa_client = None
        if audio_part is not None and hasattr(audio_part, "name"):
            qa_part = audio_part
            qa_client = client

        qa_result = await run_translation_qa(
            dub_video_path=video_path,
            original_audio_path=original_audio,
            ai_data=qa_ai_data,
            duration=duration,
            dub_srt_path=dub_srt,
            dub_audio_path=clean_ru,
            existing_audio_part=qa_part,
            existing_client=qa_client,
        )

        if qa_result and autofix and workdir is not None:
            majors = [issue for issue in (qa_result.get("issues") or []) if str(issue.get("severity")) == "major"]
            if majors:
                try:
                    from services.livedub_mix import (
                        apply_qa_audio_fixes,
                        prepare_livedub_send_path,
                    )

                    fixed = await apply_qa_audio_fixes(workdir, qa_result.get("issues") or [])
                    if fixed and fixed.exists():
                        fixed = prepare_livedub_send_path(fixed, title_line, fallback=f"{media_id}_fixed")
                        if fixed.stat().st_size / (1024 * 1024) <= max_file_size_mb():
                            video_path = fixed
                            autofix_note = (
                                f"🩹 В {len(majors)} месте(ах) с искажением "
                                "русский дубляж приглушён, оригинал выведен в полный голос."
                            )
                        else:
                            logger.info("[LiveDubQA] фикс-версия превышает лимит — шлю оригинал")
                    else:
                        autofix_note = (
                            "🩹 Найдены искажения, но авто-приглушение не удалось "
                            "(чистые дорожки не сохранились) — детали в отчёте ниже."
                        )
                except Exception as exc:
                    logger.warning(f"[LiveDubQA] авто-правка сбой: {exc}")
    except Exception as exc:
        logger.warning(f"[LiveDubQA] сбой проверки: {exc}")
    finally:
        if status is not None:
            with contextlib.suppress(Exception):
                await status.delete()

    return video_path, qa_result, autofix_note
