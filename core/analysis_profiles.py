#!/usr/bin/env python3
"""Positive complexity profiles for expanded Study/Reflection pages.

The goal is not to restrict Gemini with more bans, but to give duration-aware
creative direction: short materials should be concentrated, long materials may
open more exegetical/theological layers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpandedAnalysisProfile:
    name: str
    duration_label: str
    max_tokens: int
    target_sections: str
    target_chars: str
    source_focus: str
    original_languages: str
    translation_forks: str
    reasoning_style: str

    def prompt_block(self, page_kind: str) -> str:
        page_kind = (page_kind or "study").lower()
        if page_kind == "reflection":
            return (
                "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
                "ПРОФИЛЬ ГЛУБИНЫ ДЛЯ ЭТОГО МАТЕРИАЛА\n"
                "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n\n"
                f"Длительность: {self.duration_label}. Профиль: {self.name}.\n"
                f"Целевой объём: {self.target_sections} sections, {self.target_chars}.\n"
                "Пиши не длиннее ради длины, а глубже там, где материал сам несёт пасторскую нагрузку.\n"
                f"Пасторская логика: {self.reasoning_style}\n"
                "Главное: помоги читателю молиться, проверять сердце и видеть Христа — без искусственного драматизма.\n"
            )
        return (
            "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
            "ПРОФИЛЬ ГЛУБИНЫ ДЛЯ ЭТОГО МАТЕРИАЛА\n"
            "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n\n"
            f"Длительность: {self.duration_label}. Профиль: {self.name}.\n"
            f"Целевой объём: {self.target_sections} sections, {self.target_chars}.\n"
            f"Источники: {self.source_focus}\n"
            f"Языки оригинала: {self.original_languages}\n"
            f"Переводческие развилки: {self.translation_forks}\n"
            f"Стиль рассуждения: {self.reasoning_style}\n"
            "Это позитивный ориентир, а не механическая квота: выбирай то, что реально раскрывает материал.\n"
        )


def get_expanded_analysis_profile(duration_seconds: int | float = 0, page_kind: str = "study") -> ExpandedAnalysisProfile:
    """Return duration-aware profile for Study/Reflection generation."""
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    kind = (page_kind or "study").lower()

    if dur and dur < 20 * 60:
        if kind == "reflection":
            return ExpandedAnalysisProfile(
                name="fast",
                duration_label="короткий материал (<20 мин)",
                max_tokens=14000,
                target_sections="4–5",
                target_chars="2500–4200 символов",
                source_focus="не требуется",
                original_languages="не требуется",
                translation_forks="не требуется",
                reasoning_style="один главный духовный нерв, 2–3 точных применения, без растягивания",
            )
        return ExpandedAnalysisProfile(
            name="fast",
            duration_label="короткий материал (<20 мин)",
            max_tokens=16000,
            target_sections="4–5",
            target_chars="2500–4500 символов",
            source_focus="2–3 самых релевантных источника, только если они реально помогают",
            original_languages="0–1 ключевое слово, только если оно меняет понимание аргумента",
            translation_forks="0–1 развилка; если нет содержательного анализа — пропусти",
            reasoning_style="концентрированный разбор главного тезиса без имитации большой лекции",
        )

    if dur and dur >= 120 * 60:
        if kind == "reflection":
            return ExpandedAnalysisProfile(
                name="very_long",
                duration_label="очень длинный материал (2+ часа)",
                max_tokens=36000,
                target_sections="7–8",
                target_chars="5500–8000 символов",
                source_focus="не требуется",
                original_languages="не требуется",
                translation_forks="не требуется",
                reasoning_style="несколько пасторских слоёв с полным покрытием финала: утешение, обличение, самоиспытание, молитвенный отклик, конкретные шаги",
            )
        return ExpandedAnalysisProfile(
            name="very_long",
            duration_label="очень длинный материал (2+ часа)",
            max_tokens=40000,
            target_sections="7–9",
            target_chars="5500–9000 символов",
            source_focus="5–6 источников максимум; строго по теме, без декоративных",
            original_languages="2–4 ключевых слова/формы, каждое через роль в аргументе",
            translation_forks="1–3 развилки с реальным эффектом на смысл",
            reasoning_style="полная архитектура: текст, доктрина, историко-богословский фон, границы ошибки, покрытие финала материала",
        )

    if dur and dur >= 60 * 60:
        if kind == "reflection":
            return ExpandedAnalysisProfile(
                name="deep",
                duration_label="длинный материал (60+ мин)",
                max_tokens=28000,
                target_sections="6–7",
                target_chars="4500–7000 символов",
                source_focus="не требуется",
                original_languages="не требуется",
                translation_forks="не требуется",
                reasoning_style="несколько пасторских слоёв: утешение, обличение, самоиспытание, молитвенный отклик",
            )
        return ExpandedAnalysisProfile(
            name="deep",
            duration_label="длинный материал (60+ мин)",
            max_tokens=36000,
            target_sections="6–8",
            target_chars="4500–7500 символов",
            source_focus="4–5 источников максимум; лучше меньше, но точнее и без повторов",
            original_languages="2–3 ключевых слова/формы, каждое объясняй через роль в аргументе текста",
            translation_forks="1–3 развилки, каждая с эффектом на смысл и богословский вывод",
            reasoning_style="разверни архитектуру аргумента: текст, доктрина, исторический фон, границы ошибки",
        )

    # Default / medium / unknown duration.
    if kind == "reflection":
        return ExpandedAnalysisProfile(
            name="balanced",
            duration_label="средний материал или длительность неизвестна",
            max_tokens=22000,
            target_sections="5–6",
            target_chars="3500–6000 символов",
            source_focus="не требуется",
            original_languages="не требуется",
            translation_forks="не требуется",
            reasoning_style="ясный пасторский ход: главный нерв, смена парадигмы, диагностика, молитвенный отклик",
        )
    return ExpandedAnalysisProfile(
        name="balanced",
        duration_label="средний материал или длительность неизвестна",
        max_tokens=26000,
        target_sections="5–6",
        target_chars="3500–6500 символов",
        source_focus="3–4 источника максимум, строго по теме материала",
        original_languages="1–2 ключевых слова, не словарно, а в контексте аргумента",
        translation_forks="0–2 развилки, только с содержательным анализом",
        reasoning_style="соедини экзегезу, доктрину и контекст без энциклопедического распухания",
    )
