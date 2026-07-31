#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive retries and continuation-aware timeline assembly."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from tools.voxcpm2 import direct_timeline_compaction

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_max_quality_render.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_max_quality_render_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить direct render utilities: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

ADAPTIVE_RETRY_POLICY = "direct-candidate-adaptive-retry-v1"
TIMELINE_COMPACTION_POLICY = direct_timeline_compaction.POLICY
_legacy_generation_profile = _legacy._generation_profile
_legacy_build_timeline = _legacy.build_timeline


def _generation_profile(attempt: int, base_cfg: float, base_steps: int) -> tuple[float, int]:
    """Return deterministic profiles for three normal and two rescue attempts."""
    attempt = int(attempt)
    if attempt <= 3:
        return _legacy_generation_profile(attempt, base_cfg, base_steps)
    if attempt == 4:
        # Near-base CFG with more refinement: useful for cadence/noise failures
        # without pushing pitch or intensity farther from the reference.
        return min(2.20, max(1.45, float(base_cfg) + 0.05)), min(36, int(base_steps) + 14)
    if attempt == 5:
        # Deliberately different low-CFG trajectory and seed, still bounded.
        return max(1.35, float(base_cfg) - 0.30), min(40, int(base_steps) + 18)
    raise ValueError(f"Неподдерживаемая попытка VoxCPM: {attempt}")


def build_timeline(
    fitted_segments: list[tuple[dict[str, Any], Path]],
    output: Path,
    total_duration: float,
) -> None:
    """Late-align repairable continuations, then run the original fail-closed QA."""
    adjusted, report = direct_timeline_compaction.compact_timeline_segments(
        fitted_segments
    )
    shifted = [int(item) for item in report.get("shifted_segment_ids") or []]
    if shifted:
        details = ", ".join(
            "#{id} +{shift:.3f}s".format(
                id=int(item["id"]),
                shift=float(item.get("shift_seconds") or 0.0),
            )
            for item in report.get("segments") or []
            if float(item.get("shift_seconds") or 0.0) > 0.001
        )
        print(
            "🧩 Continuation timeline compaction: "
            + details
            + f"; target-gap={direct_timeline_compaction.TARGET_GAP_SECONDS:.3f}s",
            flush=True,
        )
    _legacy_build_timeline(adjusted, output, total_duration)


_legacy._generation_profile = _generation_profile
_legacy.build_timeline = build_timeline

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "ADAPTIVE_RETRY_POLICY",
        "TIMELINE_COMPACTION_POLICY",
        "_generation_profile",
        "build_timeline",
    }
)
