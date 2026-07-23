#!/usr/bin/env python3
"""Positive complexity profiles for expanded Study/Reflection pages.

Duration controls the available ceiling, never a quota. A long recording may
justify more sections and research layers, but it does not automatically justify
Greek, Hebrew, translation comparisons, or practical exercises.
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
                f"Допустимый ориентир: {self.target_sections} sections, {self.target_chars}.\n"
                "Это потолок и диапазон, а не требование заполнить объём. Сначала истина Писания, "
                "её понимание и усвоение; применение — только как реальный плод материала.\n"
                f"Пасторская логика: {self.reasoning_style}\n"
                "Помоги читателю увидеть Бога, понять истину, распознать ложь и ответить верой — "
                "без искусственного драматизма и без списка дел ради списка.\n"
            )
        return (
            "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n"
            "ПРОФИЛЬ ГЛУБИНЫ ДЛЯ ЭТОГО МАТЕРИАЛА\n"
            "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\n\n"
            f"Длительность: {self.duration_label}. Профиль: {self.name}.\n"
            f"Допустимый ориентир: {self.target_sections} sections, {self.target_chars}.\n"
            "Используй столько пространства, сколько реально нужно сильному обучающему тексту, "
            "вплоть до бюджета одной насыщенной Telegraph-страницы. Это не минимум и не квота: "
            "не раздувай слабый материал, но и не обрезай глубокий разбор из-за условного лимита слов.\n"
            f"Источники: {self.source_focus}\n"
            f"Языки оригинала: {self.original_languages}\n"
            f"Переводческие развилки: {self.translation_forks}\n"
            f"Стиль рассуждения: {self.reasoning_style}\n"
            "Все числа — верхние ориентиры. Нулевой результат для источников, оригинала или "
            "переводов является правильным, если они не меняют понимание текста.\n"
        )


def get_expanded_analysis_profile(
    duration_seconds: int | float = 0,
    page_kind: str = "study",
) -> ExpandedAnalysisProfile:
    """Return duration-aware, non-quota depth guidance."""
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
                max_tokens=22000,
                target_sections="3–5",
                target_chars="2200–4200 символов",
                source_focus="не требуется",
                original_languages="не требуется",
                translation_forks="не требуется",
                reasoning_style=(
                    "одна управляющая истина, её основание, одно ключевое исправление "
                    "мышления и 1–3 соразмерных ответа"
                ),
            )
        return ExpandedAnalysisProfile(
            name="fast",
            duration_label="короткий материал (<20 мин)",
            max_tokens=18000,
            target_sections="2–5",
            target_chars="3500–8000 символов",
            source_focus="0–3 источника; только если источник решает конкретную исследовательскую задачу",
            original_languages=(
                "0–1 ключевое слово или форма; только если контекстуальный смысл без неё заметно беднее"
            ),
            translation_forks="0–1 развилка; только при реальном влиянии на смысл или аргумент",
            reasoning_style="концентрированный обучающий разбор истины, основания, различения и следствия",
        )

    if dur and dur >= 120 * 60:
        if kind == "reflection":
            return ExpandedAnalysisProfile(
                name="very_long",
                duration_label="очень длинный материал (2+ часа)",
                max_tokens=56000,
                target_sections="5–8",
                target_chars="6000–10000 символов",
                source_focus="не требуется",
                original_languages="не требуется",
                translation_forks="не требуется",
                reasoning_style=(
                    "несколько истин и пасторских слоёв с покрытием финала; сначала "
                    "понимание и усвоение, затем обличение, утешение, молитва и ответ"
                ),
            )
        return ExpandedAnalysisProfile(
            name="very_long",
            duration_label="очень длинный материал (2+ часа)",
            max_tokens=60000,
            target_sections="5–9",
            target_chars="16000–26000 символов",
            source_focus="0–6 источников; каждый отвечает на отдельный вопрос и не дублирует соседний",
            original_languages="0–4 формы; каждая органично встроена в контекст, функцию и границы вывода",
            translation_forks="0–3 развилки; длина материала не делает их обязательными",
            reasoning_style=(
                "выстроить большую, но цельную обучающую главу: текст, контекст, доктрина, "
                "сильная альтернатива, границы вывода и связь с финалом материала"
            ),
        )

    if dur and dur >= 60 * 60:
        if kind == "reflection":
            return ExpandedAnalysisProfile(
                name="deep",
                duration_label="длинный материал (60+ мин)",
                max_tokens=46000,
                target_sections="5–7",
                target_chars="5000–8500 символов",
                source_focus="не требуется",
                original_languages="не требуется",
                translation_forks="не требуется",
                reasoning_style=(
                    "управляющие истины и их усвоение; затем сопротивление сердца, "
                    "отношения, средства благодати и соразмерный ответ"
                ),
            )
        return ExpandedAnalysisProfile(
            name="deep",
            duration_label="длинный материал (60+ мин)",
            max_tokens=52000,
            target_sections="4–8",
            target_chars="12000–22000 символов",
            source_focus="0–5 источников; лучше ни одного, чем декоративная библиография",
            original_languages=(
                "0–3 формы; не считай 2–3 ключевых слова обязательной нормой: "
                "включай их только когда они естественно усиливают конкретный абзац "
                "и доказуемо меняют чтение"
            ),
            translation_forks="0–3 развилки; сравнивать решения, а не объявлять победителя",
            reasoning_style=(
                "развернуть связную архитектуру аргумента: основание в тексте, смысл, различения, "
                "альтернативные чтения, доктринальные следствия и границы"
            ),
        )

    if kind == "reflection":
        return ExpandedAnalysisProfile(
            name="balanced",
            duration_label="средний материал или длительность неизвестна",
            max_tokens=38000,
            target_sections="4–6",
            target_chars="4000–7500 символов",
            source_focus="не требуется",
            original_languages="не требуется",
            translation_forks="не требуется",
            reasoning_style=(
                "ясный путь: истина и основание → усвоение → исправление ложной рамки → "
                "диагностика → молитва или верный ответ"
            ),
        )
    return ExpandedAnalysisProfile(
        name="balanced",
        duration_label="средний материал или длительность неизвестна",
        max_tokens=40000,
        target_sections="3–7",
        target_chars="8000–16000 символов",
        source_focus="0–4 источника, каждый с конкретной функцией для этого материала",
        original_languages="0–2 формы; не словарно, а органично внутри контекста и роли в аргументе",
        translation_forks="0–2 развилки; отсутствие полезной развилки — полноценный результат",
        reasoning_style=(
            "создать цельную обучающую главу, соединяя текст, контекст, доктрину и проверяемое "
            "следствие без энциклопедического распухания"
        ),
    )
