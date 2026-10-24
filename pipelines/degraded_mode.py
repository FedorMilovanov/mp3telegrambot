#!/usr/bin/env python3
"""Деградированный режим: что отдаётся, когда Gemini недоступен.

Раньше этот блок жил внутри `process_single_video` и его поведение можно было
увидеть только при исчерпанной квоте — то есть в самый неудачный момент. При
этом он определяет, что пользователь получит вместо конспекта, и не должен
меняться незаметно:

* mp3 уходит **с честной пометкой**, что AI-разбор недоступен (иначе человек
  решит, что конспекта нет из-за него);
* таймкоды можно взять из субтитров YouTube — дёшево и без Gemini;
* любая проблема на этом пути не должна ломать отправку mp3: деградация не
  превращается в ошибку.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Пометка в caption. Текст видит пользователь, поэтому он зафиксирован тестом.
AI_UNAVAILABLE_WARNING = (
    "Gemini 3.5 сейчас недоступен или исчерпана квота. "
    "Отправляю MP3 без AI-конспектов, разбора и вопросов."
)

#: Сколько символов субтитров просить у YouTube для таймкодов.
TRANSCRIPT_MAX_CHARS = 12_000
#: Сколько строк таймкодов имеет смысл показать в caption.
TRANSCRIPT_MAX_LINES = 40


def transcript_fallback_enabled(value: str | None = None) -> bool:
    """Включать ли таймкоды из субтитров YouTube (``DEGRADED_TRANSCRIPT_TIMESTAMPS``).

    ``None`` — прочитать окружение. Всё, кроме явного «выключено», включает
    подстановку: получить таймкоды полезнее, чем не получить.
    """
    import os

    raw = os.getenv("DEGRADED_TRANSCRIPT_TIMESTAMPS", "1") if value is None else value
    return str(raw or "1").strip().lower() not in {"0", "false", "no", "off"}


def degraded_caption_data(title: str, performer: str) -> dict[str, Any]:
    """Минимальные данные для caption, когда анализа нет.

    Ключ ``_ai_unavailable_warning`` читается билдером подписи и попадает в
    текст сообщения; остальные поля — те же, что и у полного анализа, чтобы
    вызывающий код не разветвлялся на два формата.
    """
    return {
        "real_title": title,
        "real_author": performer,
        "format": "other",
        "_ai_unavailable_warning": AI_UNAVAILABLE_WARNING,
    }


TranscriptFetcher = Callable[..., Awaitable[str]]
TimestampsConverter = Callable[..., str]


async def attach_transcript_timestamps(
    data: dict[str, Any],
    *,
    url: str,
    media_id: str,
    download_dir: Path,
    duration: int,
    source_lang: str = "",
    fetch: TranscriptFetcher | None = None,
    to_timestamps: TimestampsConverter | None = None,
) -> dict[str, Any]:
    """Добавить таймкоды из субтитров YouTube, если это возможно.

    Возвращает **новый** словарь: частичный результат не должен протекать в
    вызывающий код, если что-то упало на середине. Временный каталог удаляется
    всегда — иначе деградация оставляла бы мусор в загрузках.
    """
    if not transcript_fallback_enabled():
        return data
    if fetch is None or to_timestamps is None:
        from services.youtube_transcript import (
            download_youtube_transcript_text,
            timed_text_to_caption_timestamps,
        )

        fetch = fetch or download_youtube_transcript_text
        to_timestamps = to_timestamps or timed_text_to_caption_timestamps

    work_dir = Path(download_dir) / f"{media_id}_degraded_transcript"
    try:
        timed_text = await fetch(
            url,
            work_dir,
            lang=source_lang or "ru",
            max_chars=TRANSCRIPT_MAX_CHARS,
            expected_duration=duration,
        )
        timestamps = to_timestamps(timed_text, max_lines=TRANSCRIPT_MAX_LINES)
        if not timestamps:
            return data
        lines = len([line for line in timestamps.split("\n") if line.strip()])
        logger.info(
            "Degraded mode: added %d transcript timestamp lines for %s", lines, media_id
        )
        return {**data, "timestamps": timestamps, "_timestamps_source": "youtube_transcript"}
    except Exception as error:  # сеть, отсутствие субтитров, битый ответ
        logger.info(
            "Degraded transcript timestamps unavailable for %s: %s",
            media_id,
            str(error)[:160],
        )
        return data
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


__all__ = [
    "AI_UNAVAILABLE_WARNING",
    "TRANSCRIPT_MAX_CHARS",
    "TRANSCRIPT_MAX_LINES",
    "attach_transcript_timestamps",
    "degraded_caption_data",
    "transcript_fallback_enabled",
]
