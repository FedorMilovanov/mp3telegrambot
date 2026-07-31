#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alignment-correct facade for post-AAC spatial-bed regression."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import numpy as np

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "final_media_spatial_bed.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._final_media_spatial_bed_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить spatial-bed estimator: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

ALIGNMENT_POLICY = "russian-reference-to-mixed-alignment-v2"


def _align_stereo(
    source: np.ndarray,
    mixed: np.ndarray,
    russian: np.ndarray,
    lag_samples: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Align source/Russian reference together against the encoded mixed branch."""
    lag = int(lag_samples)
    if lag > 0:
        # mixed is delayed relative to the Russian-only reference.
        source = source[:-lag]
        russian = russian[:-lag]
        mixed = mixed[lag:]
    elif lag < 0:
        # mixed starts earlier; trim source and Russian by the opposite offset.
        offset = -lag
        source = source[offset:]
        russian = russian[offset:]
        mixed = mixed[:-offset]
    length = min(len(source), len(mixed), len(russian))
    return source[:length], mixed[:length], russian[:length]


_legacy._align_stereo = _align_stereo


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
    | {"ALIGNMENT_POLICY", "_align_stereo"}
)
