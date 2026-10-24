#!/usr/bin/env python3
"""Стадия подписи (`pipelines/caption_stage.py`) — поведение, а не строки.

Алгоритм обрезки подписи до 1024 видимых символов раньше существовал двумя
копиями (обычный путь и кэш-хит), и копии разошлись: в кэш-хите футер
собирался в обратном порядке и без заголовков секций. Здесь проверяется
единый контракт: порядок шагов обрезки, содержимое футера и метрики, которые
уходят в архив (`caption_trim_stage`, `caption_timestamps_shown`).
"""

from __future__ import annotations

from converters.md_telegraph import visible_length
from pipelines import caption_stage
from pipelines.caption_stage import (
    MAX_CAPTION_VISIBLE,
    MAX_FULL_VISIBLE,
    MINI_TIMESTAMP_LIMITS,
    CaptionSource,
    count_timestamps,
    fit_caption,
)

TIMESTAMPS = "\n".join(f"{i}:00 — тезис номер {i} с довольно длинным пояснением" for i in range(1, 25))


def _source(**overrides):
    values = dict(
        performer="Джон МакАртур",
        title="Достаточность Писания",
        duration=3400,
        file_size_mb=48.5,
        bitrate="128",
        url="https://youtube.com/watch?v=abc",
        telegraph_url="https://telegra.ph/Synopsis-01-01",
        rutube_url="",
        vk_url="",
        quotes_link="https://telegra.ph/Quotes-01-01",
        questions_link="https://telegra.ph/Questions-01-01",
        terms_link="https://telegra.ph/Terms-01-01",
        format_name="sermon",
    )
    values.update(overrides)
    return CaptionSource(**values)


def _ai(**overrides):
    values = {
        "real_author": "Джон МакАртур",
        "real_title": "Достаточность Писания",
        "main_topic": "Тема: Писание достаточно для веры и жизни. " * 12,
        "hashtags": "#Писание #Реформация #Проповедь #Достаточность #Библия",
        "timestamps": TIMESTAMPS,
        "format": "sermon",
    }
    values.update(overrides)
    return values


def test_short_caption_is_not_touched_and_reports_no_trimming():
    ai = _ai(timestamps="0:00 — вступление", hashtags="", main_topic="Короткая тема")
    fit = fit_caption(_source(), ai)
    assert fit.timestamps_total == 1
    assert fit.timestamps_shown == 1
    assert fit.trim_stage == ""
    assert visible_length(fit.caption) <= MAX_CAPTION_VISIBLE


def test_ai_data_is_not_mutated():
    ai = _ai()
    before = {key: value for key, value in ai.items()}
    fit_caption(_source(), ai)
    assert ai == before
    assert "caption_trim_stage" not in ai


def test_enriched_data_carries_archive_metrics():
    fit = fit_caption(_source(), _ai())
    assert fit.data is not None
    assert fit.data["caption_timestamps_total"] == fit.timestamps_total
    assert fit.data["caption_timestamps_shown"] == fit.timestamps_shown
    assert fit.data["caption_trim_stage"] == fit.trim_stage


def test_timestamps_are_never_dropped_completely_while_a_mini_set_fits():
    """Главный инвариант шага 4: навигация сохраняется, пока влезает 3 якоря."""
    fit = fit_caption(_source(), _ai())
    assert fit.timestamps_shown >= MINI_TIMESTAMP_LIMITS[-1]
    assert fit.trim_stage == "timestamps_trimmed"


def test_trim_stage_is_removed_only_when_no_timestamps_fit(monkeypatch):
    # Бюджет сжимаем искусственно: на реальных 1024 мини-набор обычно влезает,
    # а проверить этот шаг нужно детерминированно.
    monkeypatch.setattr(caption_stage, "MAX_CAPTION_VISIBLE", 280)
    fit = fit_caption(_source(), _ai())
    assert fit.timestamps_shown == 0
    assert fit.trim_stage == "timestamps_removed"
    assert visible_length(fit.caption) <= 280


def test_last_resort_keeps_header_and_links_when_nothing_else_fits(monkeypatch):
    """Последний резерв: шапка (4 строки) + ссылки вместо обрезанного HTML."""
    monkeypatch.setattr(caption_stage, "MAX_CAPTION_VISIBLE", 200)
    fit = fit_caption(_source(), _ai())
    assert visible_length(fit.caption) <= 200
    # Разбор идёт первым: он и должен уцелеть при обрезке.
    assert "Читать Подробный Разбор" in fit.caption
    assert fit.timestamps_shown == 0


def test_footer_order_is_telegraph_first_with_section_headers():
    """Кэш-хит раньше собирал футер наоборот и без заголовков — это была регрессия."""
    footer = _source().footer()
    assert "<b>📂 Читать Подробный Разбор:</b>" in footer
    assert "<b>Смотреть Видео:</b>" in footer
    assert footer.index("Читать Подробный Разбор") < footer.index("Смотреть Видео")


def test_footer_without_telegraph_keeps_only_platform_block():
    footer = _source(telegraph_url="", quotes_link="", questions_link="", terms_link="").footer()
    assert "Смотреть Видео" in footer
    assert "Читать Подробный Разбор" not in footer


def test_full_caption_is_a_separate_long_text_and_keeps_all_timestamps():
    fit = fit_caption(_source(), _ai())
    assert count_timestamps(TIMESTAMPS) == 24
    # Полный текст не участвует в обрезке до 1024 и собирается из входа как есть.
    assert visible_length(fit.full_caption) > visible_length(fit.caption)
    assert visible_length(fit.full_caption) <= MAX_FULL_VISIBLE


def test_format_limit_applies_before_the_overflow_steps():
    """У формата может быть свой лимит таймкодов; он применяется первым."""
    fit = fit_caption(_source(format_name="qa"), _ai(format="qa"))
    assert fit.timestamps_total == 24
    assert fit.timestamps_shown <= fit.timestamps_total


def test_missing_ai_data_still_produces_a_caption():
    fit = fit_caption(_source(), None)
    assert fit.caption
    assert fit.data is None
    assert fit.timestamps_total == 0
    assert fit.trim_stage == ""


def test_count_timestamps_ignores_blank_lines():
    assert count_timestamps("0:00 — a\n\n  \n1:00 — b\n") == 2
    assert count_timestamps(None) == 0


def test_full_caption_is_trimmed_to_its_own_message_limit(monkeypatch):
    # Полный текст уходит отдельным сообщением: у него свой лимит 4096.
    monkeypatch.setattr(caption_stage, "MAX_FULL_VISIBLE", 200)
    fit = fit_caption(_source(), _ai())
    assert visible_length(fit.full_caption) <= 200


def test_hashtags_are_dropped_before_timestamps(monkeypatch):
    # 1024 -> 760: подпись перестаёт влезать, и первым уходит хэштег, а не
    # навигация — иначе терять таймкоды пришлось бы раньше времени.
    monkeypatch.setattr(caption_stage, "MAX_CAPTION_VISIBLE", 760)
    fit = fit_caption(_source(), _ai())
    assert fit.data is not None
    assert fit.data["hashtags"] == ""
    assert fit.timestamps_shown > 0


def test_format_limit_trims_long_timestamp_list_before_overflow(monkeypatch):
    # Лимит формата для "other" — 20 строк, а таймкодов 24: усечение обязано
    # произойти до шагов переполнения. Лимит подписи поднимаем, чтобы шаги
    # 1-5 заведомо не участвовали и проверялся именно шаг 0.
    monkeypatch.setattr(caption_stage, "MAX_CAPTION_VISIBLE", 5000)
    fit = fit_caption(_source(format_name="other"), _ai(format="other"))
    assert fit.data is not None
    assert fit.data["_caption_timestamps_trimmed"] is True
    assert fit.data["_caption_timestamps_total"] == 24
    assert count_timestamps(fit.data["timestamps"]) == 20


def test_trimmed_flag_mirrors_trim_stage():
    assert fit_caption(_source(), _ai()).trimmed is True
    short = fit_caption(_source(), _ai(timestamps="0:00 — вступление", hashtags="", main_topic="Тема"))
    assert short.trimmed is False


def test_footer_with_neither_telegraph_nor_platform_is_empty():
    source = _source(telegraph_url="", quotes_link="", questions_link="", terms_link="", url="")
    assert source.footer() == ""
