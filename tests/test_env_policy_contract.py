#!/usr/bin/env python3
"""Контракт для переменных окружения, которые форсирует стартовая политика.

Аудит нашёл 20+ «мёртвых ручек»: значения были описаны в ``.env.example`` как
настройки, но ``gemini_max_quality``/``livedub_quality_runtime`` перезаписывали
их безусловно, без единого сообщения. Здесь проверяется, что

* форсирование по-прежнему работает (контракт качества не отключить из .env);
* подмена значения оператора видна — WARNING в лог и ``overridden_env`` в
  стартовой диагностике;
* реестр ``POLICY_OWNED`` совпадает со списком, опубликованным в
  ``.env.example``, то есть документация не расходится с кодом;
* новые политики не начинают писать ``os.environ[...]`` напрямую, в обход
  реестра.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path

import pytest

from core.model_catalog import MODEL_PRIMARY
from services import env_policy, gemini_max_quality, livedub_quality_runtime

ENV_EXAMPLE = Path(".env.example")
OWNED_BLOCK = ("<!-- POLICY-OWNED:START -->", "<!-- POLICY-OWNED:END -->")

#: Значения, которые форсирует каждая политика (owner -> переменные).
EXPECTED_OWNED = {
    "services.gemini_max_quality": {
        "GEMINI_MODEL",
        "GEMINI_SCHEMA_THINKING",
        "LIVEDUB_INFO_MODEL",
        "LIVEDUB_INFO_FALLBACK_MODELS",
        "LIVEDUB_QUICK_QA_MODEL",
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_MODEL",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_MODEL",
        "LIVEDUB_QA_VERIFY_THINKING",
        "LIVEDUB_PUBLICATION_FALLBACK_MODELS",
        "SHORTS_FACTORY_MODEL",
        "WHISPER_MODEL",
        "SHORTS_FACTORY_WHISPER_MODEL",
    },
    "services.livedub_quality_runtime": {
        "GEMINI_MODEL",
        "GEMINI_SCHEMA_THINKING",
        "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL",
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_MODEL",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_MODEL",
        "LIVEDUB_QA_VERIFY_THINKING",
        "LIVEDUB_PUBLICATION_FALLBACK_MODELS",
        "LIVEDUB_TTS_FALLBACK",
        "LIVEDUB_INFO_FALLBACK_MODELS",
    },
}


def _documented_owned() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    start = text.index(OWNED_BLOCK[0]) + len(OWNED_BLOCK[0])
    end = text.index(OWNED_BLOCK[1])
    names = set()
    for line in text[start:end].splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            names.add(stripped)
    return names


def _apply_both_policies() -> None:
    gemini_max_quality.configure_max_quality_env()
    livedub_quality_runtime.configure_gemini_policy()


@pytest.fixture()
def clean_policy_env(monkeypatch: pytest.MonkeyPatch):
    """Снять все управляемые переменные и сбросить реестр подмен."""
    for names in EXPECTED_OWNED.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    env_policy.POLICY_OWNED.clear()
    env_policy.reset()
    yield
    env_policy.POLICY_OWNED.clear()
    env_policy.reset()


def test_policy_still_wins_over_env(clean_policy_env) -> None:
    os.environ["GEMINI_MODEL"] = "gemini-2.5-pro"
    _apply_both_policies()
    assert os.environ["GEMINI_MODEL"] == MODEL_PRIMARY
    assert os.environ["LIVEDUB_QUICK_QA_MODEL"] == MODEL_PRIMARY


def test_operator_override_is_reported_not_swallowed(clean_policy_env, caplog) -> None:
    os.environ["WHISPER_MODEL"] = "tiny"
    os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] = "gemini-2.5-flash"

    with caplog.at_level(logging.WARNING, logger="services.env_policy"):
        diagnostic = gemini_max_quality.configure_max_quality_env()

    assert os.environ["WHISPER_MODEL"] == "large-v3"

    warnings = [record.getMessage() for record in caplog.records]
    assert any("WHISPER_MODEL" in message and "tiny" in message for message in warnings), warnings
    assert any("LIVEDUB_INFO_FALLBACK_MODELS" in message for message in warnings), warnings

    # Диагностика старта называет подмену, чтобы её было видно и без логов.
    assert "overridden_env=" in diagnostic
    assert "WHISPER_MODEL" in diagnostic
    assert env_policy.overridden()["WHISPER_MODEL"] == ("tiny", "large-v3")


def test_matching_operator_value_is_silent(clean_policy_env, caplog) -> None:
    """Тот же значение, что форсирует политика, — не повод шуметь."""
    os.environ["GEMINI_MODEL"] = MODEL_PRIMARY
    with caplog.at_level(logging.WARNING, logger="services.env_policy"):
        diagnostic = gemini_max_quality.configure_max_quality_env()
    assert not [r for r in caplog.records if "GEMINI_MODEL" in r.getMessage()]
    assert "overridden_env=" not in diagnostic


def test_registry_matches_the_documented_list(clean_policy_env) -> None:
    _apply_both_policies()
    assert set(env_policy.POLICY_OWNED) == _documented_owned(), (
        "список POLICY-OWNED в .env.example разошёлся с кодом политики"
    )


def test_each_policy_owns_exactly_its_documented_variables(clean_policy_env) -> None:
    _apply_both_policies()
    for owner, expected in EXPECTED_OWNED.items():
        actual = {name for name, owners in env_policy.POLICY_OWNED.items() if owner in owners}
        assert actual == expected, f"{owner}: {actual ^ expected}"


def test_every_forced_variable_is_actually_read(clean_policy_env) -> None:
    """Форсировать переменную, которую никто не читает, — это мёртвая ручка.

    Аудит нашёл восемь таких: GEMINI_MAX_MODEL, GEMINI_FORCE_THINKING_LEVEL,
    GEMINI_LIGHT_* (3), LIVEDUB_INFO_THINKING,
    LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK, WHISPER_ENG_SUBTITLES_MODEL.
    Они были удалены, а не задокументированы. Тест не даёт появиться новым:
    каждая переменная из POLICY_OWNED обязана встречаться в коде как чтение
    (`os.getenv`/`os.environ.get`/`os.environ[...]`).
    """
    from scripts.render_env_reference import collect

    _apply_both_policies()
    read_somewhere = set(collect())
    dead = sorted(set(env_policy.POLICY_OWNED) - read_somewhere)
    assert not dead, (
        "политика форсирует переменные, которые код не читает (мёртвые ручки): "
        f"{dead}. Либо подключите чтение, либо удалите переменную."
    )


def test_policy_modules_do_not_write_managed_vars_directly() -> None:
    """Форсирование идёт только через env_policy.force*, иначе реестр врёт.

    Прямая запись в ``os.environ`` допустима для того, чем политика не владеет
    (прокси-маршрутизация в ``configure_gemini_network``) — но не для
    переменных из реестра.
    """
    # Static list: this check must not depend on another test having populated
    # the runtime registry first.
    managed = set().union(*EXPECTED_OWNED.values())

    for path in (
        Path("services/gemini_max_quality.py"),
        Path("services/livedub_quality_runtime.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "environ"
                ):
                    continue
                key = target.slice
                if isinstance(key, ast.Constant) and key.value in managed:
                    offenders.append((node.lineno, key.value))
        assert not offenders, f"{path}: прямой os.environ[...] {offenders}"


def test_overridden_report_is_a_copy() -> None:
    """Диагностика не должна давать менять внутреннее состояние через снимок."""
    env_policy.reset()
    env_policy.force("SOME_POLICY_VAR", "enforced", owner="test")
    snapshot = env_policy.overridden()
    snapshot["SOME_POLICY_VAR"] = ("tampered", "tampered")
    assert env_policy.overridden() == {}
