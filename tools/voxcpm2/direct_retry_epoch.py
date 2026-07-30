#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable per-segment seed epochs for deterministic VoxCPM recovery.

A failed segment must not regenerate the same five deterministic candidates on
every job retry. This module advances only that segment's seed epoch after a raw,
assembled or post-AAC delivery failure. Successful segment checkpoints keep
their original epoch and remain reusable during hour-long renders.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLICY = "failed-segment-seed-epoch-v1"
# Keep epoch namespaces disjoint even for extremely long projects. The old
# 100,000 stride collided with epoch 1 / segment 1 and epoch 0 / segment 1001.
SEED_EPOCH_STRIDE = 1_000_000_000_000
MAX_RETRY_EPOCH = 100_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strict_segment_id(value: Any) -> int:
    if isinstance(value, bool):
        raise RuntimeError("segment_id не может быть bool.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректный segment_id: {value!r}") from exc
    if result <= 0:
        raise RuntimeError(f"segment_id должен быть положительным: {result}.")
    return result


def retry_epoch_path(work_dir: Path, segment_id: Any) -> Path:
    segment = _strict_segment_id(segment_id)
    return Path(work_dir).resolve() / "retry_epochs" / f"segment_{segment:02d}.json"


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён retry epoch: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Retry epoch должен быть JSON-объектом: {path}")
    return payload


def load_retry_epoch(work_dir: Path, segment_id: Any) -> int:
    path = retry_epoch_path(work_dir, segment_id)
    payload = _read_payload(path)
    value = payload.get("epoch", 0)
    if isinstance(value, bool):
        raise RuntimeError(f"Retry epoch не может быть bool: {path}")
    try:
        epoch = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректный retry epoch в {path}: {value!r}") from exc
    if not 0 <= epoch <= MAX_RETRY_EPOCH:
        raise RuntimeError(f"Retry epoch вне диапазона 0..{MAX_RETRY_EPOCH}: {path}")
    return epoch


def seed_for_attempt(
    base_seed: Any,
    segment_id: Any,
    attempt: Any,
    epoch: Any,
) -> int:
    values: list[int] = []
    for name, value in (
        ("base_seed", base_seed),
        ("segment_id", segment_id),
        ("attempt", attempt),
        ("epoch", epoch),
    ):
        if isinstance(value, bool):
            raise RuntimeError(f"{name} не может быть bool.")
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"Некорректный {name}: {value!r}") from exc
        values.append(number)
    base, segment, attempt_value, epoch_value = values
    if base < 0 or segment <= 0 or attempt_value <= 0:
        raise RuntimeError("base_seed/segment_id/attempt вне допустимого диапазона.")
    if not 0 <= epoch_value <= MAX_RETRY_EPOCH:
        raise RuntimeError(f"epoch вне диапазона 0..{MAX_RETRY_EPOCH}.")
    seed = base + segment * 100 + attempt_value + epoch_value * SEED_EPOCH_STRIDE
    if seed > 2**63 - 1:
        raise RuntimeError("Вычисленный VoxCPM seed переполнил signed int64.")
    return seed


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def advance_retry_epoch(
    work_dir: Path,
    segment_id: Any,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    segment = _strict_segment_id(segment_id)
    path = retry_epoch_path(work_dir, segment)
    previous = load_retry_epoch(work_dir, segment)
    if previous >= MAX_RETRY_EPOCH:
        raise RuntimeError(
            f"Сегмент #{segment}: исчерпан retry epoch {MAX_RETRY_EPOCH}."
        )
    history_payload = _read_payload(path)
    history = history_payload.get("history")
    if not isinstance(history, list):
        history = []
    entry = {
        "from_epoch": previous,
        "to_epoch": previous + 1,
        "reason": str(reason or "delivery_failure")[:240],
        "created_at": _now(),
        "evidence": dict(evidence or {}),
    }
    history = [*history[-31:], entry]
    payload = {
        "schema_version": 1,
        "policy": POLICY,
        "segment_id": segment,
        "epoch": previous + 1,
        "seed_stride": SEED_EPOCH_STRIDE,
        "updated_at": entry["created_at"],
        "last_reason": entry["reason"],
        "history": history,
    }
    _atomic_write(path, payload)
    return payload


def invalidate_segment_for_retry(
    work_dir: Path,
    segment: dict[str, Any],
    *,
    reason: str,
    fitted_path: Path | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    segment_id = _strict_segment_id(segment.get("id"))
    root = Path(work_dir).resolve()
    profile = str(segment.get("reference_profile") or "extended")
    fitted = (
        Path(fitted_path).resolve()
        if fitted_path is not None
        else root / "segments_fitted" / f"{segment_id:02d}_{profile}_fitted.wav"
    )
    checkpoint = root / "checkpoints" / f"segment_{segment_id:02d}.json"
    clean = root / "segments_clean" / f"{segment_id:02d}_{profile}_clean.wav"
    fitted.unlink(missing_ok=True)
    checkpoint.unlink(missing_ok=True)
    clean.unlink(missing_ok=True)
    epoch = advance_retry_epoch(
        root,
        segment_id,
        reason=reason,
        evidence=evidence,
    )
    return {
        "policy": POLICY,
        "id": segment_id,
        "fitted": str(fitted),
        "checkpoint": str(checkpoint),
        "clean": str(clean),
        "retry_epoch": int(epoch["epoch"]),
        "retry_epoch_path": str(retry_epoch_path(root, segment_id)),
        "reason": str(reason or "delivery_failure"),
    }


__all__ = [
    "MAX_RETRY_EPOCH",
    "POLICY",
    "SEED_EPOCH_STRIDE",
    "advance_retry_epoch",
    "invalidate_segment_for_retry",
    "load_retry_epoch",
    "retry_epoch_path",
    "seed_for_attempt",
]
