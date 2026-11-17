"""Explicit layout choice required at all generic Shorts renderer boundaries.

The legacy full_frame_vertical layout includes blur and is not a full-bleed
solo-speaker mode. A missing/unknown selection must never silently use it.
This does not replace reviewed per-camera-shot crop plans.
"""

from __future__ import annotations
from typing import TypeGuard

SUPPORTED_VISUAL_MODES = frozenset(("crop_zoom", "full_frame_vertical"))


def has_explicit_visual_mode(value: object) -> TypeGuard[str]:
    """Return True only for one of the two *explicitly selected* legacy modes."""
    return isinstance(value, str) and value in SUPPORTED_VISUAL_MODES
