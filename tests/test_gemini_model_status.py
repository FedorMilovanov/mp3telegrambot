"""Diagnostics contract: every catalog status maps to the right operator message.

The tests are parametrized over ``core.model_catalog`` itself, so adding or
retiring a model never requires editing them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.model_catalog import (
    FLOATING_ALIASES,
    MODEL_PRIMARY,
    MODEL_UTILITY,
    POLICY,
    access_limited_models,
    current_models_outside_routing,
    deprecated_aliases,
    paid_only_models,
    preview_models,
    scheduled_migrations,
    shutdown_models,
)
from services.gemini_model_status import classify_gemini_model

ROOT = Path(__file__).resolve().parents[1]


def test_primary_route_is_the_deployment_max_quality_route() -> None:
    diagnostic = classify_gemini_model(MODEL_PRIMARY)
    assert diagnostic.level == "info"
    assert "max-quality production route" in diagnostic.message


def test_utility_route_is_warned_as_not_semantic() -> None:
    diagnostic = classify_gemini_model(MODEL_UTILITY)
    assert diagnostic.level == "warning"
    assert "utility-only" in diagnostic.message
    assert MODEL_PRIMARY in diagnostic.message


@pytest.mark.parametrize("alias", sorted(deprecated_aliases()))
def test_deprecated_alias_names_its_real_target(alias: str) -> None:
    since, replacement = deprecated_aliases()[alias]
    diagnostic = classify_gemini_model(alias)
    assert diagnostic.level == "warning"
    assert since in diagnostic.message
    assert "deprecated alias" in diagnostic.message
    assert replacement in diagnostic.message


@pytest.mark.parametrize("model", sorted(set(scheduled_migrations()) - paid_only_models()))
def test_scheduled_migration_reports_exact_deadline(model: str) -> None:
    deadline, replacement = scheduled_migrations()[model]
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "warning"
    assert deadline in diagnostic.message
    assert replacement in diagnostic.message


@pytest.mark.parametrize("model", sorted(shutdown_models()))
def test_shutdown_models_are_errors(model: str) -> None:
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "error"
    assert MODEL_PRIMARY in diagnostic.message


@pytest.mark.parametrize("model", sorted(access_limited_models()))
def test_access_limited_models_are_not_reported_as_shutdown(model: str) -> None:
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "warning"
    assert "не deprecated" in diagnostic.message
    assert "2026-09-18" in diagnostic.message
    assert access_limited_models()[model] in diagnostic.message


@pytest.mark.parametrize("model", sorted(preview_models() - paid_only_models()))
def test_preview_models_are_not_reported_as_stable(model: str) -> None:
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "warning"
    assert "preview" in diagnostic.message
    assert MODEL_PRIMARY in diagnostic.message


@pytest.mark.parametrize(
    "model", sorted(set(current_models_outside_routing()) - paid_only_models())
)
def test_current_models_outside_routing_are_described(model: str) -> None:
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "warning"
    assert "актуальная модель" in diagnostic.message
    assert MODEL_PRIMARY in diagnostic.message


@pytest.mark.parametrize("model", sorted(paid_only_models()))
def test_paid_only_models_are_errors_under_the_free_tier_policy(model: str) -> None:
    """A paid-only model cannot work on a free key, whatever else it is."""
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "error"
    assert "paid tier" in diagnostic.message
    assert "free tier" in diagnostic.message
    assert MODEL_PRIMARY in diagnostic.message


@pytest.mark.parametrize("model", sorted(FLOATING_ALIASES))
def test_floating_alias_warns_about_hot_swap(model: str) -> None:
    diagnostic = classify_gemini_model(model)
    assert diagnostic.level == "warning"
    assert "плавающий alias" in diagnostic.message
    assert MODEL_PRIMARY in diagnostic.message


def test_unknown_and_empty_models_warn_or_error() -> None:
    unknown = classify_gemini_model("gemini-unknown-experiment")
    assert unknown.level == "warning"
    assert "models.list" in unknown.message

    empty = classify_gemini_model("")
    assert empty.level == "error"


def test_policy_version_tracks_catalog_snapshot() -> None:
    assert POLICY.startswith("project-gemini-routing-")


def test_main_uses_classified_capabilities() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "classify_gemini_model" in source
    assert "_required_tools" in source
    assert "_optional_tools" in source
    assert "часть функций молча деградирует" not in source
    assert "gemini-3.1-flash-lite-preview" not in source
