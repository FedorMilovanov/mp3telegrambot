#!/usr/bin/env python3
"""Матрица публикации: какие Telegraph-страницы выпускаются и почему.

До этого теста решение принималось пятью флагами, размазанными по
`process_single_video`, и тремя взаимными исключениями, записанными словами в
комментариях. Ошибка здесь стоит дорого в обе стороны: лишняя страница —
публикация с выдуманным содержимым, недостающая — потерянный раздел.
"""

from __future__ import annotations

import itertools

import pytest
from typing import Any

from pipelines.publication_plan import (
    TERMS_FIELDS,
    FeatureFlags,
    build_plan,
    feature_flags,
    has_ai_analysis,
    page_plan,
    terms_total,
)


# ── «есть ли анализ» ────────────────────────────────────────────────────────


@pytest.mark.parametrize("ai_data", [None, {}, {"real_title": ""}, {"questions": []}])
def test_empty_analysis_is_not_an_analysis(ai_data: dict[str, Any] | None) -> None:
    assert has_ai_analysis(ai_data) is False


@pytest.mark.parametrize(
    "ai_data",
    [
        {"real_title": "Название"},
        {"main_topic": "Тема"},
        {"timestamps": "00:00 — начало"},
        {"questions": ["Вопрос?"]},
        {"terms_data": {"concepts": [{"term": "благодать"}]}},
        {"terms_data": {"scripture": ["Ин. 3:16"]}},
    ],
)
def test_any_meaningful_field_counts(ai_data: dict[str, Any] | None) -> None:
    assert has_ai_analysis(ai_data) is True


# ── подсчёт терминов ────────────────────────────────────────────────────────


def test_terms_total_sums_all_sections() -> None:
    data = {
        "concepts": [1, 2],
        "scripture": [1],
        "translations": [1, 2, 3],
        "lexicon_notes": [],
    }
    assert terms_total(data) == 6


def test_terms_total_ignores_unknown_sections() -> None:
    assert terms_total({"concepts": [1], "что-то_ещё": [1, 2, 3]}) == 1


def test_terms_total_of_empty() -> None:
    assert terms_total(None) == 0
    assert terms_total({}) == 0
    assert terms_total({key: None for key in TERMS_FIELDS}) == 0


# ── флаги из настроек ───────────────────────────────────────────────────────


def test_missing_settings_mean_enabled() -> None:
    """Отсутствие ключа — «пользователь ничего не выключал»."""
    flags = feature_flags({}, analysis_available=True)
    assert flags == FeatureFlags(True, True, True, True, True)


def test_settings_can_disable_each_flag() -> None:
    settings = {key: False for key in ("analytics", "questions", "terms", "study_analysis", "reflection_application")}
    assert feature_flags(settings, analysis_available=True) == FeatureFlags(False, False, False, False, False)


def test_no_analysis_turns_everything_off() -> None:
    """Иначе страницы строились бы из пустого ответа — с выдуманным содержимым."""
    assert feature_flags({}, analysis_available=False) == FeatureFlags(False, False, False, False, False)


# ── взаимные исключения ─────────────────────────────────────────────────────


def test_study_analysis_replaces_legacy_analytics() -> None:
    plan = page_plan(FeatureFlags(True, False, False, True, False))
    assert plan.study_analysis is True
    assert plan.analytics is False


def test_legacy_analytics_survives_without_study() -> None:
    plan = page_plan(FeatureFlags(True, False, False, False, False))
    assert plan.analytics is True


def test_reflection_replaces_legacy_questions() -> None:
    plan = page_plan(FeatureFlags(False, True, False, False, True))
    assert plan.reflection_application is True
    assert plan.questions is False


def test_legacy_questions_survive_without_reflection() -> None:
    plan = page_plan(FeatureFlags(False, True, False, False, False))
    assert plan.questions is True


def test_terms_are_dropped_when_both_new_pages_run() -> None:
    """Термины входят в разделы новых страниц — иначе они дублируются."""
    plan = page_plan(FeatureFlags(True, True, True, True, True))
    assert plan.terms is False
    assert plan.study_analysis and plan.reflection_application


@pytest.mark.parametrize(("study", "reflection"), [(True, False), (False, True), (False, False)])
def test_terms_survive_if_at_least_one_new_page_is_off(study: bool, reflection: bool) -> None:
    plan = page_plan(FeatureFlags(False, False, True, study, reflection))
    assert plan.terms is True


# ── полная матрица (32 комбинации) ──────────────────────────────────────────


def test_all_32_flag_combinations_are_consistent() -> None:
    """Ни одна комбинация не должна давать противоречивый план."""
    checked = 0
    for combo in itertools.product([False, True], repeat=5):
        flags = FeatureFlags(*combo)
        plan = page_plan(flags)
        checked += 1

        # Ничего не включено сверх флагов.
        assert not plan.analytics or flags.analytics
        assert not plan.questions or flags.questions
        assert not plan.terms or flags.terms
        assert plan.study_analysis == flags.study_analysis
        assert plan.reflection_application == flags.reflection_application

        # Взаимные исключения соблюдены.
        assert not (plan.analytics and plan.study_analysis)
        assert not (plan.questions and plan.reflection_application)
        if flags.study_analysis and flags.reflection_application:
            assert plan.terms is False

        # any_page отражает реальность.
        assert plan.any_page == bool(plan.enabled_names())
    assert checked == 32


def test_all_disabled_means_no_pages() -> None:
    plan = page_plan(FeatureFlags(False, False, False, False, False))
    assert plan.any_page is False
    assert plan.enabled_names() == ()


def test_default_plan_publishes_new_pages_only() -> None:
    """С настройками по умолчанию и рабочим анализом дублей не публикуется."""
    _flags, plan = build_plan({}, ai_data={"real_title": "Название"})
    assert plan.enabled_names() == ("study_analysis", "reflection_application")


def test_plan_without_analysis_is_empty() -> None:
    _flags, plan = build_plan({}, ai_data=None)
    assert plan.any_page is False


# ── одна функция на всё ─────────────────────────────────────────────────────


def test_build_plan_returns_flags_and_plan() -> None:
    flags, plan = build_plan(
        {
            "analytics": True,
            "questions": False,
            "terms": True,
            "study_analysis": False,
            "reflection_application": False,
        },
        ai_data={"main_topic": "Тема"},
    )
    assert flags == FeatureFlags(True, False, True, False, False)
    assert plan.enabled_names() == ("analytics", "terms")


def test_build_plan_with_terms_only_analysis() -> None:
    """Анализ, где есть только термины, — тоже достаточное основание."""
    _flags, plan = build_plan(
        {"study_analysis": False, "reflection_application": False},
        ai_data={"terms_data": {"concepts": [{"term": "вера"}]}},
    )
    assert plan.enabled_names() == ("analytics", "questions", "terms")


def test_plan_does_not_mutate_settings() -> None:
    settings = {"analytics": True}
    before = dict(settings)
    build_plan(settings, ai_data={"real_title": "x"})
    assert settings == before
