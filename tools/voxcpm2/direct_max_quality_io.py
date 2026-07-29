#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single direct VoxCPM2 CPU renderer for maximum-quality short-form dubbing.

The Telegram bot and manual PowerShell runs invoke this exact CLI. It does not
patch VoxCPM internals and does not install runtime wrappers. Quality is gained
through transparent reference preparation, multiple deterministic candidates,
voice/prosody/timbre diagnostics, durable checkpoints and downstream QA.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

POLICY = "voxcpm2-direct-max-quality-v3"
EXPECTED_ENCODE_SR = 16000
EXPECTED_OUTPUT_SR = 48000
REFERENCE_TAIL_SILENCE = 0.0
MAX_TEMPO = 1.35
MAX_START_DELAY_MS = 1500


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(message, flush=True)


def run_checked(
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        tail = (process.stderr or process.stdout or "")[-6000:] if capture else ""
        raise RuntimeError(
            "Команда завершилась с ошибкой:\n"
            + " ".join(command)
            + ("\n\n" + tail if tail else "")
        )
    return process


def probe_duration(path: Path) -> float:
    process = run_checked(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture=True,
    )
    value = float((process.stdout or "").strip())
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"Некорректная длительность: {path}: {value!r}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atempo_chain(factor: float) -> list[str]:
    if factor <= 0:
        raise ValueError("atempo factor должен быть > 0")
    result: list[str] = []
    remaining = float(factor)
    while remaining < 0.5:
        result.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        result.append("atempo=2.0")
        remaining /= 2.0
    result.append(f"atempo={remaining:.8f}")
    return result


def looks_like_model_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "config.json").is_file()
        and (
            (path / "model.safetensors").is_file()
            or any(path.glob("*.safetensors"))
            or any(path.glob("*.bin"))
        )
    )


def newest_snapshot(path: Path) -> Path | None:
    snapshots = path / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [item for item in snapshots.iterdir() if looks_like_model_dir(item)]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def discover_model(archive_root: Path) -> Path:
    candidates = [
        archive_root / "models" / "voxcpm2-model-cache" / "models--openbmb--VoxCPM2",
        archive_root / "models" / "voxcpm2-model-cache" / "models--OpenBMB--VoxCPM2",
    ]
    for candidate in candidates:
        if looks_like_model_dir(candidate):
            return candidate
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot
    for candidate in archive_root.rglob("models--openbmb--VoxCPM2"):
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot
    raise RuntimeError("Локальный snapshot VoxCPM2 не найден.")


def _finite_float(value: Any, *, field: str, segment_id: int) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Сегмент #{segment_id}: некорректное значение {field}={value!r}."
        ) from exc
    if not math.isfinite(result):
        raise RuntimeError(
            f"Сегмент #{segment_id}: {field} должен быть конечным числом."
        )
    return result


def read_segments(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments JSON должен содержать непустой список.")
    result: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    previous_end = 0.0
    previous_effective_end = 0.0
    for index, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Сегмент #{index} должен быть JSON-объектом.")
        item = dict(raw)
        try:
            segment_id = int(item.get("id", index))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"Некорректный ID сегмента #{index}.") from exc
        if segment_id <= 0:
            raise RuntimeError(f"ID сегмента должен быть положительным: {segment_id}.")
        if segment_id in seen_ids:
            raise RuntimeError(f"Повторяющийся ID сегмента: {segment_id}.")
        seen_ids.add(segment_id)
        start = _finite_float(item.get("start"), field="start", segment_id=segment_id)
        end = _finite_float(item.get("end"), field="end", segment_id=segment_id)
        tail_guard = _finite_float(
            item.get("tail_guard", 0.22),
            field="tail_guard",
            segment_id=segment_id,
        )
        try:
            start_delay_ms = int(item.get("start_delay_ms", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(
                f"Сегмент #{segment_id}: некорректный start_delay_ms."
            ) from exc
        item["id"] = segment_id
        item["start"] = start
        item["end"] = end
        item["text"] = str(item.get("text") or "").strip()
        item["tail_guard"] = tail_guard
        item["start_delay_ms"] = start_delay_ms
        item["reference_profile"] = str(item.get("reference_profile", "extended"))
        if start < 0.0:
            raise RuntimeError(f"Сегмент #{segment_id}: start не может быть отрицательным.")
        if end <= start:
            raise RuntimeError(f"Некорректный сегмент #{segment_id}.")
        if tail_guard < 0.0:
            raise RuntimeError(
                f"Сегмент #{segment_id}: tail_guard не может быть отрицательным."
            )
        if not 0 <= start_delay_ms <= MAX_START_DELAY_MS:
            raise RuntimeError(
                f"Сегмент #{segment_id}: start_delay_ms должен быть в диапазоне "
                f"0..{MAX_START_DELAY_MS}."
            )
        if start < previous_end - 0.001:
            raise RuntimeError(f"Пересечение у сегмента #{segment_id}.")
        effective_start = start + start_delay_ms / 1000.0
        effective_end = end + start_delay_ms / 1000.0
        if effective_start < previous_effective_end - 0.001:
            raise RuntimeError(
                f"Эффективное пересечение после delay у сегмента #{segment_id}."
            )
        if not item["text"]:
            raise RuntimeError(f"Пустой текст #{segment_id}.")
        if item["reference_profile"] not in {"extended", "composite"}:
            raise RuntimeError(f"Неизвестный reference_profile у #{segment_id}.")
        result.append(item)
        previous_end = end
        previous_effective_end = effective_end
    return result


def set_seed(seed: int, torch_module: Any) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch_module.manual_seed(seed)
