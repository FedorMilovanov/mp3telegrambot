#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive retry profiles for direct VoxCPM2 rendering.

The sibling module owns all FFmpeg fitting, timeline assembly and model-call
logic.  This facade extends only the deterministic candidate profile table so a
long render can recover from three artifact/cadence failures without changing
text, voice reference, timing limits or quality gates.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

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
_legacy_generation_profile = _legacy._generation_profile


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


_legacy._generation_profile = _generation_profile

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {"ADAPTIVE_RETRY_POLICY", "_generation_profile"}
)
