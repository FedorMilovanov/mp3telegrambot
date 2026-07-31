#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared source-bed coefficients for monolithic Dub mastering and post-AAC QA."""
from __future__ import annotations

import math
from typing import Any

POLICY = "dialogue-suppressed-spatial-bed-v1"
QA_POLICY = "post-aac-dialogue-suppressed-spatial-bed-v1"
CENTER_FLOOR_RATIO = 0.065
MAX_CENTER_FLOOR = 0.010
SIDE_BED_RATIO = 1.0
CENTER_ABSOLUTE_TOLERANCE = 0.020
SIDE_ABSOLUTE_TOLERANCE = 0.035
MIN_SIDE_TO_MID_ENERGY_RATIO = 0.012
MIN_RUSSIAN_GAIN = 0.04
MAX_RUSSIAN_GAIN = 2.0


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result


def source_bed_levels(original_level: Any) -> dict[str, float]:
    requested = _finite(original_level, field="original_level")
    if not 0.0 <= requested <= 1.0:
        raise RuntimeError("original_level должен быть в диапазоне 0..1")
    center = min(MAX_CENTER_FLOOR, requested * CENTER_FLOOR_RATIO)
    spatial_side = requested * SIDE_BED_RATIO
    # The bounded full-center branch also carries its tiny share of source side.
    expected_total_side = center + spatial_side
    return {
        "requested_original_level": requested,
        "center_full_mix_level": center,
        "spatial_side_level": spatial_side,
        "expected_total_side_level": expected_total_side,
        "maximum_allowed_center_level": center + CENTER_ABSOLUTE_TOLERANCE,
        "minimum_allowed_side_level": max(0.0, expected_total_side - SIDE_ABSOLUTE_TOLERANCE),
        "maximum_allowed_side_level": expected_total_side + SIDE_ABSOLUTE_TOLERANCE,
    }


__all__ = [
    "CENTER_ABSOLUTE_TOLERANCE",
    "CENTER_FLOOR_RATIO",
    "MAX_CENTER_FLOOR",
    "MAX_RUSSIAN_GAIN",
    "MIN_RUSSIAN_GAIN",
    "MIN_SIDE_TO_MID_ENERGY_RATIO",
    "POLICY",
    "QA_POLICY",
    "SIDE_ABSOLUTE_TOLERANCE",
    "SIDE_BED_RATIO",
    "source_bed_levels",
]
