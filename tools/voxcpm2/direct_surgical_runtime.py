#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-install surgical runtime for universal direct VoxCPM2 jobs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.voxcpm2 import direct_retry_epoch
from tools.voxcpm2 import direct_surgical_io
from tools.voxcpm2 import direct_timing_guard as guard

POLICY = "voxcpm2-surgical-runtime-v1"
_PROGRESS_POLICY = "candidate-aware-project-progress-v2"
_RUNTIME_SCOPE_FILES = (
    "tools/voxcpm2/direct_timing_guard.py",
    "tools/voxcpm2/direct_universal_runtime.py",
    "tools/voxcpm2/direct_surgical_runtime.py",
    "tools/voxcpm2/direct_surgical_io.py",
    "tools/voxcpm2/direct_failure_recovery.py",
    "tools/voxcpm2/direct_retry_epoch.py",
    "tools/voxcpm2/direct_max_quality_cli.py",
    "services/speech_backends/voxcpm2.py",
    "services/speech_backends/audited_voxcpm2.py",
)


def _segments_by_id(values: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if segment_id > 0:
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
