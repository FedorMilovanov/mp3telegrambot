#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scope-aware durable retry epochs for universal VoxCPM2 dubbing.

The original implementation is kept as an immutable base snapshot. This module
adds exact-input retry scopes so failures from an old SRT, model, profile or
reference cannot poison a newly edited job that happens to reuse a segment ID.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_direct_retry_epoch_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing direct retry base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._direct_retry_epoch_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME


def _required_export(name: str) -> Any:
    value = globals().get(name)
    if value is None:
        raise RuntimeError(f"Direct retry base export is missing: {name}")
    return value


def _required_callable(name: str) -> Callable[..., Any]:
    value = _required_export(name)
    if not callable(value):
        raise RuntimeError(f"Direct retry base export is not callable: {name}")
    return value


BASE_POLICY = str(_required_export("POLICY"))
MAX_RETRY_EPOCH = int(_required_export("MAX_RETRY_EPOCH"))
MAX_SEGMENT_ID = int(_required_export("MAX_SEGMENT_ID"))
SEED_EPOCH_STRIDE = int(_required_export("SEED_EPOCH_STRIDE"))
_strict_segment_id = _required_callable("_strict_segment_id")
_read_payload = _required_callable("_read_payload")
_now = _required_callable("_now")
_atomic_write = _required_callable("_atomic_write")
retry_epoch_path = _required_callable("retry_epoch_path")
seed_for_attempt = _required_callable("seed_for_attempt")
invalidate_segment_for_retry = _required_callable("invalidate_segment_for_retry")
_base_load_retry_epoch = _required_callable("load_retry_epoch")
_base_advance_retry_epoch = _required_callable("advance_retry_epoch")

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
    raw = payload.get(_SCOPE_EPOCHS_KEY)
    result: dict[str, int] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            fingerprint = str(key or "").strip().lower()
            if len(fingerprint) != 64:
                continue
            try:
                epoch = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 <= epoch <= MAX_SCOPE_EPOCH:
                result[fingerprint] = epoch
    explicit = set(result)
    history = payload.get("history")
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            fingerprint = _scope_fingerprint(entry.get("evidence"))
            if fingerprint and fingerprint not in explicit:
                result[fingerprint] = min(
                    MAX_SCOPE_EPOCH,
                    result.get(fingerprint, 0) + 1,
                )
    return result


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
