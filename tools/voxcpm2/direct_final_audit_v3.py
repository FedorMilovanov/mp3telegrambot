#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final fail-closed audit layer for every direct VoxCPM2 CLI invocation.

This layer closes gaps that can exist between raw JSON input, the already
normalised renderer state and the outer bot preflight. It deliberately wraps
only public direct-CLI boundaries and keeps the previously audited base
implementations unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable


POLICY = "voxcpm2-final-audit-v3"
MAX_SEGMENTS_BYTES = 8 * 1024 * 1024
MAX_ARCHIVED_MARKERS = 8

_MODULE_SHA256 = ""


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{name} не может быть bool.")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise RuntimeError(f"{name} должен быть целым числом.")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {name}: {value!r}") from exc


def _raw_segments(path: Path) -> list[dict[str, Any]]:
    source = Path(path).resolve()
    try:
        if not source.is_file():
            raise RuntimeError(f"Не найден segments JSON: {source}")
        if source.stat().st_size > MAX_SEGMENTS_BYTES:
            raise RuntimeError(f"segments JSON слишком велик: {source}")
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Повреждён segments JSON: {source}") from exc
    except OSError as exc:
        raise RuntimeError(f"Не удалось прочитать segments JSON: {source}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments JSON должен содержать непустой список.")

    result: list[dict[str, Any]] = []
    for position, raw in enumerate(payload, 1):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"Сегмент #{position} должен быть JSON-объектом.")
        item = dict(raw)
        if "id" in item:
            _integer(item["id"], f"segment[{position}].id")
        for field in ("start", "end", "tail_guard"):
            if isinstance(item.get(field), bool):
                raise RuntimeError(f"segment[{position}].{field} не может быть bool.")
        if "start_delay_ms" in item:
            _integer(item["start_delay_ms"], f"segment[{position}].start_delay_ms")
        result.append(item)
    return result


def _module_sha256(hash_file: Callable[[Path], str]) -> str:
    global _MODULE_SHA256
    if not _MODULE_SHA256:
        value = str(hash_file(Path(__file__).resolve())).strip().casefold()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise RuntimeError("Некорректный SHA final-audit module.")
        _MODULE_SHA256 = value
    return _MODULE_SHA256


def _model_context(
    model_path: Path,
    hash_file: Callable[[Path], str],
) -> dict[str, Any]:
    model = Path(model_path).resolve()
    config = model / "config.json"
    config_sha = str(hash_file(config)).strip().casefold() if config.is_file() else ""
    files: list[dict[str, Any]] = []
    for pattern in ("*.safetensors", "*.bin", "*.json"):
        for item in sorted(model.glob(pattern), key=lambda value: value.name.casefold()):
            try:
                stat = item.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": item.name,
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
    encoded = json.dumps(
        {
            "path": str(model),
            "config_sha256": config_sha,
            "files": files,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "direct_model_path": str(model),
        "direct_model_snapshot": model.name,
        "direct_model_config_sha256": config_sha,
        "direct_model_snapshot_fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


__all__ = ["MAX_SEGMENTS_BYTES", "POLICY"]
