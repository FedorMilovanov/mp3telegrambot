#!/usr/bin/env python3
"""Какие страницы публиковать: план выпуска, отдельно от публикации.

Раньше это решение принималось пятью флагами, разбросанными по телу
`process_single_video`: часть — из настроек, часть — из «а есть ли вообще
результат анализа», и ещё три взаимных исключения (Study Analysis заменяет
Аналитику, Reflection заменяет Вопросы, обе новые страницы заменяют Термины).
Проверить такую матрицу было нечем, хотя цена ошибки — публикация страницы с
выдуманным содержимым или, наоборот, потеря целого раздела.

Здесь только решение. Никакого I/O: вход — настройки и данные анализа, выход —
что запускать. Поэтому все 32 комбинации флагов проверяются в тестах.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Ключи настроек в порядке, в котором они показываются пользователю.
SETTING_KEYS = (
    "analytics",
    "questions",
    "terms",
    "study_analysis",
    "reflection_application",
)

#: Поля `terms_data`, из которых складывается «есть ли термины вообще».
TERMS_FIELDS = ("concepts", "scripture", "translations", "lexicon_notes")


@dataclass(frozen=True)
class FeatureFlags:
    """Что пользователь включил в настройках."""

    analytics: bool
    questions: bool
    terms: bool
    study_analysis: bool
    reflection_application: bool


@dataclass(frozen=True)
class PagePlan:
    """Что реально будет опубликовано с учётом взаимных исключений."""

    analytics: bool
    questions: bool
    terms: bool
    study_analysis: bool
    reflection_application: bool

    @property
    def any_page(self) -> bool:
        return any(
            (
                self.analytics,
                self.questions,
                self.terms,
                self.study_analysis,
                self.reflection_application,
            )
        )

    def enabled_names(self) -> tuple[str, ...]:
        """Имена включённых страниц — для лога одной строкой."""
        names = []
        for name in (
            "analytics",
            "questions",
            "terms",
            "study_analysis",
            "reflection_application",
        ):
            if getattr(self, name):
                names.append(name)
        return tuple(names)


def terms_total(terms_data: Mapping[str, Any] | None) -> int:
    """Сколько всего терминов/стихов/переводов пришло от анализа."""
    data = terms_data or {}
    total = 0
    for key in TERMS_FIELDS:
        value = data.get(key) or []
        total += len(value) if hasattr(value, "__len__") else 0
    return total


def has_ai_analysis(ai_data: Mapping[str, Any] | None) -> bool:
    """Есть ли из чего строить AI-страницы.

    Пустой/битый ответ Gemini выглядит как успех (``ai_data`` — словарь), но
    публиковать по нему нечего. Раньше страницы в этом случае всё равно
    пытались создавать — с выдуманным содержимым.
    """
    if not ai_data:
        return False
    if ai_data.get("real_title") or ai_data.get("main_topic") or ai_data.get("timestamps"):
        return True
    if terms_total(ai_data.get("terms_data")):
        return True
    return bool(ai_data.get("questions"))


def feature_flags(settings: Mapping[str, Any] | None, *, analysis_available: bool) -> FeatureFlags:
    """Флаги из настроек, погашенные, если анализа нет.

    Настройки по умолчанию включены: отсутствие ключа означает «пользователь
    ничего не выключал», а не «выключено».
    """
    settings = settings or {}
    if not analysis_available:
        return FeatureFlags(False, False, False, False, False)
    return FeatureFlags(
        analytics=bool(settings.get("analytics", True)),
        questions=bool(settings.get("questions", True)),
        terms=bool(settings.get("terms", True)),
        study_analysis=bool(settings.get("study_analysis", True)),
        reflection_application=bool(settings.get("reflection_application", True)),
    )


def page_plan(flags: FeatureFlags) -> PagePlan:
    """Применить взаимные исключения к включённым флагам.

    * Study Analysis заменяет legacy Аналитику;
    * Reflection/Application заменяет legacy Вопросы;
    * Термины остаются только если хотя бы одна из новых страниц выключена —
      иначе они дублируются в их разделах.
    """
    return PagePlan(
        analytics=flags.analytics and not flags.study_analysis,
        questions=flags.questions and not flags.reflection_application,
        terms=flags.terms and not (flags.study_analysis and flags.reflection_application),
        study_analysis=flags.study_analysis,
        reflection_application=flags.reflection_application,
    )


def build_plan(
    settings: Mapping[str, Any] | None, *, ai_data: Mapping[str, Any] | None
) -> tuple[FeatureFlags, PagePlan]:
    """(флаги, план) одной функцией — чтобы их не собирали по частям."""
    flags = feature_flags(settings, analysis_available=has_ai_analysis(ai_data))
    return flags, page_plan(flags)


__all__ = [
    "FeatureFlags",
    "PagePlan",
    "SETTING_KEYS",
    "TERMS_FIELDS",
    "build_plan",
    "feature_flags",
    "has_ai_analysis",
    "page_plan",
    "terms_total",
]
