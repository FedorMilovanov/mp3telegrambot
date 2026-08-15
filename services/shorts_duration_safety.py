#!/usr/bin/env python3
"""Pure source-window and public-delivery safety for ordinary Shorts.

The pipeline calls these helpers directly. This module owns no ContextVar,
installer, imported-module rebinding or ambient render state.
"""
from __future__ import annotations

import math
import os
from typing import Any

from services.media_delivery_probe import media_probe_is_deliverable

PUBLIC_SHORT_MAX_SEC = 180.0
PUBLIC_DURATION_EPSILON_SEC = 0.05
_SPEED_EPSILON = 0.01
_INTERVAL_EPSILON_SEC = 1e-6


def _finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _bounded_env_float(name: str, default: float, maximum: float = 30.0) -> float:
    value = _finite_float(os.getenv(name, ""), default)
    assert value is not None
    return max(0.0, min(float(value), maximum))


def _nonnegative_optional(value: Any) -> float:
    parsed = _finite_float(value, 0.0) or 0.0
    return max(0.0, parsed)


def short_speed_transform_required(speed: Any) -> bool:
    value = _finite_float(speed)
    return bool(value is not None and value > 0 and abs(value - 1.0) > _SPEED_EPSILON)


def public_short_duration_ok(duration: Any) -> bool:
    value = _finite_float(duration)
    return bool(
        value is not None
        and value > 0.0
        and value <= PUBLIC_SHORT_MAX_SEC + PUBLIC_DURATION_EPSILON_SEC
    )


def plan_short_source_window(
    candidate: dict[str, Any],
    *,
    speed: Any,
    boundary_padding: bool,
    source_duration: Any = 0.0,
    pre_roll: float | None = None,
    post_roll: float | None = None,
) -> tuple[float, float, float] | None:
    """Return ``(start, requested_end, absolute_snap_ceiling)``.

    The source budget is ``180s * speed`` so the public file can never exceed
    three minutes after the requested speed transform. Optional boundary
    padding is reclaimed inside that same budget rather than added on top.
    """
    start = _finite_float(candidate.get("start_seconds"))
    end = _finite_float(candidate.get("end_seconds"))
    speed_value = _finite_float(speed)
    if (
        start is None
        or end is None
        or speed_value is None
        or start < 0.0
        or end <= start
        or speed_value <= 0.0
    ):
        return None

    source_limit = _finite_float(source_duration, 0.0) or 0.0
    if source_limit > 0.0 and end > source_limit + _INTERVAL_EPSILON_SEC:
        return None

    source_budget = PUBLIC_SHORT_MAX_SEC * speed_value
    semantic_span = end - start
    if semantic_span > source_budget + _INTERVAL_EPSILON_SEC:
        return None

    desired_pre = 0.0
    desired_post = 0.0
    if boundary_padding:
        desired_pre = (
            _bounded_env_float("SHORTS_PREROLL_SECONDS", 1.5)
            if pre_roll is None
            else _nonnegative_optional(pre_roll)
        )
        desired_post = (
            _bounded_env_float("SHORTS_POSTROLL_SECONDS", 2.5)
            if post_roll is None
            else _nonnegative_optional(post_roll)
        )
        desired_pre = min(desired_pre, start)

    spare = max(0.0, source_budget - semantic_span)
    desired_extra = desired_pre + desired_post
    scale = 1.0
    if desired_extra > 0.0 and desired_extra > spare:
        scale = spare / desired_extra

    render_start = max(0.0, start - desired_pre * scale)
    render_end = end + desired_post * scale
    if source_limit > 0.0:
        render_end = min(render_end, source_limit)
    if render_end <= render_start:
        return None

    snap_ceiling = math.floor(render_start + source_budget + 1e-9)
    if source_limit > 0.0:
        snap_ceiling = min(snap_ceiling, math.floor(source_limit + 1e-9))
    snap_ceiling = max(render_end, float(snap_ceiling))
    if snap_ceiling - render_start > source_budget + _INTERVAL_EPSILON_SEC:
        snap_ceiling = render_end

    return render_start, render_end, snap_ceiling


def authoritative_short_source_start(
    pipeline_source_start: Any,
    render_window: Any,
) -> float:
    fallback = max(0.0, _finite_float(pipeline_source_start, 0.0) or 0.0)
    if not isinstance(render_window, (tuple, list)) or not render_window:
        return fallback
    actual = _finite_float(render_window[0])
    if actual is None or actual < 0.0:
        return fallback
    return actual


def final_public_short_is_safe(probe: Any, *, max_file_size_mb: Any) -> bool:
    """Require deliverable media, <=180.05s and the active upload-size cap."""
    if not media_probe_is_deliverable(probe):
        return False
    if not public_short_duration_ok(getattr(probe, "duration", 0.0)):
        return False
    limit = _finite_float(max_file_size_mb)
    size = _finite_float(getattr(probe, "size_mb", None))
    return bool(
        limit is not None
        and limit > 0.0
        and size is not None
        and size >= 0.0
        and size <= limit + 1e-9
    )


__all__ = [
    "PUBLIC_DURATION_EPSILON_SEC",
    "PUBLIC_SHORT_MAX_SEC",
    "authoritative_short_source_start",
    "final_public_short_is_safe",
    "plan_short_source_window",
    "public_short_duration_ok",
    "short_speed_transform_required",
]
