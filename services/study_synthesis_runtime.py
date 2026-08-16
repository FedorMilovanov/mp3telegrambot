"""Compatibility facade for the former Study runtime installer.

Study policy is source-owned by ``study_synthesis_policy``, ``structured_blocks``,
``content_audit`` and ``telegraph_pages``.  This module performs no mutation and
contains no import hook.
"""
from __future__ import annotations

from core.study_quality import render_word_study_as_prose
from services.study_synthesis_policy import (
    TEACHERLY_STUDY_PROMPT,
    validate_teacherly_study_policy,
)


__all__ = [
    "TEACHERLY_STUDY_PROMPT",
    "render_word_study_as_prose",
]
