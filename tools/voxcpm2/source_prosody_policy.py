#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent role policy for source-language prosody evidence."""
from __future__ import annotations

from typing import Any

POLICY = "diagnostic-only-no-cross-language-ranking-v2"
ROLE_KEY = "source_prosody_role"
DIAGNOSTIC_ONLY_ROLE = POLICY


def is_diagnostic_only(segment: dict[str, Any] | None) -> bool:
    """Source-language prosody is always diagnostic in the Russian pipeline."""
    return isinstance(segment, dict)


def ranking_view(segment: dict[str, Any]) -> dict[str, Any]:
    """Return a fail-closed ranking view without source-language prosody.

    Callers cannot opt into cross-language ranking by omitting a marker. The
    original evidence remains available on the source object for reporting,
    while every candidate-selection and hard-gate view removes it.
    """
    result = dict(segment)
    result.pop("source_prosody", None)
    result[ROLE_KEY] = DIAGNOSTIC_ONLY_ROLE
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
