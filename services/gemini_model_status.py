#!/usr/bin/env python3
"""Current official Gemini model diagnostics for startup reporting.

Reviewed against the official Gemini model and deprecation pages on 2026-08-03.
The generation config itself remains adaptive in ``core.globals``.
"""
from __future__ import annotations

from dataclasses import dataclass

POLICY = "official-gemini-model-status-2026-08-03-v3"

_CURRENT_GA = {
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
}
_SCHEDULED_MIGRATION = {
    "gemini-3.1-flash-lite": ("2027-05-07", "gemini-3.5-flash-lite"),
}
_CURRENT_PREVIEW = {
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
}
_LEGACY_MIGRATION = {
    "gemini-2.5-flash": ("2026-10-16", "gemini-3.6-flash"),
    "gemini-2.5-flash-lite": ("2026-10-16", "gemini-3.5-flash-lite"),
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
    if model in _CURRENT_GA:
        return GeminiModelDiagnostic(
            "info",
            f"GEMINI_MODEL='{model}' — актуальная production-модель Gemini API.",
        )
    if model in _SCHEDULED_MIGRATION:
        deadline, replacement = _SCHEDULED_MIGRATION[model]
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' остаётся GA, но выключение запланировано не раньше {deadline}; "
            f"перейдите на {replacement}.",
        )
    if model == "gemini-flash-latest":
        return GeminiModelDiagnostic(
            "warning",
            "GEMINI_MODEL='gemini-flash-latest' — плавающий alias может быть "
            "переключён на новую версию; для воспроизводимого production "
            "зафиксируйте gemini-3.6-flash.",
        )
    if model == "gemini-3-flash-preview":
        return GeminiModelDiagnostic(
            "warning",
            "GEMINI_MODEL='gemini-3-flash-preview' — действующая preview-модель; "
            "для стабильного production используйте gemini-3.6-flash.",
        )
    if model == "gemini-3.1-pro-preview":
        return GeminiModelDiagnostic(
            "warning",
            "GEMINI_MODEL='gemini-3.1-pro-preview' — действующая preview-модель Pro; "
            "стабильная Pro-версия пока не объявлена. Для задач, допускающих Flash, "
            "используйте gemini-3.6-flash.",
        )
    if model in _LEGACY_MIGRATION:
        deadline, replacement = _LEGACY_MIGRATION[model]
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' поддерживается до {deadline}; "
            f"запланируйте переход на {replacement}.",
        )
    if model in _SHUTDOWN:
        return GeminiModelDiagnostic(
            "error",
            f"GEMINI_MODEL='{model}' отключена или снята с поддержки; используйте gemini-3.6-flash.",
        )
    if not model:
        return GeminiModelDiagnostic("error", "GEMINI_MODEL не задан.")
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' не входит в проверенный официальный каталог на 2026-08-03; проверьте models.list.",
    )


__all__ = ["GeminiModelDiagnostic", "POLICY", "classify_gemini_model"]
