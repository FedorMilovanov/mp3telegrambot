#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent role policy for source-language prosody evidence."""
from __future__ import annotations

from typing import Any

POLICY = "diagnostic-only-no-cross-language-ranking-v1"
ROLE_KEY = "source_prosody_role"
DIAGNOSTIC_ONLY_ROLE = POLICY


def is_diagnostic_only(segment: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(segment, dict)
        and str(segment.get(ROLE_KEY) or "") == DIAGNOSTIC_ONLY_ROLE
    )


def ranking_view(segment: dict[str, Any]) -> dict[str, Any]:
    """Return a safe ranking view with source prosody removed by default."""
    result = dict(segment)
    if is_diagnostic_only(segment):
        result.pop("source_prosody", None)
    return result


def mark_diagnostic_only(segment: dict[str, Any]) -> dict[str, Any]:
    result = dict(segment)
    result[ROLE_KEY] = DIAGNOSTIC_ONLY_ROLE
    return result


__all__ = [
    "DIAGNOSTIC_ONLY_ROLE",
    "POLICY",
    "ROLE_KEY",
    "is_diagnostic_only",
    "mark_diagnostic_only",
    "ranking_view",
]
