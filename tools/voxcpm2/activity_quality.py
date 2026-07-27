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
    window_frames: int = 16,
    minimum_active_frames: int = 7,
    maximum_gap_frames: int = 2,
) -> int | None:
    """Return the first/last genuinely connected speech-activity frame.

    VoxCPM2 can emit a 20–40 ms reference-tail chirp before the requested
    sentence. Counting active frames alone can join that chirp to real speech
    across a silent gap. This helper requires the first seven active frames to
    form one connected cluster, allowing only short natural speech gaps.
    """
    values = np.asarray(active, dtype=bool).reshape(-1)
    if not len(values):
        return None

    window_frames = max(7, int(window_frames))
    minimum_active_frames = max(4, int(minimum_active_frames))
    maximum_gap_frames = max(0, int(maximum_gap_frames))

    if reverse:
        reversed_index = sustained_activity_index(
            values[::-1],
            window_frames=window_frames,
            minimum_active_frames=minimum_active_frames,
            maximum_gap_frames=maximum_gap_frames,
        )
        return None if reversed_index is None else len(values) - 1 - reversed_index

    for index in range(len(values)):
        if not values[index]:
            continue
        window = values[index : min(len(values), index + window_frames)]
        positions = np.flatnonzero(window)
        if len(positions) < minimum_active_frames:
            continue
        connected = positions[:minimum_active_frames]
        if len(connected) == 1:
            return int(index)
        # A difference of 1 means contiguous frames. Allow at most the requested
        # number of silent frames between successive activity frames.
        if int(np.max(np.diff(connected))) <= maximum_gap_frames + 1:
            return int(index)
    return None


__all__ = ["sustained_activity_index"]
