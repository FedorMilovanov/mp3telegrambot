#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared activity-density helpers for VoxCPM2 edge and timing QA."""
from __future__ import annotations

from typing import Any

import numpy as np


def sustained_activity_index(
    active: Any,
    *,
    reverse: bool = False,
    window_frames: int = 14,
    minimum_active_frames: int = 7,
) -> int | None:
    """Return the first/last genuinely sustained activity frame.

    VoxCPM2 can emit a 20–40 ms reference-tail chirp before the requested
    sentence. A local ``3 of 5`` rule treats that chirp as speech. Requiring
    activity density across a longer forward/backward window rejects the chirp
    while retaining normal short words and plosive onsets through preroll.
    """
    values = np.asarray(active, dtype=bool).reshape(-1)
    if not len(values):
        return None

    window_frames = max(5, int(window_frames))
    minimum_active_frames = max(3, int(minimum_active_frames))
    indices = range(len(values) - 1, -1, -1) if reverse else range(len(values))

    for index in indices:
        if not values[index]:
            continue
        if reverse:
            left = max(0, index - window_frames + 1)
            right = index + 1
        else:
            left = index
            right = min(len(values), index + window_frames)
        width = right - left
        required = min(width, minimum_active_frames)
        if width >= 3 and int(np.count_nonzero(values[left:right])) >= required:
            return int(index)
    return None


__all__ = ["sustained_activity_index"]
