#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Surgical fail-closed additions for the universal direct timing guard."""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from collections.abc import Iterable, Mapping, MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.voxcpm2 import direct_timing_guard as guard

POLICY = "voxcpm2-surgical-timing-polish-v1"
MARKER_SCHEMA_VERSION = 2
MAX_SCOPE_EPOCHS = 3
MAX_MARKER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVED_MARKERS = 8
_INSTALLED = False


class RetryableSynthesisFailure(RuntimeError):
    """Early stop carrying explicit retry-state semantics."""

    def __init__(
        self,
        message: str,
        *,
        segment: Mapping[str, Any],
        evidence: Mapping[str, Any] | None,
        advance_retry: bool,
        failure_kind: str,
    ) -> None:
        super().__init__(str(message))
        self.segment = dict(segment)
        self.segment_id = int(self.segment.get("id") or 0)
        self.evidence = dict(evidence or {})
        self.advance_retry = bool(advance_retry)
        self.failure_kind = str(failure_kind or "synthesis_failure")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _marker_path(work_dir: Path, segment_id: int) -> Path:
    return Path(work_dir).resolve() / "timing_blocks" / f"segment_{segment_id:02d}.json"


def _archive(path: Path, reason: str) -> None:
    suffix = re.sub(r"[^a-z0-9_-]+", "-", reason.casefold()).strip("-")
    destination = path.with_suffix(
        path.suffix + f".stale-{suffix or 'unknown'}-{uuid.uuid4().hex[:8]}"
    )
    try:
        path.replace(destination)
    except OSError:
        path.unlink(missing_ok=True)
    archived = sorted(
        path.parent.glob(path.name + ".stale-*"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
        reverse=True,
    )
    for stale in archived[MAX_ARCHIVED_MARKERS:]:
        stale.unlink(missing_ok=True)


def _validate_segments(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(item) for item in values]
    if not result:
        raise RuntimeError("Timing preflight получил пустой список сегментов.")
    seen: set[int] = set()
    previous_end = -1.0
    for position, segment in enumerate(result, 1):
        segment_id = int(segment.get("id") or position)
        start = _finite(segment.get("start"), float("nan"))
        end = _finite(segment.get("end"), float("nan"))
        tail = _finite(segment.get("tail_guard"), float("nan"))
        if segment_id <= 0 or segment_id in seen:
            raise RuntimeError(f"Некорректный или повторный ID сегмента: {segment_id}.")
        if not all(math.isfinite(value) for value in (start, end, tail)):
            raise RuntimeError(f"Сегмент #{segment_id}: тайминг должен быть конечным.")
        if start < 0.0 or end <= start or tail < 0.0 or tail >= end - start:
            raise RuntimeError(f"Сегмент #{segment_id}: некорректное речевое окно.")
        if start < previous_end - 1e-6:
            raise RuntimeError(f"Сегмент #{segment_id}: перекрытие или неправильный порядок.")
        if not _normalise(segment.get("text")):
            raise RuntimeError(f"Сегмент #{segment_id}: пустой русский текст.")
        slot = end - start - tail
        stored = segment.get("speech_slot")
        if stored is not None and abs(_finite(stored, float("nan")) - slot) > 1e-6:
            raise RuntimeError(f"Сегмент #{segment_id}: сохранённый speech_slot не совпадает.")
        segment["id"] = segment_id
        seen.add(segment_id)
        previous_end = end
    return result


def install_guard_contract() -> None:
    """Patch the shared guard module once, preserving its public import path."""
    global _INSTALLED
    if _INSTALLED:
        return
    original_preflight = guard.run_pre_model_guard
    original_budget = guard.enforce_retry_epoch_budget

    def run_pre_model_guard(
        segments: Iterable[dict[str, Any]],
        *,
        work_dir: Path,
        max_tempo: float,
        signature_context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        values = _validate_segments(segments)
        report = original_preflight(
            values,
            work_dir=work_dir,
            max_tempo=max_tempo,
            signature_context=signature_context,
        )
        if isinstance(report, dict):
            report["surgical_guard_policy"] = POLICY
            _atomic_json(Path(work_dir).resolve() / guard.REPORT_NAME, report)
        return report

    def enforce_retry_epoch_budget(
        *,
        work_dir: Path,
        segment: Mapping[str, Any],
        retry_epoch: int,
        signature_context: Mapping[str, Any] | None,
    ) -> None:
        if int(retry_epoch) >= MAX_SCOPE_EPOCHS:
            raise RuntimeError(
                f"Сегмент #{int(segment.get('id') or 0)}: исчерпаны "
                f"{MAX_SCOPE_EPOCHS} seed epoch для точного входа. "
                "Измените текст, тайминг, модель, профиль или reference."
            )
        original_budget(
            work_dir=work_dir,
            segment=segment,
            retry_epoch=retry_epoch,
            signature_context=signature_context,
        )

    def persist_timing_block(
        work_dir: Path,
        *,
        segment: Mapping[str, Any],
        signature_context: Mapping[str, Any] | None,
        retry_epoch: int,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        segment_id = int(segment.get("id") or 0)
        slot = _finite(segment.get("end")) - _finite(segment.get("start")) - _finite(
            segment.get("tail_guard")
        )
        attempts = [item for item in evidence.get("attempts") or [] if isinstance(item, Mapping)]
        durations = [_finite(item.get("duration")) for item in attempts if _finite(item.get("duration")) > 0]
        max_tempo = max(0.1, _finite(evidence.get("max_tempo"), 1.36))
        best = min(durations) if durations else 0.0
        hard_slot = best / max_tempo if best else slot
        payload = {
            "schema_version": MARKER_SCHEMA_VERSION,
            "policy": POLICY,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "segment_id": segment_id,
            "signature": guard.failure_scope_fingerprint(
                segment, signature_context=signature_context
            ),
            "text": _normalise(segment.get("text")),
            "speech_slot": round(slot, 6),
            "retry_epoch": int(retry_epoch),
            "evidence": dict(evidence),
            "recommendation": {
                "hard_minimum_speech_slot": round(max(slot, hard_slot), 3),
                "hard_shorten_percent": int(
                    math.ceil(max(0.0, 1.0 - slot / max(slot, hard_slot)) * 20.0) * 5
                ),
            },
        }
        _atomic_json(_marker_path(work_dir, segment_id), payload)
        return payload

    def load_matching_timing_block(
        work_dir: Path,
        *,
        segment: Mapping[str, Any],
        signature_context: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        path = _marker_path(work_dir, int(segment.get("id") or 0))
        if not path.is_file():
            return None
        try:
            if path.stat().st_size > MAX_MARKER_BYTES:
                _archive(path, "oversized")
                return None
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            _archive(path, "corrupt-json")
            return None
        if not isinstance(payload, dict) or (
            payload.get("schema_version") != MARKER_SCHEMA_VERSION
            or payload.get("policy") != POLICY
            or int(payload.get("segment_id") or 0) != int(segment.get("id") or 0)
            or not isinstance(payload.get("evidence"), dict)
            or not isinstance(payload.get("recommendation"), dict)
        ):
            _archive(path, "contract-mismatch")
            return None
        expected = guard.failure_scope_fingerprint(
            segment, signature_context=signature_context
        )
        if payload.get("signature") == expected:
            return payload
        _archive(path, "input-changed")
        return None

    def format_timing_block_message(block: Mapping[str, Any], *, repeated: bool) -> str:
        evidence = block.get("evidence") if isinstance(block.get("evidence"), Mapping) else {}
        attempts = [item for item in evidence.get("attempts") or [] if isinstance(item, Mapping)]
        tempos = [_finite(item.get("required_tempo")) for item in attempts if _finite(item.get("required_tempo")) > 0]
        tempo_text = f"{min(tempos):.2f}–{max(tempos):.2f}×" if tempos else "нет данных"
        note = (
            "Повтор не запущен и новый retry epoch не расходуется."
            if repeated
            else "Оставшиеся дорогие seed остановлены."
        )
        recommendation = block.get("recommendation") or {}
        return (
            f"Сегмент #{int(block.get('segment_id') or 0)} не помещается естественно: "
            f"окно={_finite(block.get('speech_slot')):.2f} сек., required tempo={tempo_text}. "
            f"{note} Сократите текст примерно на "
            f"{int(recommendation.get('hard_shorten_percent') or 0)}% или расширьте окно."
        )

    guard.POLICY = "voxcpm2-direct-timing-guard-v2"
    guard.MARKER_SCHEMA_VERSION = MARKER_SCHEMA_VERSION
    guard.RetryableSynthesisFailure = RetryableSynthesisFailure
    guard.run_pre_model_guard = run_pre_model_guard
    guard.enforce_retry_epoch_budget = enforce_retry_epoch_budget
    guard.persist_timing_block = persist_timing_block
    guard.load_matching_timing_block = load_matching_timing_block
    guard.format_timing_block_message = format_timing_block_message
    _INSTALLED = True


__all__ = ["POLICY", "RetryableSynthesisFailure", "install_guard_contract"]
