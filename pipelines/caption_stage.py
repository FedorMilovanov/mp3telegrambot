#!/usr/bin/env python3
"""Сборка подписи к mp3: сборка, обрезка до лимита Telegram и метрики.

Почему отдельный модуль: этот алгоритм существовал **двумя копиями** —
в обычном пути и в пути «кэш-хит» (`pipelines/main_pipeline.py`). Копии
разошлись: в кэш-хите футер собирался в обратном порядке и без заголовков
секций, поэтому одна и та же запись выглядела по-разному при первом прогоне
и при повторной отправке из кэша. Здесь одна реализация на оба пути.

Логика обрезки — последовательность всё более решительных шагов, потому что
подпись Telegram ограничена 1024 видимыми символами (HTML-теги не считаются):

1. убрать хэштеги;
2. убрать `main_topic`;
3. сократить таймкоды вдвое;
4. оставить мини-набор таймкодов (7 → 5 → 3), чтобы не терять навигацию;
5. последний резерв — шапка (4 строки) + ссылки, обрезанные без лома тегов.

Полный текст (отдельное сообщение, лимит 4096) собирается отдельно и не
участвует в этих шагах.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from converters.caption import build_caption, build_platform_links, build_telegraph_links
from converters.md_telegraph import (
    _trim_timestamps,
    get_caption_timestamp_limit,
    safe_trim_caption,
    visible_length,
)

#: Лимит подписи к медиа в Telegram (видимые символы).
MAX_CAPTION_VISIBLE = 1024

#: Лимит отдельного сообщения с полным текстом.
MAX_FULL_VISIBLE = 4096

#: Мини-наборы таймкодов для шага 4: первый, который влез, и выигрывает.
MINI_TIMESTAMP_LIMITS = (7, 5, 3)

#: Сколько первых строк подписи считается «шапкой» в последнем резерве.
_HEADER_LINES = 4


def count_timestamps(value: Any) -> int:
    """Сколько непустых строк в блоке таймкодов."""
    return len([line for line in str(value or "").split("\n") if line.strip()])


@dataclass(frozen=True)
class CaptionSource:
    """Всё, что не меняется между попытками сборки подписи."""

    performer: str
    title: str
    duration: int | float
    file_size_mb: float
    bitrate: str
    url: str
    telegraph_url: str
    rutube_url: str
    vk_url: str
    quotes_link: str = ""
    questions_link: str = ""
    terms_link: str = ""
    study_link: str = ""
    reflection_link: str = ""
    format_name: str = "other"

    def build(self, data: dict[str, Any] | None, *, full_mode: bool = False) -> str:
        return build_caption(
            self.performer,
            self.title,
            self.duration,
            self.file_size_mb,
            data,
            self.bitrate,
            self.url,
            self.telegraph_url,
            self.rutube_url,
            self.vk_url,
            self.quotes_link,
            self.questions_link,
            self.terms_link,
            study_tg_url=self.study_link,
            reflection_tg_url=self.reflection_link,
            full_mode=full_mode,
        )

    def timestamps_limit(self) -> int:
        return get_caption_timestamp_limit(self.format_name)

    def footer(self) -> str:
        """Футер последнего резерва: сначала разбор, потом видео.

        Порядок и заголовки секций — часть контракта: ``build_telegraph_links``
        и ``build_platform_links`` возвращают только ссылки, а человека нужно
        вести от разбора к видео, а не наоборот.
        """
        parts: list[str] = []
        telegraph_block = build_telegraph_links(
            self.telegraph_url or "",
            self.quotes_link,
            self.questions_link,
            self.terms_link,
            study_tg_url=self.study_link,
            reflection_tg_url=self.reflection_link,
        )
        if telegraph_block:
            parts.extend(("<b>📂 Читать Подробный Разбор:</b>", telegraph_block))
        platform_block = build_platform_links(self.url, self.rutube_url, self.vk_url)
        if platform_block:
            parts.extend(("<b>Смотреть Видео:</b>", platform_block))
        return "\n".join(parts)


@dataclass(frozen=True)
class CaptionFit:
    """Результат подгонки: обе подписи, метрики и обогащённые данные."""

    caption: str
    full_caption: str
    timestamps_total: int
    timestamps_shown: int
    trim_stage: str
    data: dict[str, Any] | None

    @property
    def trimmed(self) -> bool:
        return bool(self.trim_stage)


def _trim_stage(total: int, shown: int) -> str:
    if total > shown:
        return "timestamps_trimmed" if shown else "timestamps_removed"
    return ""


def fit_caption(source: CaptionSource, ai_data: dict[str, Any] | None) -> CaptionFit:
    """Собрать подпись и подогнать её под лимит Telegram.

    ``ai_data`` не мутируется: наружу уходит копия с полями
    ``caption_trim_stage`` / ``caption_timestamps_total`` /
    ``caption_timestamps_shown``, которые читает архив и ``/pdf``.
    """
    limit = source.timestamps_limit()
    total = count_timestamps((ai_data or {}).get("timestamps"))
    # base — вход как есть: полный текст собирается из него, а не из копии,
    # которую ниже урезает format-лимит таймкодов.
    base = ai_data
    data = dict(ai_data) if ai_data else None

    # Шаг 0: применяем format-лимит к таймкодам до проверки переполнения.
    if data and data.get("timestamps"):
        limited = _trim_timestamps(data["timestamps"], limit)
        if limited != data["timestamps"]:
            data = {
                **data,
                "timestamps": limited,
                "_caption_timestamps_trimmed": True,
                "_caption_timestamps_total": total,
                "_caption_timestamps_shown": min(total, limit),
            }

    caption = source.build(data)
    full_caption = source.build(base, full_mode=True) if base else caption
    if visible_length(full_caption) > MAX_FULL_VISIBLE:
        full_caption = safe_trim_caption(full_caption, MAX_FULL_VISIBLE)

    # Таймкоды, реально попавшие в подпись: от них считается trim_stage.
    shown_timestamps = str((data or {}).get("timestamps") or "")

    if visible_length(caption) > MAX_CAPTION_VISIBLE and data and data.get("hashtags"):
        data = {**data, "hashtags": ""}
        caption = source.build(data)

    if visible_length(caption) > MAX_CAPTION_VISIBLE and data and data.get("main_topic"):
        data = {**data, "hashtags": "", "main_topic": ""}
        caption = source.build(data)

    if visible_length(caption) > MAX_CAPTION_VISIBLE and data and data.get("timestamps"):
        half = _trim_timestamps(data["timestamps"], max(limit // 2, 3))
        data = {**data, "hashtags": "", "timestamps": half}
        caption = source.build(data)
        shown_timestamps = half

    if visible_length(caption) > MAX_CAPTION_VISIBLE and data and data.get("timestamps"):
        fitted = False
        for mini_limit in MINI_TIMESTAMP_LIMITS:
            mini = _trim_timestamps(data["timestamps"], min(mini_limit, limit))
            candidate = source.build(
                {
                    **data,
                    "hashtags": "",
                    "main_topic": "",
                    "timestamps": mini,
                    "_caption_timestamps_trimmed": True,
                }
            )
            if visible_length(candidate) <= MAX_CAPTION_VISIBLE:
                caption = candidate
                shown_timestamps = mini
                fitted = True
                break
        if not fitted:
            # Даже три таймкода не влезают: подпись остаётся без навигации,
            # но с шапкой и ссылками — это лучше, чем обрезанный HTML.
            caption = source.build(
                {
                    **data,
                    "hashtags": "",
                    "main_topic": "",
                    "timestamps": "",
                    "_caption_timestamps_trimmed": True,
                }
            )
            shown_timestamps = ""

    if visible_length(caption) > MAX_CAPTION_VISIBLE:
        footer = source.footer()
        header = "\n".join(caption.split("\n")[:_HEADER_LINES])
        caption = header + (f"\n\n{footer}" if footer else "")
        caption = safe_trim_caption(caption, MAX_CAPTION_VISIBLE)
        shown_timestamps = ""

    shown = count_timestamps(shown_timestamps)
    stage = _trim_stage(total, shown)
    if data:
        data = {
            **data,
            "caption_trim_stage": stage,
            "caption_timestamps_total": total,
            "caption_timestamps_shown": shown,
        }

    return CaptionFit(
        caption=caption,
        full_caption=full_caption,
        timestamps_total=total,
        timestamps_shown=shown,
        trim_stage=stage,
        data=data,
    )


__all__ = [
    "MAX_CAPTION_VISIBLE",
    "MAX_FULL_VISIBLE",
    "MINI_TIMESTAMP_LIMITS",
    "CaptionFit",
    "CaptionSource",
    "count_timestamps",
    "fit_caption",
]
