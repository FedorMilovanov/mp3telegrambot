#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed compatibility facade for clean audio repair.

The proven repair implementation stays in the sibling ``.py`` module. This
package shadows it for imports and ``python -m`` execution, preserving every
legacy helper while validating repair scope, hashes, segments, checkpoints and
seeds before execution and making manifest settings match rendered segments.
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
_legacy_checkpoint_ready = _legacy._checkpoint_ready


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


def _load_segments(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(f"Не найден segments_ru_final.json: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён segments_ru_final.json: {path}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или не является списком.")

    result: list[dict[str, Any]] = []
    raw_ids: list[Any] = []
    for position, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"segment[{position}] должен быть JSON-объектом, "
                f"получено {type(item).__name__}."
            )
        copied = dict(item)
        raw_ids.append(copied.get("id"))
        for field in ("start", "end"):
            strict_core._finite(
                copied.get(field),
                field=f"segment[{position}].{field}",
            )
        if copied.get("source_end") is not None:
            strict_core._finite(
                copied.get("source_end"),
                field=f"segment[{position}].source_end",
            )
        strict_core._strict_int(
            copied.get("start_delay_ms", 0),
            field=f"segment[{position}].start_delay_ms",
            low=0,
            high=1500,
        )
        if not str(copied.get("text") or "").strip():
            raise RuntimeError(f"segment[{position}] не содержит текста.")
        result.append(copied)

    ids = _strict_ids(raw_ids, field="segments.id")
    for item, segment_id in zip(result, ids, strict=True):
        item["id"] = segment_id
    return sorted(result, key=lambda item: int(item["id"]))


def _segment_ids(path: Path) -> list[int]:
    return [int(item["id"]) for item in _load_segments(path)]


def _validated_sha256(value: Any, *, field: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64:
        raise RuntimeError(f"{field} должен быть SHA-256 из 64 hex-символов.")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise RuntimeError(f"{field} содержит не-hex символы.") from exc
    return digest


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

    segments_path = root / "segments_ru_final.json"
    expected_sha = _validated_sha256(
        repair.get("segments_sha256"),
        field="audio_repair.segments_sha256",
    )
    actual_sha = _legacy.legacy_repair._sha256(segments_path)
    if actual_sha != expected_sha:
        raise RuntimeError(
            "segments_ru_final.json изменился после создания repair request; "
            "создайте /dubfix заново."
        )

    selected = _strict_ids(repair.get("segment_ids"), field="audio_repair.segment_ids")
    segments = _load_segments(segments_path)
    all_ids = [int(item["id"]) for item in segments]
    selected_set = set(selected)
    all_set = set(all_ids)
    if not selected_set.issubset(all_set):
        raise RuntimeError("audio_repair содержит неизвестные segment ID.")
    if repair_all != (selected_set == all_set):
        raise RuntimeError("audio_repair.repair_all не соответствует выбранным segment ID.")

    if not repair_all:
        source = root / "source" / "source.mp4"
        if not source.is_file():
            raise RuntimeError("Для выборочного ремонта отсутствует source/source.mp4.")
        duration = _legacy.pipeline.ffprobe_duration(source)
        strict_core._mark_and_validate_segments(
            [dict(item) for item in segments],
            duration,
        )
    return repair


def _next_seed(
    request: dict[str, Any],
    marker: dict[str, Any],
    manifest: dict[str, Any],
) -> int:
    if not isinstance(request, dict) or not isinstance(marker, dict) or not isinstance(manifest, dict):
        raise RuntimeError("Repair seed inputs должны быть JSON-объектами.")
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
    if history is None:
        repair_index = 1
    elif isinstance(history, list):
        repair_index = len(history) + 1
    else:
        raise RuntimeError("manifest.audio_repairs должен быть списком.")
    candidate = max(initial, previous) + max(1, repair_index) * clean_runtime_contract.RETRY_SEED_OFFSET
    if not 0 <= candidate <= clean_runtime_contract.MAX_BASE_SEED:
        raise RuntimeError("Следующий repair seed выходит за безопасный диапазон.")
    return candidate


def _checkpoint_ready(
    work_dir: Path,
    segment_id: int,
) -> tuple[bool, str]:
    expected_id = strict_core._strict_int(
        segment_id,
        field="checkpoint.segment_id",
        low=1,
        high=2**31 - 1,
    )
    payload = _legacy._checkpoint_payload(work_dir, expected_id)
    report = payload.get("report") if isinstance(payload, dict) else None
    if not isinstance(report, dict):
        return False, "checkpoint report отсутствует"
    try:
        report_id = strict_core._strict_int(
            report.get("id"),
            field="checkpoint.report.id",
            low=1,
            high=2**31 - 1,
        )
    except RuntimeError as exc:
        return False, str(exc)
    if report_id != expected_id:
        return False, "checkpoint report id не совпадает"
    return _legacy_checkpoint_ready(work_dir, expected_id)


def _dominant_segment_delay(root: Path) -> int:
    """Return the global delay proven by rendered segment data.

    Tail segments may be capped downward to remain inside the video. Therefore
    the maximum validated segment delay is the truthful global setting used by
    the renderer, while every value still has to satisfy the clean 0..1500 ms
    contract.
    """
    payload = _load_segments(Path(root) / "segments_ru_final.json")
    delays = [
        clean_request_settings.russian_delay_ms(
            {"russian_delay_ms": item.get("start_delay_ms")}
        )
        for item in payload
    ]
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
_legacy._checkpoint_ready = _checkpoint_ready
_legacy._update_manifest = _update_manifest
_legacy.legacy_repair._load_segments = _load_segments

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "_checkpoint_ready",
        "_dominant_segment_delay",
        "_load_segments",
        "_next_seed",
        "_strict_ids",
        "_validate_repair_request",
        "_validated_sha256",
        "_update_manifest",
        "main",
    }
)
