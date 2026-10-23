"""Contracts for ``core.model_catalog`` - the single source of model IDs.

These tests assert *catalog behaviour*, never a hardcoded literal: bumping the
model in ``core/model_catalog.py`` must not require editing this file.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core import model_catalog as catalog


def test_every_role_has_a_model_or_is_intentionally_empty() -> None:
    for role in catalog.ROLE_MODELS:
        value = catalog.ROLE_MODELS[role]
        assert isinstance(value, str)
        if role != catalog.ROLE_FALLBACK:
            assert value, f"role {role} must have a model"


def test_role_models_are_current_catalog_entries() -> None:
    for role, model_id in catalog.ROLE_MODELS.items():
        if not model_id:
            continue
        spec = catalog.model_spec(model_id)
        assert spec is not None, f"{role} points at an unknown model {model_id}"
        assert spec.status == "current", f"{role} points at {spec.status} {model_id}"


def test_role_constants_match_the_catalog_table() -> None:
    assert catalog.MODEL_PRIMARY == catalog.ROLE_MODELS[catalog.ROLE_PRIMARY]
    assert catalog.MODEL_UTILITY == catalog.ROLE_MODELS[catalog.ROLE_UTILITY]
    assert catalog.MODEL_TTS == catalog.ROLE_MODELS[catalog.ROLE_TTS]
    assert catalog.MODEL_TRANSCRIBE == catalog.ROLE_MODELS[catalog.ROLE_TRANSCRIBE]


def test_deprecated_aliases_resolve_to_their_real_target() -> None:
    for alias, (_since, replacement) in catalog.deprecated_aliases().items():
        assert catalog.normalize_model_id(alias) == replacement
        assert catalog.alias_target(alias) == replacement
        assert catalog.model_spec(replacement).status == "current"


def test_normalization_is_idempotent_and_keeps_unknown_models() -> None:
    for alias in catalog.deprecated_aliases():
        once = catalog.normalize_model_id(alias)
        assert catalog.normalize_model_id(once) == once
    assert catalog.normalize_model_id("gemini-custom-audio-model") == (
        "gemini-custom-audio-model"
    )
    assert catalog.normalize_model_id("") == ""


def test_configured_model_normalizes_a_stale_env_override(monkeypatch) -> None:
    alias, (_since, replacement) = next(iter(catalog.deprecated_aliases().items()))
    monkeypatch.setenv("GEMINI_MODEL", alias)
    assert catalog.configured_model(catalog.ROLE_PRIMARY) == replacement

    monkeypatch.setenv("MP3BOT_PRIMARY_MODEL", "gemini-custom-model")
    # The project-specific override wins over the legacy generic name.
    assert catalog.configured_model(catalog.ROLE_PRIMARY) == "gemini-custom-model"


def test_configured_model_falls_back_to_the_catalog_default(monkeypatch) -> None:
    for name in catalog.ROLE_ENV[catalog.ROLE_PRIMARY]:
        monkeypatch.delenv(name, raising=False)
    assert catalog.configured_model(catalog.ROLE_PRIMARY) == catalog.MODEL_PRIMARY


def test_fallback_role_is_disabled_by_default(monkeypatch) -> None:
    for name in catalog.ROLE_ENV[catalog.ROLE_FALLBACK]:
        monkeypatch.delenv(name, raising=False)
    assert catalog.fallback_model() == ""

    monkeypatch.setenv("MP3BOT_FALLBACK_MODEL", catalog.MODEL_UTILITY)
    assert catalog.fallback_model() == catalog.MODEL_UTILITY


@pytest.mark.parametrize(
    ("model", "level", "expected"),
    [
        ("gemini-3.8-flash", "low", "low"),
        ("gemini-3.8-flash", "medium", "medium"),
        ("gemini-3.8-flash", "high", "high"),
        ("gemini-3.8-flash", "minimal", "high"),
        ("gemini-4-argon", "medium", "medium"),
        ("gemini-4-argon", "minimal", "high"),
        ("gemini-3.5-flash-lite", "high", "minimal"),
        ("gemini-3.5-flash", "high", "minimal"),
        ("gemini-custom-audio-model", "medium", "medium"),
    ],
)
def test_thinking_levels_follow_model_capabilities(model, level, expected) -> None:
    assert catalog.thinking_level_for(model, level) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gemini-3.8-flash", True),
        ("gemini-3-flash-preview", True),
        ("gemini-4-argon", True),
        ("gemini-40-argon", False),
        ("gemini-2.5-flash", False),
        ("my-gemini-3-proxy", False),
        ("", False),
    ],
)
def test_thinking_config_detection_is_generation_aware(model, expected) -> None:
    assert catalog.uses_thinking_config(model) is expected


def test_primary_role_is_not_a_deprecated_or_limited_model() -> None:
    statuses = set(catalog.deprecated_aliases())
    assert catalog.MODEL_PRIMARY not in statuses
    assert catalog.MODEL_PRIMARY not in catalog.scheduled_migrations()
    assert catalog.MODEL_PRIMARY not in catalog.access_limited_models()
    assert catalog.MODEL_PRIMARY not in catalog.shutdown_models()


def test_catalog_snapshot_is_dated_and_matches_policy() -> None:
    assert catalog.CATALOG_VERSION
    assert catalog.POLICY == f"project-gemini-routing-{catalog.CATALOG_VERSION}"


def test_module_has_no_repository_imports() -> None:
    """Pre-main policy imports this module before ``core.globals`` exists."""
    source = (catalog.__file__ or "")
    text = open(source, encoding="utf-8").read()
    assert "from core.globals" not in text
    assert "import core.globals" not in text
    assert "from services" not in text


def test_reload_keeps_role_table_stable() -> None:
    reloaded = importlib.reload(catalog)
    assert reloaded.ROLE_MODELS == catalog.ROLE_MODELS


def test_upgrade_drill_touches_exactly_one_line() -> None:
    """A model upgrade must be a one-line catalog edit plus the renderer."""
    source = Path(catalog.__file__ or "").read_text(encoding="utf-8")
    drill = importlib.import_module("scripts.model_upgrade_drill")
    swapped = drill._swap_catalog(source, "gemini-4-argon")

    diff = [
        (before, after)
        for before, after in zip(source.splitlines(), swapped.splitlines())
        if before != after
    ]
    assert len(diff) == 1
    assert "ROLE_PRIMARY" in diff[0][0]
    assert "gemini-4-argon" in diff[0][1]
