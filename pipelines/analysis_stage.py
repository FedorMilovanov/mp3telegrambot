#!/usr/bin/env python3
"""Стадия анализа: Gemini, конспект и честная деградация.

Раньше это был один блок внутри `process_single_video` — тот, где решается,
что вообще известно о записи. Теперь он здесь целиком, с явными границами:

1. прогресс «слушаю запись» (шаг 4) и вызов анализа;
2. `title_topic_warning` → подмена заголовка на безопасный, **до** публикации,
   иначе пользователь видит заголовок, который не совпадает с содержимым;
3. поиск альтернативных ссылок (RuTube/VK) — **до** конспекта: таймкоды этих
   платформ должны попасть на страницу с первой публикации, а не при правке;
4. конспект (если пользователь не выключил его в настройках);
5. если анализа нет — деградация из `pipelines.degraded_mode`,
   а не «полупустая» публикация.

Порядок шагов — это и есть поведение, поэтому он проверяется тестами на
фейковых зависимостях, а не глазами в 2900-строчной функции.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.title_topic_audit import choose_safe_public_title
from pipelines.degraded_mode import attach_transcript_timestamps, degraded_caption_data

logger = logging.getLogger(__name__)

#: Номер шага прогресса для анализа и для конспекта (см. `core/progress.py`).
ANALYZE_STEP = 4
SYNOPSIS_STEP = 5

#: Что вернул анализ: `(ai_data, used_client, used_audio_part)`.
AnalyzeFn = Callable[..., Awaitable[tuple[dict[str, Any] | None, str | None, Any]]]
#: Что вернул конспект: `(telegraph_url, outline)`.
SynopsisFn = Callable[..., Awaitable[tuple[str | None, list[dict[str, Any]] | None]]]
#: Что вернул поиск альтернативных ссылок: словарь с `rutube`/`vk` или None.
LinksFn = Callable[..., Awaitable[dict[str, Any] | None]]
#: Чтение пользовательской настройки.
SettingFn = Callable[[str], Awaitable[Any]]
#: Обновление сообщения прогресса.
ProgressFn = Callable[..., Awaitable[None]]
#: Заголовок для поиска по `ai_data`.
SearchTitleFn = Callable[..., str]


@dataclass(frozen=True)
class AnalysisRequest:
    """Всё, что нужно стадии; ничего не читается из замыканий."""

    mp3_path: Path
    title: str
    performer: str
    duration: int
    url: str
    full_title: str
    channel_name: str
    media_id: str
    download_dir: Path
    bitrate: str = ""
    source_lang: str = ""
    progress_prefix: str = ""
    status_msg: Any = None


@dataclass(frozen=True)
class AnalysisOutcome:
    """Результат стадии.

    `ai_data` и `degraded_caption_data` взаимоисключающие: либо есть анализ,
    либо честная деградация. Оба `None` — Gemini не вызывался вовсе.
    """

    ai_data: dict[str, Any] | None = None
    telegraph_url: str | None = None
    synopsis_outline: list[dict[str, Any]] | None = None
    used_client: str | None = None
    used_audio_part: Any = None
    degraded_caption_data: dict[str, Any] | None = None
    early_links: dict[str, Any] | None = None
    rutube_url: str = ""
    vk_url: str = ""
    safe_title_replaced: bool = False

    @property
    def caption_data(self) -> dict[str, Any] | None:
        """Данные для подписи и плана публикации: анализ или деградация."""
        return self.ai_data or self.degraded_caption_data


@dataclass(frozen=True)
class AnalysisDeps:
    """Внешние границы стадии. В тестах — фейки, в бою — `production_deps()`."""

    analyze: AnalyzeFn
    create_synopsis: SynopsisFn
    find_links: LinksFn
    get_setting: SettingFn
    progress: ProgressFn
    build_search_title: SearchTitleFn


def production_deps() -> AnalysisDeps:
    """Реальные зависимости.

    Импорты внутри функции: `analysis_stage` не тянет за собой Gemini и
    Telegraph при импорте, а тесты не получают настоящие клиенты случайно.
    """
    from core.database import asettings_get
    from core.progress import set_progress
    from services.gemini_analyze import gemini_analyze_audio
    from services.search import _build_search_title, find_alternative_links
    from services.telegraph import create_telegraph_synopsis

    return AnalysisDeps(
        analyze=gemini_analyze_audio,
        create_synopsis=create_telegraph_synopsis,
        find_links=find_alternative_links,
        get_setting=asettings_get,
        progress=set_progress,
        build_search_title=_build_search_title,
    )


def apply_safe_title(
    ai_data: dict[str, Any],
    full_title: str,
) -> tuple[dict[str, Any], bool]:
    """Подменить заголовок на безопасный, если аудит темы его отверг.

    Возвращает **новый** словарь и признак подмены: исходный `ai_data`
    используется ниже по коду, и незаметная мутация сломала бы сравнения.
    """
    if not ai_data.get("title_topic_warning"):
        return ai_data, False
    safe_title = choose_safe_public_title(ai_data, full_title)
    if not safe_title or safe_title == ai_data.get("real_title"):
        return ai_data, False
    logger.warning(
        "Title-topic warning: using fallback title for publication: %r -> %r",
        ai_data.get("real_title"),
        safe_title,
    )
    return {**ai_data, "real_title": safe_title}, True


async def _early_links(
    request: AnalysisRequest,
    ai_data: dict[str, Any],
    deps: AnalysisDeps,
) -> dict[str, Any] | None:
    """Найти RuTube/VK **до** конспекта; сбой поиска не отменяет публикацию."""
    search_title = deps.build_search_title(ai_data, request.full_title)
    try:
        return await deps.find_links(
            search_title,
            request.channel_name,
            request.duration,
            ai_data=ai_data,
            fallback_title=request.full_title,
        )
    except Exception as err:  # поиск ссылок не критичен для публикации
        logger.warning("early alt-links error: %s", err)
        return None


async def build_degraded_data(request: AnalysisRequest) -> dict[str, Any]:
    """Данные подписи без Gemini: честная пометка + таймкоды из субтитров."""
    data = degraded_caption_data(request.title, request.performer)
    return await attach_transcript_timestamps(
        data,
        url=request.url,
        media_id=request.media_id,
        download_dir=request.download_dir,
        duration=request.duration,
        source_lang=request.source_lang,
    )


async def run_analysis(
    request: AnalysisRequest,
    deps: AnalysisDeps | None = None,
    *,
    enabled: bool = True,
) -> AnalysisOutcome:
    """Пройти стадию анализа целиком.

    `enabled=False` (нет ключей Gemini) — стадия ничего не делает и не
    показывает прогресс: обещать разбор, которого не будет, хуже, чем молчать.
    """
    if not enabled:
        return AnalysisOutcome()
    deps = deps or production_deps()

    size_mb = request.mp3_path.stat().st_size / (1024 * 1024)
    await deps.progress(
        request.status_msg,
        ANALYZE_STEP,
        {
            "title": f"📝 {request.title}",
            "author": (f"👤 {request.performer}  •  {request.duration // 60}:{request.duration % 60:02d}"),
            "size": f"🎵 {size_mb:.1f} МБ  •  {request.bitrate} kbps",
        },
        prefix=request.progress_prefix,
    )

    ai_data, used_client, used_audio_part = await deps.analyze(
        request.mp3_path,
        request.title,
        request.performer,
        request.duration,
        request.status_msg,
        request.progress_prefix,
    )

    if not ai_data:
        logger.warning(
            "Gemini analysis unavailable for %s — honest degraded mode: MP3 only, no AI pages/questions",
            request.media_id,
        )
        return AnalysisOutcome(
            used_client=used_client,
            used_audio_part=used_audio_part,
            degraded_caption_data=await build_degraded_data(request),
        )

    await deps.progress(request.status_msg, SYNOPSIS_STEP, prefix=request.progress_prefix)

    ai_data, replaced = apply_safe_title(ai_data, request.full_title)
    links = await _early_links(request, ai_data, deps)
    rutube = str((links or {}).get("rutube") or "")
    vk = str((links or {}).get("vk") or "")

    telegraph_url: str | None = None
    outline: list[dict[str, Any]] | None = None
    if await deps.get_setting("synopsis"):
        telegraph_url, outline = await deps.create_synopsis(
            request.mp3_path,
            request.title,
            request.performer,
            request.duration,
            request.url,
            existing_audio_part=used_audio_part,
            existing_client=used_client,
            ai_data=ai_data,
            rutube_url=rutube,
            vk_url=vk,
            source_lang=request.source_lang,
        )

    return AnalysisOutcome(
        ai_data=ai_data,
        telegraph_url=telegraph_url,
        synopsis_outline=outline,
        used_client=used_client,
        used_audio_part=used_audio_part,
        early_links=links,
        rutube_url=rutube,
        vk_url=vk,
        safe_title_replaced=replaced,
    )


__all__ = [
    "ANALYZE_STEP",
    "SYNOPSIS_STEP",
    "AnalysisDeps",
    "AnalysisOutcome",
    "AnalysisRequest",
    "apply_safe_title",
    "build_degraded_data",
    "production_deps",
    "run_analysis",
]
