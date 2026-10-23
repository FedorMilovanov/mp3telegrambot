#!/usr/bin/env python3
"""Enforce the strong, grounded Gemini policy for LiveDub translation QA.

The project already rejects unconfirmed Gemini findings with a focused second
audio pass.  Older installations can still carry explicit ``.env`` overrides
that point a QA route at an older/utility model.  The general model policy
historically used ``setdefault`` for those variables, so the stale value
survived and translation QA kept using a weak scheduled-migration Lite model.

Which identifiers count as "weak" is not a hand-written list: it is derived
from ``core.model_catalog`` (deprecated aliases, scheduled migrations,
access-limited legacy models, shutdown models and the utility route).

This module runs before ``core.globals`` creates Gemini clients.  It upgrades
known weak or project-obsolete QA model overrides, keeps deliberate custom models, forces
high reasoning, and keeps audio grounding plus focused confirmation enabled.
An explicit emergency escape hatch remains available for operator debugging.
"""
from __future__ import annotations

import os

from core.model_catalog import (
    MODEL_PRIMARY as _PRIMARY_MODEL,
    MODEL_UTILITY as _LIGHT_MODEL,
    access_limited_models,
    deprecated_aliases,
    scheduled_migrations,
    shutdown_models,
)

_QA_MODEL_ENV = (
    "LIVEDUB_QUICK_QA_MODEL",
    "LIVEDUB_LONG_QA_MODEL",
    "LIVEDUB_QA_VERIFY_MODEL",
)
_QA_THINKING_ENV = (
    "LIVEDUB_QUICK_QA_THINKING",
    "LIVEDUB_LONG_QA_THINKING",
    "LIVEDUB_QA_VERIFY_THINKING",
)
# Lower-tier/stale catalog entries are migration inputs only, never QA
# fallbacks. False positives here can trigger auto-muting, so they are upgraded
# to the primary route. The set follows the catalog, so a catalog bump cannot
# leave a stale hardcoded identifier behind.
_RETIRED_OR_WEAK_QA_MODELS = frozenset(
    set(deprecated_aliases())
    | set(scheduled_migrations())
    | set(access_limited_models())
    | set(shutdown_models())
    | {_LIGHT_MODEL}
)
_TRUE = {"1", "true", "yes", "on"}


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE


def _qa_model(value: str) -> str:
    configured = str(value or "").strip()
    if not configured or configured in _RETIRED_OR_WEAK_QA_MODELS:
        return _PRIMARY_MODEL
    return configured


def configure_gemini_qa_policy() -> str:
    """Apply the translation-QA trust contract before Gemini client imports."""
    migrated: list[str] = []
    for name in _QA_MODEL_ENV:
        before = os.getenv(name, "").strip()
        after = _qa_model(before)
        os.environ[name] = after
        if before != after:
            migrated.append(name)

    # Translation QA is quality-sensitive even in the user-facing "Quick QA"
    # mode: a false major can alter the delivered audio.  Keep one consistent
    # high-reasoning contract across the broad scan and focused verifier.
    for name in _QA_THINKING_ENV:
        os.environ[name] = "high"

    # The first Gemini pass is only a candidate generator.  Unless the operator
    # explicitly enables the emergency escape hatch, both actual audio grounding
    # and focused confirmation remain mandatory regardless of stale .env values.
    allow_unconfirmed = _enabled("LIVEDUB_QA_ALLOW_UNCONFIRMED", False)
    if not allow_unconfirmed:
        os.environ["LIVEDUB_QA_AUDIO_TRUST"] = "1"
        os.environ["LIVEDUB_QA_CONFIRM_ISSUES"] = "1"

    models = ",".join(
        f"{name.removeprefix('LIVEDUB_').removesuffix('_MODEL').lower()}="
        f"{os.environ[name]}"
        for name in _QA_MODEL_ENV
    )
    confirmation = os.getenv("LIVEDUB_QA_CONFIRM_ISSUES", "1").strip() or "1"
    grounding = os.getenv("LIVEDUB_QA_AUDIO_TRUST", "1").strip() or "1"
    migration = ",".join(migrated) if migrated else "none"
    return (
        f"translation_qa[{models}; thinking=high; audio_trust={grounding}; "
        f"confirm={confirmation}; migrated={migration}]"
    )
