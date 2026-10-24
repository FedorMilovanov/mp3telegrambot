#!/usr/bin/env python3
"""Регрессии, найденные при включении `mypy --strict` для `core/`.

Каждое изменение здесь — следствие ошибки типов, которая указывала на
реальный дефект или на неявный контракт. Аннотации не должны менять
поведение, поэтому всё, что могло его изменить, закрыто тестом:

* `_build_thinking_config` — строка уровня вместо enum и молчаливый
  фолбэк на `thinking_budget`;
* `audit_synopsis_density` — секции без времени не должны давать
  «покрытие 0 %»;
* `source_packs` — `negative` это `tuple[str, ...]`, а не кортеж кортежей;
* `_exception_status_code` — исключение без `code` не должно падать на
  `int(None)`.
"""

from __future__ import annotations

from core import globals as core_globals
from core.source_packs import _TOPIC_RULES, select_source_pack_topics
from core.synopsis_quality import audit_synopsis_density


def test_thinking_config_accepts_level_regardless_of_case():
    config = core_globals._build_thinking_config("HIGH")
    assert config is not None
    # enum SDK, а не строка: именно этого требовал тип ThinkingConfig.
    assert str(getattr(config.thinking_level, "value", config.thinking_level)).upper() == "HIGH"
    assert config.thinking_budget is None


def test_thinking_config_falls_back_to_budget_for_unknown_level():
    config = core_globals._build_thinking_config("неведомый-уровень")
    assert config is not None
    # Неизвестный уровень — это не повод отправлять запрос без бюджета:
    # раньше строка уходила в SDK как есть и падала уже на стороне API.
    assert config.thinking_level is None
    assert config.thinking_budget == 24576


def test_thinking_config_budget_map_covers_all_known_levels():
    for level, expected in (("minimal", 4096), ("low", 8192), ("medium", 16384), ("high", 24576)):
        config = core_globals._build_thinking_config(level)
        assert config is not None
        # Для известных уровней бюджет не нужен — работает enum.
        assert config.thinking_budget is None, level
        assert config.thinking_level is not None
        assert core_globals._THINKING_BUDGETS[level.upper()] == expected


def test_synopsis_density_ignores_missing_times_instead_of_reporting_zero_coverage():
    sections = [
        {"title": "Вступление", "content": "Развёрнутое вступление без таймкода. " * 40},
        {"title": "Основная часть", "content": "Основная часть без таймкода. " * 60},
    ]
    codes = {issue.code for issue in audit_synopsis_density(sections, 90 * 60)}
    # Времени секций нет — измерить покрытие нельзя, поэтому и вердикта нет.
    assert "synopsis_time_coverage_low" not in codes


def test_source_pack_negative_keywords_are_plain_strings():
    for topic, rule in _TOPIC_RULES.items():
        assert all(isinstance(word, str) for word in rule.negative), topic


def test_source_pack_negative_keyword_lowers_score():
    topics = dict(select_source_pack_topics(["церковь", "членство"], "Церковь и членство"))
    topics_with_noise = dict(select_source_pack_topics(["церковь", "членство"], "Церковь, членство и молитва"))
    assert topics.get("ecclesiology", 0) > topics_with_noise.get("ecclesiology", 0)


def test_quota_domain_status_code_survives_exception_without_code():
    from services.gemini_quota_domains import _exception_status_code

    class _Bare(Exception):
        pass

    assert _exception_status_code(_Bare("nothing here")) is None

    class _WithCode(Exception):
        code = 429

    assert _exception_status_code(_WithCode("quota")) == 429

    class _Noise(Exception):
        code = "not-a-number"

    assert _exception_status_code(_Noise("weird")) is None
