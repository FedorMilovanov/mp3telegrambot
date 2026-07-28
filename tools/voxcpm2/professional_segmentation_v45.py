#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Short semantic segmentation policy for every Professional Audio v4.5 mode."""
from __future__ import annotations

from typing import Any

from tools.voxcpm2 import dub_quality_v4

_ORIGINAL_GROUP_CUES = dub_quality_v4.group_cues_v4
_ORIGINAL_GROUP_READY_SRT = dub_quality_v4.group_ready_srt_v4


def group_cues_v45(
    cues: list[Any],
    *,
    target_seconds: float = 4.2,
    max_seconds: float = 5.4,
) -> list[dict[str, Any]]:
    return _ORIGINAL_GROUP_CUES(
        cues,
        target_seconds=min(float(target_seconds), 4.2),
        max_seconds=min(float(max_seconds), 5.4),
    )


def group_ready_srt_v45(
    cues: list[Any],
    *,
    max_seconds: float = 5.4,
) -> list[dict[str, Any]]:
    return _ORIGINAL_GROUP_READY_SRT(
        cues,
        max_seconds=min(float(max_seconds), 5.4),
    )


def install() -> None:
    dub_quality_v4.group_cues_v4 = group_cues_v45
    dub_quality_v4.group_ready_srt_v4 = group_ready_srt_v45
