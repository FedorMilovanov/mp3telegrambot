#!/usr/bin/env python3
"""Gemini model diagnostics for the source-owned deployment policy.

All catalog knowledge (aliases, deadlines, role assignments) lives in
``core.model_catalog``.  This module only turns that data into operator-facing
messages, so a model change never requires editing diagnostics text or the
regression tests that assert on catalog facts.

Catalog snapshot and routing policy version are owned by the catalog too.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.model_catalog import (
    FLOATING_ALIASES,
    MODEL_PRIMARY,
    MODEL_UTILITY,
    POLICY,
    access_limited_models,
    current_models_outside_routing,
    deprecated_aliases,
    model_spec,
    preview_models,
    scheduled_migrations,
    shutdown_models,
)

_PRIMARY_MODEL = MODEL_PRIMARY
_UTILITY_MODEL = MODEL_UTILITY

__all__ = ["GeminiModelDiagnostic", "POLICY", "classify_gemini_model"]


@dataclass(frozen=True)
class GeminiModelDiagnostic:
    level: str
    message: str
    policy: str = POLICY


def _deprecated_alias(model: str) -> GeminiModelDiagnostic | None:
    aliases = deprecated_aliases()
    if model not in aliases:
        return None
    retired_at, replacement = aliases[model]
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' — deprecated alias с {retired_at}; провайдер "
        f"автоматически направляет запросы в {replacement}. Замените строку модели "
        f"на {replacement}, чтобы routing не зависел от скрытого redirect.",
    )


def _scheduled_migration(model: str) -> GeminiModelDiagnostic | None:
    migrations = scheduled_migrations()
    if model not in migrations:
        return None
    deadline, replacement = migrations[model]
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' имеет запланированную миграцию не позже {deadline}; "
        f"перейдите на {replacement}.",
    )


def _access_limited(model: str) -> GeminiModelDiagnostic | None:
    replacements = access_limited_models()
    if model not in replacements:
        return None
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' относится к Gemini 2.5: модель не deprecated и дата "
        "shutdown не объявлена, но с 2026-09-18 доступ к 2.5 ограничен проектами, "
        f"которые уже использовали эти модели. Для нового проекта перейдите на "
        f"{replacements[model]}.",
    )


def _outside_routing(model: str) -> GeminiModelDiagnostic | None:
    notes = current_models_outside_routing()
    if model not in notes:
        return None
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' — актуальная модель Gemini API ({notes[model]}); "
        f"она не входит в текущий text routing этого deployment "
        f"({_PRIMARY_MODEL}).",
    )


def classify_gemini_model(model_name: str) -> GeminiModelDiagnostic:
    model = str(model_name or "").strip().lower()
    if not model:
        return GeminiModelDiagnostic("error", "GEMINI_MODEL не задан.")
    if model == _PRIMARY_MODEL:
        return GeminiModelDiagnostic(
            "info",
            f"GEMINI_MODEL='{model}' — max-quality production route этого API deployment.",
        )
    if model == _UTILITY_MODEL:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — utility-only модель; для semantic/quality-sensitive "
            f"задач требуется {_PRIMARY_MODEL}.",
        )
    if model in FLOATING_ALIASES:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — плавающий alias; для воспроизводимого "
            f"production зафиксируйте {_PRIMARY_MODEL}.",
        )
    for builder in (_deprecated_alias, _scheduled_migration, _access_limited):
        diagnostic = builder(model)
        if diagnostic is not None:
            return diagnostic
    if model in preview_models():
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — preview route; production policy этого deployment "
            f"использует {_PRIMARY_MODEL}.",
        )
    if model in shutdown_models():
        return GeminiModelDiagnostic(
            "error",
            f"GEMINI_MODEL='{model}' отключена или снята с поддержки; используйте "
            f"{_PRIMARY_MODEL}.",
        )
    diagnostic = _outside_routing(model)
    if diagnostic is not None:
        return diagnostic
    spec = model_spec(model)
    if spec is not None:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — известная модель каталога, но не входит в "
            f"routing policy этого deployment; проверьте {_PRIMARY_MODEL}.",
        )
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' не входит в проверенную routing policy этого deployment; "
        "проверьте models.list.",
    )
