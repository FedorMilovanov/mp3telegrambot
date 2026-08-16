#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable per-segment seed epochs for deterministic VoxCPM recovery.

A failed segment must not regenerate the same five deterministic candidates on
every job retry. This module advances only that segment's seed epoch after a raw,
assembled or post-AAC delivery failure. Successful segment checkpoints keep
their original epoch and remain reusable during hour-long renders.
"""
from __future__ import annotations
from collections.abc import Mapping

from tools.voxcpm2 import direct_surgical_polish_v2 as polish
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from numbers import Integral
from typing import Any

POLICY = "failed-segment-seed-epoch-v1"
# Epoch namespaces remain disjoint for every supported segment id. One billion
# segment IDs is many orders of magnitude above an hour-long project while still
# leaving a wide, explicit namespace between adjacent epochs.
SEED_EPOCH_STRIDE = 1_000_000_000_000
MAX_SEGMENT_ID = 1_000_000_000
MAX_RETRY_EPOCH = 100_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strict_segment_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise RuntimeError(f"Некорректный segment_id: {value!r}")
    result = int(value)
    if not 1 <= result <= MAX_SEGMENT_ID:
        raise RuntimeError(
            f"segment_id должен быть в диапазоне 1..{MAX_SEGMENT_ID}: {result}."
        )
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
    if isinstance(base_seed, bool) or isinstance(attempt, bool) or isinstance(epoch, bool):
        raise RuntimeError("base_seed/attempt/epoch не могут быть bool.")
    try:
        base = int(base_seed)
        attempt_value = int(attempt)
        epoch_value = int(epoch)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Некорректный base_seed/attempt/epoch.") from exc
    segment = _strict_segment_id(segment_id)
    if base < 0 or attempt_value <= 0:
        raise RuntimeError("base_seed/attempt вне допустимого диапазона.")
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


def _polish_base_invalidate_segment_for_retry(
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

BASE_POLICY = POLICY
_base_load_retry_epoch = load_retry_epoch
_base_advance_retry_epoch = advance_retry_epoch

POLICY = "failed-segment-seed-epoch-scope-v2"
MAX_SCOPE_EPOCH = 3
_SCOPE_EPOCHS_KEY = "scope_epochs"
_SCOPE_FINGERPRINT_KEY = "failure_scope_fingerprint"


def _scope_fingerprint(evidence: Mapping[str, Any] | None) -> str:
    if not isinstance(evidence, Mapping):
        return ""
    value = str(evidence.get(_SCOPE_FINGERPRINT_KEY) or "").strip().lower()
    if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
        return value
    return ""


def _scope_epochs(payload: Mapping[str, Any]) -> dict[str, int]:
    return polish._scope_epochs(payload)


def load_retry_epoch(
    work_dir: Path,
    segment_id: Any,
    *,
    scope_fingerprint: str | None = None,
) -> int:
    """Return the raw epoch for legacy callers or exact-scope epoch for TTS."""
    segment = _strict_segment_id(segment_id)
    if not scope_fingerprint:
        return int(_base_load_retry_epoch(work_dir, segment))
    fingerprint = str(scope_fingerprint).strip().lower()
    if len(fingerprint) != 64 or any(
        char not in "0123456789abcdef" for char in fingerprint
    ):
        raise RuntimeError("scope_fingerprint должен быть SHA-256 hex.")
    payload = _read_payload(retry_epoch_path(work_dir, segment))
    return int(_scope_epochs(payload).get(fingerprint, 0))


def advance_retry_epoch(
    work_dir: Path,
    segment_id: Any,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance global history and, when present, one exact synthesis scope."""
    segment = _strict_segment_id(segment_id)
    path = retry_epoch_path(work_dir, segment)
    payload_before = _read_payload(path)
    raw_previous = int(_base_load_retry_epoch(work_dir, segment))
    if raw_previous >= MAX_RETRY_EPOCH:
        raise RuntimeError(
            f"Сегмент #{segment}: исчерпан raw retry epoch {MAX_RETRY_EPOCH}."
        )

    evidence_payload = dict(evidence or {})
    fingerprint = _scope_fingerprint(evidence_payload)
    scope_epochs = _scope_epochs(payload_before)
    scope_previous = int(scope_epochs.get(fingerprint, 0)) if fingerprint else 0
    if fingerprint and scope_previous >= MAX_SCOPE_EPOCH:
        raise RuntimeError(
            f"Сегмент #{segment}: исчерпан безопасный retry-бюджет "
            f"для точного входа ({MAX_SCOPE_EPOCH} failed epochs). "
            "Измените текст, тайминг, модель/профиль или reference; "
            "новые seed для того же входа больше не запускаются."
        )

    history = payload_before.get("history")
    if not isinstance(history, list):
        history = []
    created_at = _now()
    scope_next = scope_previous + 1 if fingerprint else None
    entry = {
        "from_epoch": raw_previous,
        "to_epoch": raw_previous + 1,
        "scope_epoch_from": scope_previous if fingerprint else None,
        "scope_epoch_to": scope_next,
        "reason": str(reason or "delivery_failure")[:240],
        "created_at": created_at,
        "evidence": evidence_payload,
    }
    history = [*history[-63:], entry]
    if fingerprint and scope_next is not None:
        scope_epochs[fingerprint] = scope_next
        if len(scope_epochs) > 64:
            referenced = {
                _scope_fingerprint(item.get("evidence"))
                for item in history
                if isinstance(item, Mapping)
            }
            scope_epochs = {
                key: value
                for key, value in scope_epochs.items()
                if key in referenced
            }

    payload = {
        "schema_version": 2,
        "policy": POLICY,
        "base_policy": BASE_POLICY,
        "segment_id": segment,
        "epoch": raw_previous + 1,
        "seed_stride": SEED_EPOCH_STRIDE,
        "updated_at": created_at,
        "last_reason": entry["reason"],
        "last_scope_fingerprint": fingerprint or None,
        "last_scope_epoch": scope_next,
        _SCOPE_EPOCHS_KEY: scope_epochs,
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
    evidence_payload = dict(evidence or {})
    fingerprint = polish._sha(evidence_payload.get("failure_scope_fingerprint"))
    result = dict(
        _polish_base_invalidate_segment_for_retry(
            work_dir,
            segment,
            reason=reason,
            fitted_path=fitted_path,
            evidence=evidence_payload,
        )
    )
    result["raw_retry_epoch"] = int(result.get("retry_epoch") or 0)
    if fingerprint:
        epoch = load_retry_epoch(
            work_dir,
            segment.get("id"),
            scope_fingerprint=fingerprint,
        )
        result.update(
            retry_epoch=epoch,
            scope_retry_epoch=epoch,
            last_scope_epoch=epoch,
            scope_fingerprint=fingerprint,
            policy=polish.POLICY,
        )
    return result


__all__ = [
    "BASE_POLICY",
    "MAX_RETRY_EPOCH",
    "MAX_SCOPE_EPOCH",
    "MAX_SEGMENT_ID",
    "POLICY",
    "SEED_EPOCH_STRIDE",
    "advance_retry_epoch",
    "invalidate_segment_for_retry",
    "load_retry_epoch",
    "retry_epoch_path",
    "seed_for_attempt",
]
