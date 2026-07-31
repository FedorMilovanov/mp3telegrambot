#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voice/noise-separated facade for late broadband tail detection."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import numpy as np

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_tail_artifact.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_tail_artifact_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить late-tail detector: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

VOICE_CLASSIFICATION_POLICY = "conjunctive-voiced-vs-broadband-tail-v1"


def _last_sustained_voice(
    active: np.ndarray,
    zcr: np.ndarray,
    high_ratio: np.ndarray,
    flatness: np.ndarray,
) -> tuple[int, int] | None:
    """Require speech-like conjunctions so broadband noise cannot become voice."""
    active = np.asarray(active, dtype=bool)
    zcr = np.asarray(zcr, dtype=np.float64)
    high_ratio = np.asarray(high_ratio, dtype=np.float64)
    flatness = np.asarray(flatness, dtype=np.float64)
    voice_like = active & (
        ((zcr <= 0.23) & (flatness <= 0.42))
        | ((high_ratio <= 0.48) & (flatness <= 0.22))
    )
    sustained = [
        (left, right)
        for left, right in _legacy._runs(voice_like)
        if right - left >= 4
    ]
    return sustained[-1] if sustained else None


_legacy._last_sustained_voice = _last_sustained_voice


class _WriteThroughModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str):
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"VOICE_CLASSIFICATION_POLICY", "_last_sustained_voice"}
)
