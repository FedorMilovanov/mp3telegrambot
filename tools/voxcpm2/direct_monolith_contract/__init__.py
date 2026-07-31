#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resume-safe facade for the monolithic candidate identity contract.

The sibling module keeps the acoustic implementation.  This facade resolves the
immediate previous accepted identity from durable checkpoints for every segment,
so a mixture of restored and newly synthesized cues cannot compare against a
non-adjacent in-memory candidate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_monolith_contract.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_monolith_contract_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить monolithic candidate contract: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

RESUME_POLICY = "nearest-accepted-checkpoint-identity-v1"
_legacy_evaluate_candidate = _legacy.evaluate_candidate


def _work_dir(candidate: dict[str, Any]) -> Path:
    path = Path(str(candidate.get("path") or "."))
    return path.parent.parent if path.parent.name == "attempts" else path.parent


def evaluate_candidate(candidate: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    segment_id = int(segment.get("id") or 0)
    # Finalize any in-memory previous segment first, then replace it with the
    # authoritative immediate checkpoint.  The previous checkpoint is written
    # before the renderer advances to this segment.
    _legacy.set_current_segment_id(segment_id)
    _legacy._PREVIOUS_IDENTITY = (
        _legacy._load_previous_checkpoint(_work_dir(candidate), segment_id)
        if segment_id > 1
        else None
    )
    result = dict(_legacy_evaluate_candidate(candidate, segment))
    result["resume_policy"] = RESUME_POLICY
    return result


_legacy.evaluate_candidate = evaluate_candidate


class _WriteThroughModule(types.ModuleType):
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
    | {"RESUME_POLICY", "evaluate_candidate"}
)
