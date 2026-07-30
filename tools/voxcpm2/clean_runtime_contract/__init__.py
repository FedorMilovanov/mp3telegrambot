#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade for the clean runtime fingerprint contract.

The stable contract remains in ``clean_runtime_contract.py``. This package keeps
its API and extends fingerprints with every compatibility facade, render gate,
retry-state contract and final encoded release gate resolved by production imports.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "clean_runtime_contract.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._clean_runtime_contract_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean runtime contract: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

_FACADE_RENDER_MODULES = (
    "tools/voxcpm2/clean_runtime_contract/__init__.py",
    "tools/voxcpm2/clean_production_core/__init__.py",
    "tools/voxcpm2/generic_project_runtime/__init__.py",
    "tools/voxcpm2/clean_source_download/__init__.py",
    "tools/voxcpm2/direct_max_quality_analysis/__init__.py",
    "tools/voxcpm2/direct_max_quality_render/__init__.py",
    "tools/voxcpm2/direct_retry_epoch.py",
    "tools/voxcpm2/direct_russian_cadence.py",
    "tools/voxcpm2/direct_russian_cadence/__init__.py",
    "tools/voxcpm2/direct_tail_artifact.py",
    "tools/voxcpm2/direct_timeline_delivery_qa.py",
)
_FACADE_RELEASE_MODULES = (
    "tools/voxcpm2/final_encoded_delivery_qa.py",
)
_legacy._RENDER_MODULES = tuple(
    dict.fromkeys((*_legacy._RENDER_MODULES, *_FACADE_RENDER_MODULES))
)
_legacy._RELEASE_MODULES = tuple(
    dict.fromkeys((*_legacy._RELEASE_MODULES, *_FACADE_RELEASE_MODULES))
)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

_RENDER_MODULES = _legacy._RENDER_MODULES
_RELEASE_MODULES = _legacy._RELEASE_MODULES

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {"_RENDER_MODULES", "_RELEASE_MODULES"}
)
