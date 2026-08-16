#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure universal helpers and generic timing preflight for VoxCPM2 production.

Direct CLI state and candidate/retry wrappers are owned by the canonical CLI.
This module exposes shared calculations plus the still-separate generic preflight.
"""
from __future__ import annotations

import json
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from tools.voxcpm2 import direct_timing_guard

POLICY = "voxcpm2-universal-production-hardening-v1"
_PROGRESS_POLICY = "candidate-aware-project-progress-v1"
_MODEL_TQDM_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)*\s*\d{1,3}%\|.*\|\s*\d+/\d+\s*\["
)


def _segments_by_id(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in segments:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        result[segment_id] = item
    return result


def _progress_value(
    *, position: int, total: int, attempt: int, max_attempts: int,
) -> int:
    total_value = max(1, int(total))
    position_value = max(1, min(int(position), total_value))
    attempts_value = max(1, int(max_attempts))
    attempt_value = max(1, min(int(attempt), attempts_value))
    fraction = (position_value - 1) + (attempt_value - 1) / attempts_value
    return max(8, min(86, 8 + round(fraction / total_value * 78)))


__all__ = ['POLICY']
