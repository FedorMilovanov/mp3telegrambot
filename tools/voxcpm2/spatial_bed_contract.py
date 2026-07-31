#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared Russian-only master coefficients for direct monolithic dubbing.

The original sermon track is speech-bearing in both mid and side channels.  A
mid/side difference is therefore not assumed to be ambience.  Direct ready-SRT
production keeps the requested source level only as audit metadata and applies
zero source audio unless a future, separately verified speech-free ambience stem
is explicitly supplied.
"""
from __future__ import annotations

import math
from typing import Any

POLICY = "russian-only-direct-master-v2"
QA_POLICY = "post-aac-zero-source-bed-v2"
SOURCE_BED_POLICY = "speech-bearing-original-disabled-v1"
CENTER_FLOOR_RATIO = 0.0
MAX_CENTER_FLOOR = 0.0
SIDE_BED_RATIO = 0.0
CENTER_ABSOLUTE_TOLERANCE = 0.015
SIDE_ABSOLUTE_TOLERANCE = 0.015
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


def source_bed_levels(original_level: Any) -> dict[str, float | str | bool]:
    requested = _finite(original_level, field="original_level")
    if not 0.0 <= requested <= 1.0:
        raise RuntimeError("original_level должен быть в диапазоне 0..1")
    center = 0.0
    spatial_side = 0.0
    expected_total_side = 0.0
    return {
        "source_bed_policy": SOURCE_BED_POLICY,
        "source_bed_applied": False,
        "source_bed_disabled_reason": "original_mid_and_side_may_both_contain_dialogue",
        "requested_original_level": requested,
        "applied_original_level": 0.0,
        "center_full_mix_level": center,
        "spatial_side_level": spatial_side,
        "expected_total_side_level": expected_total_side,
        "maximum_allowed_center_level": CENTER_ABSOLUTE_TOLERANCE,
        "minimum_allowed_side_level": -SIDE_ABSOLUTE_TOLERANCE,
        "maximum_allowed_side_level": SIDE_ABSOLUTE_TOLERANCE,
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
    "SOURCE_BED_POLICY",
    "source_bed_levels",
]
