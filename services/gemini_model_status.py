#!/usr/bin/env python3
"""Gemini model diagnostics for the source-owned deployment policy.

The production route is intentionally based on models available to this API
project. Public model/deprecation pages can lag per-project availability, so
startup diagnostics must not present the routing decision as a stale catalog
claim.
"""
from __future__ import annotations

from dataclasses import dataclass

POLICY = "project-gemini-routing-2026-08-17-v4"
_PRIMARY_MODEL = "gemini-3.7-flash"
_PREVIOUS_PRIMARY = "gemini-3.6-flash"
_UTILITY_MODEL = "gemini-3.5-flash-lite"
_UNUSED_MID_MODEL = "gemini-3.5-flash"

_SCHEDULED_MIGRATION = {
    "gemini-3.1-flash-lite": ("2027-05-07", _UTILITY_MODEL),
}
_CURRENT_PREVIEW = {
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
}
_LEGACY_MIGRATION = {
    "gemini-2.5-flash": ("2026-10-16", _PRIMARY_MODEL),
    "gemini-2.5-flash-lite": ("2026-10-16", _UTILITY_MODEL),
    "gemini-2.5-pro": ("2026-10-16", "gemini-3.1-pro-preview"),
}
_SHUTDOWN = {
    "gemini-3-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro-preview",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-pro-preview-06-05",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash-preview-09-25",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-001",
    "gemini-1.0-pro",
    "gemini-pro",
    "gemini-pro-vision",
}


@dataclass(frozen=True)
class GeminiModelDiagnostic:
    level: str
    message: str
    policy: str = POLICY


def classify_gemini_model(model_name: str) -> GeminiModelDiagnostic:
    model = str(model_name or "").strip().lower()
    if model == _PRIMARY_MODEL:
        return GeminiModelDiagnostic(
            "info",
            f"GEMINI_MODEL='{model}' — max-quality production route этого API deployment.",
        )
    if model == _PREVIOUS_PRIMARY:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — предыдущий semantic route; текущая политика требует {_PRIMARY_MODEL}.",
        )
    if model == _UTILITY_MODEL:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — utility-only модель; для semantic/quality-sensitive задач требуется {_PRIMARY_MODEL}.",
        )
    if model == _UNUSED_MID_MODEL:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' не используется source-owned routing: heavy={_PRIMARY_MODEL}, utility={_UTILITY_MODEL}.",
        )
    if model in _SCHEDULED_MIGRATION:
        deadline, replacement = _SCHEDULED_MIGRATION[model]
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' имеет запланированную миграцию не позже {deadline}; перейдите на {replacement}.",
        )
    if model == "gemini-flash-latest":
        return GeminiModelDiagnostic(
            "warning",
            "GEMINI_MODEL='gemini-flash-latest' — плавающий alias; для воспроизводимого production "
            f"зафиксируйте {_PRIMARY_MODEL}.",
        )
    if model == "gemini-3-flash-preview":
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — preview route; production policy этого deployment использует {_PRIMARY_MODEL}.",
        )
    if model == "gemini-3.1-pro-preview":
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' — preview Pro route; Flash-задачи этого deployment используют {_PRIMARY_MODEL}.",
        )
    if model in _LEGACY_MIGRATION:
        deadline, replacement = _LEGACY_MIGRATION[model]
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' поддерживается до {deadline}; запланируйте переход на {replacement}.",
        )
    if model in _SHUTDOWN:
        return GeminiModelDiagnostic(
            "error",
            f"GEMINI_MODEL='{model}' отключена или снята с поддержки; используйте {_PRIMARY_MODEL}.",
        )
    if not model:
        return GeminiModelDiagnostic("error", "GEMINI_MODEL не задан.")
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' не входит в проверенную routing policy этого deployment; проверьте models.list.",
    )


__all__ = ["GeminiModelDiagnostic", "POLICY", "classify_gemini_model"]
