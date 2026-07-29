#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict compatibility facade for clean production orchestration.

The proven orchestration remains in ``clean_production_core.py``. This package
preserves its public API and replaces only numeric/segment validation before
references, model loading, checkpoint reuse, or rendering can begin.
"""
from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "clean_production_core.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._clean_production_core_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean production core: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result


def _strict_int(
    value: Any,
    *,
    field: str,
    low: int,
    high: int,
) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        raise RuntimeError(f"{field} должен быть целым числом.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{field}={result} вне диапазона {low}..{high}.")
    return result


def _mark_and_validate_segments(
    segments: list[dict[str, Any]],
    duration: float,
) -> None:
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration должен быть > 0.")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Список реплик перед VoxCPM пуст или повреждён.")

    previous_end = 0.0
    previous_effective_end = 0.0
    seen_ids: set[int] = set()
    for position, item in enumerate(segments, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"segment[{position}] должен быть JSON-объектом, "
                f"получено {type(item).__name__}."
            )
        segment_id = _strict_int(
            item.get("id"),
            field=f"segment[{position}].id",
            low=1,
            high=2**31 - 1,
        )
        if segment_id in seen_ids:
            raise RuntimeError(f"Повторный ID реплики: {segment_id}.")
        seen_ids.add(segment_id)
        item["id"] = segment_id
        item["production_policy"] = POLICY

        start = _finite(item.get("start"), field=f"segment[{segment_id}].start")
        end = _finite(item.get("end"), field=f"segment[{segment_id}].end")
        delay_ms = _strict_int(
            item.get("start_delay_ms", 0),
            field=f"segment[{segment_id}].start_delay_ms",
            low=0,
            high=1500,
        )
        item["start_delay_ms"] = delay_ms
        delay = delay_ms / 1000.0
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if start < 0.0 or not text or end <= start:
            raise RuntimeError(f"Некорректная реплика #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Реплика #{segment_id} пересекается с предыдущей.")
        effective_start = start + delay
        effective_end = end + delay
        if effective_start < previous_effective_end - 0.001:
            raise RuntimeError(
                f"Реплика #{segment_id} пересекается после применения delay."
            )
        if effective_end > duration_value + 0.02:
            raise RuntimeError(f"Реплика #{segment_id} выходит за конец видео.")
        if end - start > MAX_SECONDS + 0.30:
            raise RuntimeError(
                f"Реплика #{segment_id} слишком длинная: {end - start:.3f} сек."
            )
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        rate = words / max(0.35, end - start)
        if rate > 6.2:
            raise RuntimeError(
                f"Реплика #{segment_id} физически перегружена: {rate:.2f} слова/с."
            )
        item["start"] = start
        item["end"] = end
        item["text"] = text
        previous_end = end
        previous_effective_end = effective_end


# Legacy functions resolve these names at call time.
_legacy._finite = _finite
_legacy._mark_and_validate_segments = _mark_and_validate_segments

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"_finite", "_mark_and_validate_segments", "_strict_int"}
)
