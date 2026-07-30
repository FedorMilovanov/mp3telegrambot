#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write-through compatibility facade for the Dub Studio supervisor.

The established implementation remains in ``services/dub_studio_runtime.py``.
This package pins the expected worker runtime to v4.7 for every import order and
forwards external monkeypatch assignments to the legacy module, preserving title
policy and test hooks whose functions resolve globals in that module.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_studio_runtime.py"
_SPEC = importlib.util.spec_from_file_location(
    "services._dub_studio_runtime_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub Studio supervisor: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

_WORKER_RUNTIME = "dub-worker-quality-v4.7"
_legacy._WORKER_RUNTIME = _WORKER_RUNTIME

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))
globals()["_WORKER_RUNTIME"] = _WORKER_RUNTIME


class _WriteThroughModule(types.ModuleType):
    """Keep package assignments and legacy function globals synchronized."""

    def __setattr__(self, name: str, value: Any) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str) -> Any:
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"_WORKER_RUNTIME"}
)
