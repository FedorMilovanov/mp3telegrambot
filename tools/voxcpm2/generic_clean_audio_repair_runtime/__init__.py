#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed compatibility facade for clean audio repair.

The proven repair implementation stays in the sibling ``.py`` module. This
package shadows it for imports and ``python -m`` execution, preserving every
legacy helper while validating repair scope/seeds before execution and making
user-visible manifest settings match the rendered segments afterward.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as strict_core
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import clean_runtime_contract

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


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Не найден {label}: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} должен быть JSON-объектом.")
    return payload


def _strict_ids(values: Any, *, field: str) -> list[int]:
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"{field} должен быть непустым списком ID.")
    result: list[int] = []
    seen: set[int] = set()
    for position, value in enumerate(values, start=1):
        item_id = strict_core._strict_int(
            value,
            field=f"{field}[{position}]",
            low=1,
            high=2**31 - 1,
        )
        if item_id in seen:
            raise RuntimeError(f"{field} содержит повторный ID={item_id}.")
        seen.add(item_id)
        result.append(item_id)
    return result


def _segment_ids(path: Path) -> list[int]:
    if not path.is_file():
        raise RuntimeError(f"Не найден segments_ru_final.json: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён segments_ru_final.json: {path}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или не является списком.")
    values: list[Any] = []
    for position, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"segment[{position}] должен быть JSON-объектом, "
                f"получено {type(item).__name__}."
            )
        values.append(item.get("id"))
    return _strict_ids(values, field="segments.id")


def _validate_repair_request(root: Path, project_id: str) -> dict[str, Any]:
    repair = _load_json_object(root / "input" / "audio_repair.json", "audio_repair.json")
    schema = strict_core._strict_int(
        repair.get("schema_version"),
        field="audio_repair.schema_version",
        low=1,
        high=1,
    )
    if schema != 1:
        raise RuntimeError("Неподдерживаемая schema audio_repair.json.")
    if str(repair.get("project_id") or "") != str(project_id):
        raise RuntimeError("audio_repair.json относится к другому проекту.")
    repair_all = repair.get("repair_all")
    if not isinstance(repair_all, bool):
        raise RuntimeError("audio_repair.repair_all должен быть bool.")

    selected = _strict_ids(repair.get("segment_ids"), field="audio_repair.segment_ids")
    all_ids = _segment_ids(root / "segments_ru_final.json")
    selected_set = set(selected)
    all_set = set(all_ids)
    if not selected_set.issubset(all_set):
        raise RuntimeError("audio_repair содержит неизвестные segment ID.")
    if repair_all != (selected_set == all_set):
        raise RuntimeError("audio_repair.repair_all не соответствует выбранным segment ID.")
    return repair


def _next_seed(
    request: dict[str, Any],
    marker: dict[str, Any],
    manifest: dict[str, Any],
) -> int:
    initial = strict_core._strict_int(
        _legacy._request_value(request, "base_seed", 2026072800),
        field="repair.base_seed",
        low=0,
        high=clean_runtime_contract.MAX_BASE_SEED,
    )
    previous = strict_core._strict_int(
        _legacy._request_value(marker, "base_seed", initial),
        field="repair.marker_base_seed",
        low=0,
        high=clean_runtime_contract.MAX_BASE_SEED,
    )
    history = manifest.get("audio_repairs")
    repair_index = len(history) + 1 if isinstance(history, list) else 1
    candidate = max(initial, previous) + max(1, repair_index) * clean_runtime_contract.RETRY_SEED_OFFSET
    if not 0 <= candidate <= clean_runtime_contract.MAX_BASE_SEED:
        raise RuntimeError("Следующий repair seed выходит за безопасный диапазон.")
    return candidate


def _dominant_segment_delay(root: Path) -> int:
    """Return the global delay proven by rendered segment data.

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


def main() -> None:
    project_id = _legacy.production.current_project_id()
    root = _legacy.production.project_root(project_id)
    _validate_repair_request(root, project_id)
    _legacy.main()


# Legacy functions resolve these globals at runtime.
_legacy._next_seed = _next_seed
_legacy._update_manifest = _update_manifest

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "_dominant_segment_delay",
        "_next_seed",
        "_strict_ids",
        "_validate_repair_request",
        "_update_manifest",
        "main",
    }
)
