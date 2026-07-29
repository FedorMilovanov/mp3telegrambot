#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade for clean audio repair manifest finalization.

The proven repair implementation stays in the sibling ``.py`` module. This
package shadows it for imports and ``python -m`` execution, preserving every
legacy helper while adding one post-write truth check for user-visible settings.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_request_settings

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "generic_clean_audio_repair_runtime.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._generic_clean_audio_repair_runtime_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean audio repair runtime: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_update_manifest = _legacy._update_manifest


def _dominant_segment_delay(root: Path) -> int:
    """Return the requested global delay as proven by rendered segment data.

    Tail segments may be capped downward to remain inside the video. Therefore
    the maximum validated segment delay is the truthful global setting used by
    the renderer, while every value still has to satisfy the clean 0..1500 ms
    contract.
    """
    path = Path(root) / "segments_ru_final.json"
    if not path.is_file():
        raise RuntimeError(f"Не найден segments_ru_final.json для repair manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён segments_ru_final.json: {path}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или не является списком.")

    delays: list[int] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict) or "start_delay_ms" not in item:
            raise RuntimeError(f"У repair segment #{index} отсутствует start_delay_ms.")
        delays.append(
            clean_request_settings.russian_delay_ms(
                {"russian_delay_ms": item.get("start_delay_ms")}
            )
        )
    return max(delays)


def _update_manifest(
    path: Path,
    manifest: dict[str, Any],
    *,
    selected_ids: list[int],
    repair_all: bool,
    seed: int,
    report_path: Path,
    marker: dict[str, Any],
) -> None:
    _legacy_update_manifest(
        path,
        manifest,
        selected_ids=selected_ids,
        repair_all=repair_all,
        seed=seed,
        report_path=report_path,
        marker=marker,
    )
    root = Path(path).resolve().parent.parent
    request = _legacy.production.load_request(root)
    clean_request_settings.repair_manifest(
        root,
        request,
        actual_delay_ms=_dominant_segment_delay(root),
    )


# Legacy main resolves this name dynamically when it finishes a repair.
_legacy._update_manifest = _update_manifest
main = _legacy.main

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"_dominant_segment_delay", "_update_manifest", "main"}
)
