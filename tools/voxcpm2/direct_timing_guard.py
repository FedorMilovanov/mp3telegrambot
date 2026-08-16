#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal fail-fast timing, retry-scope and candidate-budget contracts."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLICY = "voxcpm2-direct-timing-guard-v2"
PREFLIGHT_POLICY = "voxcpm2-russian-duration-preflight-v1"
REPORT_NAME = "timing_preflight.json"
CONTEXT_NAME = "timing_context.json"
MARKER_SCHEMA_VERSION = 1
MAX_TEMPO_DEFAULT = 1.36
MAX_SYNTHESIS_EPOCHS_PER_SCOPE = 3
MAX_TOTAL_CANDIDATES_PER_SCOPE = 13
MAX_CONTEXT_BYTES = 262_144

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_RU_VOWELS = re.compile(r"[аеёиоуыэюя]", re.I)
_LATIN_VOWELS = re.compile(r"[aeiouy]+", re.I)
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?![\w])")
_PAUSE_RE = re.compile(r"[,;:—–]")
_END_RE = re.compile(r"[.!?…]")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value or {}), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Timing context должен быть JSON-safe.") from exc
    if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise RuntimeError("Timing context превышает безопасный размер.")
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise RuntimeError("Timing context должен быть JSON-объектом.")
    return payload


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


def _speech_slot(segment: Mapping[str, Any]) -> float:
    start = _finite(segment.get("start"), float("nan"))
    end = _finite(segment.get("end"), float("nan"))
    tail = _finite(segment.get("tail_guard"), float("nan"))
    if not all(math.isfinite(value) for value in (start, end, tail)):
        raise RuntimeError(f"Сегмент #{segment.get('id')}: тайминг должен быть конечным.")
    slot = end - start - tail
    if start < 0.0 or end <= start or tail < 0.0 or slot <= 0.0:
        raise RuntimeError(
            f"Сегмент #{segment.get('id')}: некорректное речевое окно "
            f"start={start:.3f}, end={end:.3f}, tail={tail:.3f}."
        )
    return slot


def _numeric_cost(text: str) -> tuple[int, int, int]:
    words = syllables = letters = 0
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0).lstrip("+-").replace(",", ".")
        integer, dot, fraction = raw.partition(".")
        digits = max(1, len(integer.lstrip("0") or "0"))
        spoken = 1 if digits <= 2 else 2 if digits == 3 else max(3, math.ceil(digits / 3) * 2)
        if dot:
            spoken += 1 + max(1, len(fraction))
        words += max(0, spoken - 1)
        syllables += spoken * 3
        letters += spoken * 6
    return words, syllables, letters


def text_metrics(text: str, speech_slot: float, *, max_tempo: float) -> dict[str, Any]:
    normalized = _normalise(text)
    slot = max(0.001, float(speech_slot))
    words = len(_WORD_RE.findall(normalized))
    letters = len(_LETTER_RE.findall(normalized))
    syllables = len(_RU_VOWELS.findall(normalized)) + len(_LATIN_VOWELS.findall(normalized))
    add_words, add_syllables, add_letters = _numeric_cost(normalized)
    words += add_words
    syllables += add_syllables
    letters += add_letters
    if words and not syllables:
        syllables = words
    natural = max(words / 2.55, syllables / 5.40, letters / 13.50)
    natural += len(_PAUSE_RE.findall(normalized)) * 0.08
    natural += len(_END_RE.findall(normalized)) * 0.12
    tempo = natural / slot
    wps = words / slot
    sps = syllables / slot
    lps = letters / slot
    hard = max(1.01, float(max_tempo))
    high_density = wps >= 3.0 or sps >= 6.2 or lps >= 18.0
    critical = bool(
        words >= 6
        and (
            tempo >= hard + 0.24
            or (words >= 8 and tempo >= hard + 0.12 and high_density)
        )
    )
    warning = critical or tempo >= hard - 0.05 or sps >= 5.8
    return {
        "text": normalized,
        "speech_slot": round(slot, 6),
        "words": words,
        "letters": letters,
        "estimated_syllables": syllables,
        "words_per_second": round(wps, 6),
        "syllables_per_second": round(sps, 6),
        "estimated_natural_seconds": round(natural, 6),
        "estimated_required_tempo": round(tempo, 6),
        "risk_score": round(max(tempo / hard, wps / 3.0, sps / 6.2, lps / 18.0), 6),
        "hard_minimum_speech_slot": round(natural / hard, 3),
        "warning": bool(warning),
        "critical": bool(critical),
    }


def candidate_efficiency_plan(
    segment: Mapping[str, Any], *, speech_slot: float,
    retry_epoch: int, max_tempo: float,
) -> dict[str, Any]:
    metrics = text_metrics(str(segment.get("text") or ""), speech_slot, max_tempo=max_tempo)
    tempo = float(metrics["estimated_required_tempo"])
    risk = float(metrics["risk_score"])
    if tempo <= 1.14 and risk <= 1.18 and not metrics["warning"]:
        band, attempts = ("green" if tempo <= 0.96 and risk <= 1.0 else "balanced"), 3
    else:
        band, attempts = "guarded", (5 if int(retry_epoch) == 0 else 4)
    return {
        "policy": "voxcpm2-adaptive-candidate-budget-v1",
        "risk_band": band,
        "retry_epoch": int(retry_epoch),
        "max_attempts": attempts,
        "estimated_required_tempo": tempo,
        "risk_score": risk,
        "static_metrics": metrics,
    }


def write_signature_context(work_dir: Path, context: Mapping[str, Any]) -> Path:
    path = Path(work_dir).resolve() / CONTEXT_NAME
    _atomic_json(path, _json_object(context))
    return path


def load_signature_context(work_dir: Path) -> dict[str, Any]:
    path = Path(work_dir).resolve() / CONTEXT_NAME
    if not path.is_file():
        return {}
    if path.stat().st_size > MAX_CONTEXT_BYTES:
        raise RuntimeError(f"Timing context слишком велик: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён timing context: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Timing context должен быть JSON-объектом: {path}")
    return _json_object(payload)


def failure_scope_fingerprint(
    segment: Mapping[str, Any], *, signature_context: Mapping[str, Any] | None,
) -> str:
    payload = {
        "policy": POLICY,
        "segment_id": int(segment.get("id") or 0),
        "text": _normalise(segment.get("text")),
        "start": round(_finite(segment.get("start")), 6),
        "end": round(_finite(segment.get("end")), 6),
        "tail_guard": round(_finite(segment.get("tail_guard")), 6),
        "reference_profile": str(segment.get("reference_profile") or ""),
        "expression": {
            "tier": str(segment.get("expression_tier") or ""),
            "style": str(segment.get("style_instruction") or ""),
        },
        "context": _json_object(signature_context),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _retry_history(work_dir: Path, segment_id: int) -> list[Mapping[str, Any]]:
    path = Path(work_dir).resolve() / "retry_epochs" / f"segment_{segment_id:02d}.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Повреждён retry history: {path}") from exc
    history = payload.get("history") if isinstance(payload, dict) else None
    return [item for item in history or [] if isinstance(item, Mapping)]


def _base_enforce_retry_epoch_budget(
    *, work_dir: Path, segment: Mapping[str, Any], retry_epoch: int,
    signature_context: Mapping[str, Any] | None,
) -> None:
    segment_id = int(segment.get("id") or 0)
    scope = failure_scope_fingerprint(segment, signature_context=signature_context)
    matching = []
    for entry in _retry_history(work_dir, segment_id):
        evidence = entry.get("evidence")
        if (
            str(entry.get("reason") or "") == "raw_candidate_hard_failure"
            and isinstance(evidence, Mapping)
            and str(evidence.get("failure_scope_fingerprint") or "") == scope
        ):
            matching.append(entry)
    if len(matching) >= MAX_SYNTHESIS_EPOCHS_PER_SCOPE:
        raise RuntimeError(
            f"Сегмент #{segment_id}: для точной версии текста, модели и reference "
            f"исчерпаны {MAX_SYNTHESIS_EPOCHS_PER_SCOPE} seed epoch "
            f"(до {MAX_TOTAL_CANDIDATES_PER_SCOPE} кандидатов). "
            "Измените вход; старые неудачи другого текста не учитываются."
        )


def _candidate_snapshot(candidate: Mapping[str, Any], slot: float) -> dict[str, Any]:
    duration = _finite(candidate.get("duration"))
    tempo = _finite(candidate.get("required_tempo")) or duration / max(0.001, slot)
    return {
        "attempt": int(candidate.get("attempt") or 0),
        "seed": int(candidate.get("seed") or 0),
        "duration": round(duration, 6),
        "required_tempo": round(tempo, 6),
        "score": round(_finite(candidate.get("score")), 6),
        "cadence_failures": list((candidate.get("cadence_evidence") or {}).get("failures") or []),
    }


def evaluate_dynamic_timing_failure(
    candidates: list[dict[str, Any]], *, segment: Mapping[str, Any],
    speech_slot: float, retry_epoch: int, max_tempo: float,
) -> dict[str, Any] | None:
    if len(candidates) < 2:
        return None
    metrics = text_metrics(str(segment.get("text") or ""), speech_slot, max_tempo=max_tempo)
    required = 2 if metrics["critical"] or metrics["warning"] else 3
    if len(candidates) < required:
        return None
    snapshots = [_candidate_snapshot(item, speech_slot) for item in candidates[-required:]]
    attempts = [item["attempt"] for item in snapshots]
    seeds = [item["seed"] for item in snapshots]
    if any(value <= 0 for value in (*attempts, *seeds)):
        return None
    if len(set(attempts)) != len(attempts) or len(set(seeds)) != len(seeds):
        return None
    tempos = [float(item["required_tempo"]) for item in snapshots]
    margin = 0.08 if int(retry_epoch) else 0.12
    median_margin = 0.12 if int(retry_epoch) else 0.16
    if min(tempos) < float(max_tempo) + margin:
        return None
    if statistics.median(tempos) < float(max_tempo) + median_margin:
        return None
    return {
        "kind": "independent-severe-duration-misses",
        "attempts": snapshots,
        "speech_slot": round(float(speech_slot), 6),
        "retry_epoch": int(retry_epoch),
        "max_tempo": float(max_tempo),
        "static_metrics": metrics,
    }


def timing_block_path(work_dir: Path, segment_id: int) -> Path:
    return Path(work_dir).resolve() / "timing_blocks" / f"segment_{int(segment_id):02d}.json"


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
        "policy": SURGICAL_GUARD_POLICY,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "segment_id": segment_id,
        "signature": failure_scope_fingerprint(
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
    _atomic_json(timing_block_path(work_dir, segment_id), payload)
    return payload


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


def _base_run_pre_model_guard(
    segments: Iterable[dict[str, Any]], *, work_dir: Path,
    max_tempo: float, signature_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = [dict(item) for item in segments]
    if not values:
        raise RuntimeError("Timing preflight получил пустой список сегментов.")
    rows = []
    seen: set[int] = set()
    previous_end = -1.0
    for position, segment in enumerate(values, 1):
        segment_id = int(segment.get("id") or position)
        if segment_id <= 0 or segment_id in seen:
            raise RuntimeError(f"Некорректный или повторный ID сегмента: {segment_id}.")
        seen.add(segment_id)
        segment["id"] = segment_id
        start = _finite(segment.get("start"), float("nan"))
        end = _finite(segment.get("end"), float("nan"))
        if not math.isfinite(start) or not math.isfinite(end):
            raise RuntimeError(f"Сегмент #{segment_id}: неконечный тайминг.")
        if start < previous_end - 1e-6:
            raise RuntimeError(f"Сегмент #{segment_id}: перекрытие или неправильный порядок.")
        if not _normalise(segment.get("text")):
            raise RuntimeError(f"Сегмент #{segment_id}: пустой русский текст.")
        slot = _speech_slot(segment)
        stored = segment.get("speech_slot")
        if stored is not None and abs(_finite(stored, float("nan")) - slot) > 1e-6:
            raise RuntimeError(f"Сегмент #{segment_id}: сохранённый speech_slot не совпадает.")
        previous_end = end
        metrics = text_metrics(str(segment["text"]), slot, max_tempo=max_tempo)
        plan = candidate_efficiency_plan(
            segment, speech_slot=slot, retry_epoch=0, max_tempo=max_tempo,
        )
        rows.append({
            "id": segment_id, "start": start, "end": end,
            "tail_guard": _finite(segment.get("tail_guard")),
            **metrics,
            "candidate_plan": {key: value for key, value in plan.items() if key != "static_metrics"},
        })
    report = {
        "schema_version": 1,
        "policy": PREFLIGHT_POLICY,
        "max_tempo": float(max_tempo),
        "signature_context": _json_object(signature_context),
        "critical_ids": [row["id"] for row in rows if row["critical"]],
        "warning_ids": [row["id"] for row in rows if row["warning"]],
        "segments": rows,
    }
    _atomic_json(Path(work_dir).resolve() / REPORT_NAME, report)
    critical = [row for row in rows if row["critical"]]
    if critical:
        worst = max(critical, key=lambda row: float(row["risk_score"]))
        raise RuntimeError(
            f"Сегмент #{worst['id']} физически перегружен ещё до voice references "
            f"и модели: окно={worst['speech_slot']:.2f} сек., "
            f"естественная речь≈{worst['estimated_natural_seconds']:.2f} сек., "
            f"required tempo≈{worst['estimated_required_tempo']:.2f}× при лимите "
            f"{float(max_tempo):.2f}×. Текст: «{worst['text'][:360]}»."
        )
    return report



SURGICAL_GUARD_POLICY = "voxcpm2-surgical-timing-polish-v1"
MARKER_SCHEMA_VERSION = 2
MAX_SCOPE_EPOCHS = 3
MAX_MARKER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVED_MARKERS = 8


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


def _archive_timing_marker(path: Path, reason: str) -> None:
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


def run_pre_model_guard(
    segments: Iterable[dict[str, Any]],
    *,
    work_dir: Path,
    max_tempo: float,
    signature_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = _validate_segments(segments)
    report = _base_run_pre_model_guard(
        values,
        work_dir=work_dir,
        max_tempo=max_tempo,
        signature_context=signature_context,
    )
    if isinstance(report, dict):
        report["surgical_guard_policy"] = SURGICAL_GUARD_POLICY
        _atomic_json(Path(work_dir).resolve() / REPORT_NAME, report)
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
    _base_enforce_retry_epoch_budget(
        work_dir=work_dir,
        segment=segment,
        retry_epoch=retry_epoch,
        signature_context=signature_context,
    )

def load_matching_timing_block(
    work_dir: Path,
    *,
    segment: Mapping[str, Any],
    signature_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    path = timing_block_path(work_dir, int(segment.get("id") or 0))
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_MARKER_BYTES:
            _archive_timing_marker(path, "oversized")
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        _archive_timing_marker(path, "corrupt-json")
        return None
    if not isinstance(payload, dict) or (
        payload.get("schema_version") != MARKER_SCHEMA_VERSION
        or payload.get("policy") != SURGICAL_GUARD_POLICY
        or int(payload.get("segment_id") or 0) != int(segment.get("id") or 0)
        or not isinstance(payload.get("evidence"), dict)
        or not isinstance(payload.get("recommendation"), dict)
    ):
        _archive_timing_marker(path, "contract-mismatch")
        return None
    expected = failure_scope_fingerprint(
        segment, signature_context=signature_context
    )
    if payload.get("signature") == expected:
        return payload
    _archive_timing_marker(path, "input-changed")
    return None

__all__ = [
    "CONTEXT_NAME", "POLICY",
    "SURGICAL_GUARD_POLICY",
    "RetryableSynthesisFailure",
    "load_matching_timing_block", "PREFLIGHT_POLICY", "REPORT_NAME",
    "candidate_efficiency_plan", "enforce_retry_epoch_budget",
    "evaluate_dynamic_timing_failure", "failure_scope_fingerprint",
    "format_timing_block_message", "load_signature_context",
    "persist_timing_block", "run_pre_model_guard", "text_metrics",
    "timing_block_path", "write_signature_context",
]
