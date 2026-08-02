#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-install surgical runtime for universal direct VoxCPM2 jobs."""
from __future__ import annotations

import hashlib
import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Callable

from tools.voxcpm2 import direct_retry_epoch
from tools.voxcpm2 import direct_surgical_guard
from tools.voxcpm2 import direct_surgical_io
from tools.voxcpm2 import direct_timing_guard as guard

POLICY = "voxcpm2-surgical-runtime-v1"
_PROGRESS_POLICY = "candidate-aware-project-progress-v2"
_RUNTIME_SCOPE_FILES = (
    "tools/voxcpm2/direct_timing_guard.py",
    "tools/voxcpm2/direct_surgical_guard.py",
    "tools/voxcpm2/direct_universal_runtime.py",
    "tools/voxcpm2/direct_surgical_runtime.py",
    "tools/voxcpm2/direct_surgical_io.py",
    "tools/voxcpm2/direct_failure_recovery.py",
    "tools/voxcpm2/direct_retry_epoch.py",
    "tools/voxcpm2/direct_max_quality_cli.py",
    "tools/voxcpm2/direct_max_quality_cli/__init__.py",
    "services/speech_backends/voxcpm2.py",
    "services/speech_backends/audited_voxcpm2.py",
)


def _segments_by_id(values: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if segment_id > 0:
            result[segment_id] = item
    return result


def _progress_value(
    *, position: int, total: int, attempt: int, max_attempts: int,
) -> int:
    total_value = max(1, int(total))
    position_value = max(1, min(int(position), total_value))
    attempts_value = max(1, int(max_attempts))
    attempt_value = max(1, min(int(attempt), attempts_value))
    fraction = (position_value - 1) + (attempt_value - 1) / attempts_value
    return max(8, min(86, 8 + round(fraction / total_value * 78)))


def install_surgical_runtime(namespace: MutableMapping[str, Any]) -> None:
    """Install lazy loading, strict retry scope, cache and structured stops."""
    direct_surgical_guard.install_guard_contract()
    required = (
        "read_segments",
        "_build_generation_length_request",
        "_acceptable_candidates",
        "_raw_failure_evidence",
        "get_backend",
        "prepare_reference",
        "sha256_file",
        "MAX_TEMPO",
        "EXPECTED_ENCODE_SR",
        "EXPECTED_OUTPUT_SR",
        "log",
    )
    missing = [name for name in required if name not in namespace]
    if missing:
        raise RuntimeError("surgical direct contract missing: " + ", ".join(missing))

    original_read: Callable[..., Any] = namespace["read_segments"]
    original_build: Callable[..., Any] = namespace["_build_generation_length_request"]
    original_acceptable: Callable[..., Any] = namespace["_acceptable_candidates"]
    original_raw: Callable[..., Any] = namespace["_raw_failure_evidence"]
    original_get_backend: Callable[..., Any] = namespace["get_backend"]
    original_prepare: Callable[..., Any] = namespace["prepare_reference"]
    hash_file: Callable[[Path], str] = namespace["sha256_file"]
    original_log: Callable[[str], Any] = namespace["log"]
    max_tempo = float(namespace["MAX_TEMPO"])
    expected_encode = int(namespace["EXPECTED_ENCODE_SR"])
    expected_output = int(namespace["EXPECTED_OUTPUT_SR"])
    state: dict[str, Any] = {
        "segments": {},
        "work_dir": None,
        "retry_epochs": {},
        "current_segment_id": None,
        "runtime_context": None,
    }

    def log(message: str) -> Any:
        text = str(message)
        if text.startswith("Модель загружена за"):
            return original_log(
                "Модель работает лениво: checkpoint-only resume не открывает веса; "
                "загрузка начнётся перед первым отсутствующим сегментом."
            )
        return original_log(text)

    def get_backend(name: str) -> Any:
        backend = original_get_backend(name)
        if str(getattr(backend, "backend_id", "")).casefold() != "voxcpm2":
            return backend
        return direct_surgical_io.LazyBackend(
            backend,
            encode=expected_encode,
            output=expected_output,
            log=original_log,
        )

    def prepare_reference(source: Path, output: Path, sf_module: Any) -> dict[str, Any]:
        cached = direct_surgical_io.cached_reference(
            source=source,
            output=output,
            hash_file=hash_file,
            expected_sample_rate=expected_encode,
        )
        if cached is not None:
            original_log(
                f"Reference cache hit: {Path(output).stem} "
                f"({float(cached['duration']):.2f} сек.)"
            )
            return cached
        report = dict(original_prepare(source, output, sf_module))
        return direct_surgical_io.enrich_reference_report(
            report,
            source=source,
            hash_file=hash_file,
        )

    def read_segments(path: Path) -> list[dict[str, Any]]:
        values = list(original_read(Path(path)))
        state["segments"] = _segments_by_id(values)
        return values

    def _segment(segment_id: Any) -> dict[str, Any] | None:
        try:
            return state["segments"].get(int(segment_id))
        except (TypeError, ValueError, OverflowError):
            return None

    def _runtime_context() -> dict[str, str]:
        cached = state.get("runtime_context")
        if isinstance(cached, dict):
            return cached
        repo = Path(namespace["__file__"]).resolve().parents[2]
        hashes: dict[str, str] = {}
        for relative in _RUNTIME_SCOPE_FILES:
            path = repo / relative
            if not path.is_file():
                raise RuntimeError(f"Не найден runtime-файл для retry scope: {relative}")
            hashes[relative] = str(hash_file(path))
        encoded = json.dumps(
            hashes,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        result = {
            "surgical_runtime_policy": POLICY,
            "surgical_runtime_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        state["runtime_context"] = result
        return result

    def _scope(
        work_dir: Path,
        segment_id: Any,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        segment = _segment(segment_id)
        context = {
            **guard.load_signature_context(work_dir),
            **_runtime_context(),
        }
        if not isinstance(segment, dict):
            return None, context, ""
        profile = str(segment.get("reference_profile") or "extended")
        reference = Path(work_dir).resolve() / "references_guarded" / f"{profile}.wav"
        if reference.is_file():
            context.update(
                reference_profile=profile,
                reference_sha256=str(hash_file(reference)),
            )
        fingerprint = guard.failure_scope_fingerprint(
            segment,
            signature_context=context,
        )
        return segment, context, fingerprint

    def load_retry_epoch(work_dir: Path, segment_id: Any) -> int:
        work = Path(work_dir).resolve()
        state["work_dir"] = work
        segment, _context, scope = _scope(work, segment_id)
        value = int(
            direct_retry_epoch.load_retry_epoch(
                work,
                segment_id,
                scope_fingerprint=scope or None,
            )
        )
        key = int(segment_id)
        state["retry_epochs"][key] = value
        if isinstance(segment, dict):
            segment["retry_epoch"] = value
        return value

    def invalidate_segment_for_retry(
        work_dir: Path,
        segment: dict[str, Any],
        *,
        reason: str,
        fitted_path: Path | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _value, context, scope = _scope(work_dir, segment.get("id"))
        enriched = dict(evidence or {})
        enriched["failure_scope_fingerprint"] = scope or guard.failure_scope_fingerprint(
            segment,
            signature_context=context,
        )
        return direct_retry_epoch.invalidate_segment_for_retry(
            work_dir,
            segment,
            reason=reason,
            fitted_path=fitted_path,
            evidence=enriched,
        )

    def _position(segment_id: int) -> tuple[int, int]:
        ordered = sorted(int(value) for value in state["segments"])
        total = max(1, len(ordered))
        try:
            return ordered.index(int(segment_id)) + 1, total
        except ValueError:
            return min(max(1, int(segment_id)), total), total

    def build_generation_length_request(
        segment: dict[str, Any],
        *,
        duration_budget: float,
        attempt: int,
        previous_output_durations: tuple[float, ...],
    ) -> Any:
        segment_id = int(segment.get("id") or 0)
        state["current_segment_id"] = segment_id
        epoch = int(state["retry_epochs"].get(segment_id, segment.get("retry_epoch") or 0))
        work = state.get("work_dir")
        context: dict[str, Any] = {}
        if work is not None:
            _value, context, _scope_hash = _scope(Path(work), segment_id)
            if int(attempt) == 1:
                guard.enforce_retry_epoch_budget(
                    work_dir=Path(work),
                    segment=segment,
                    retry_epoch=epoch,
                    signature_context=context,
                )
                marker = guard.load_matching_timing_block(
                    Path(work),
                    segment=segment,
                    signature_context=context,
                )
                if marker is not None:
                    raise guard.RetryableSynthesisFailure(
                        guard.format_timing_block_message(marker, repeated=True),
                        segment=segment,
                        evidence=marker.get("evidence") or {},
                        advance_retry=False,
                        failure_kind="unchanged_timing_block",
                    )
        plan = guard.candidate_efficiency_plan(
            segment,
            speech_slot=max(0.001, float(duration_budget)),
            retry_epoch=epoch,
            max_tempo=max_tempo,
        )
        position, total = _position(segment_id)
        original_log(
            "DUB_PROGRESS "
            + json.dumps(
                {
                    "progress": _progress_value(
                        position=position,
                        total=total,
                        attempt=int(attempt),
                        max_attempts=int(plan.get("max_attempts") or 5),
                    ),
                    "stage": (
                        f"voxcpm2 · сегмент {position}/{total} · "
                        f"вариант {int(attempt)}/{int(plan.get('max_attempts') or 5)} · "
                        f"epoch {epoch}"
                    ),
                    "policy": _PROGRESS_POLICY,
                    "risk_band": plan.get("risk_band"),
                },
                ensure_ascii=False,
            )
        )
        return original_build(
            segment,
            duration_budget=duration_budget,
            attempt=attempt,
            previous_output_durations=previous_output_durations,
        )

    def seed_for_attempt(base_seed: int, segment_id: int, attempt: int, epoch: int) -> int:
        state["current_segment_id"] = int(segment_id)
        state["retry_epochs"][int(segment_id)] = int(epoch)
        return int(
            direct_retry_epoch.seed_for_attempt(base_seed, segment_id, attempt, epoch)
        )

    def acceptable_candidates(
        candidates: list[dict[str, Any]],
        speech_slot: float,
    ) -> list[dict[str, Any]]:
        try:
            acceptable = list(original_acceptable(candidates, speech_slot))
        except RuntimeError as exc:
            message = str(exc)
            if not any(
                marker in message
                for marker in ("адаптивный бюджет", "не помещается естественно")
            ):
                raise
            acceptable = []
            legacy_error = exc
        else:
            legacy_error = None
        segment = _segment(state.get("current_segment_id"))
        if not isinstance(segment, dict) or acceptable:
            return acceptable
        segment_id = int(segment.get("id") or 0)
        epoch = int(state["retry_epochs"].get(segment_id, segment.get("retry_epoch") or 0))
        work = state.get("work_dir")
        context = _scope(Path(work), segment_id)[1] if work is not None else {}
        timing = guard.evaluate_dynamic_timing_failure(
            candidates,
            segment=segment,
            speech_slot=float(speech_slot),
            retry_epoch=epoch,
            max_tempo=max_tempo,
        )
        if timing is not None and work is not None:
            marker = guard.persist_timing_block(
                Path(work),
                segment=segment,
                signature_context=context,
                retry_epoch=epoch,
                evidence=timing,
            )
            raise guard.RetryableSynthesisFailure(
                guard.format_timing_block_message(marker, repeated=False),
                segment=segment,
                evidence=timing,
                advance_retry=True,
                failure_kind="measured_timing_failure",
            )
        plan = guard.candidate_efficiency_plan(
            segment,
            speech_slot=float(speech_slot),
            retry_epoch=epoch,
            max_tempo=max_tempo,
        )
        budget = int(plan.get("max_attempts") or 5)
        if len(candidates) >= budget:
            evidence = {
                "kind": "adaptive-candidate-budget-exhausted",
                "candidate_plan": plan,
                "speech_slot": float(speech_slot),
                "max_tempo": max_tempo,
                "attempts": [
                    {
                        "attempt": int(item.get("attempt") or 0),
                        "seed": int(item.get("seed") or 0),
                        "duration": float(item.get("duration") or 0.0),
                        "required_tempo": float(item.get("required_tempo") or 0.0),
                        "score": float(item.get("score") or 0.0),
                    }
                    for item in candidates
                ],
            }
            raise guard.RetryableSynthesisFailure(
                f"Сегмент #{segment_id}: адаптивный бюджет {budget} кандидатов "
                f"исчерпан (risk={plan.get('risk_band')}); hard-quality кандидат не найден.",
                segment=segment,
                evidence=evidence,
                advance_retry=True,
                failure_kind="adaptive_budget_exhausted",
            )
        if legacy_error is not None:
            raise legacy_error
        return []

    def raw_failure_evidence(
        candidates: list[dict[str, Any]],
        *,
        speech_slot: float,
        retry_epoch: int,
    ) -> dict[str, Any]:
        payload = dict(
            original_raw(
                candidates,
                speech_slot=speech_slot,
                retry_epoch=retry_epoch,
            )
        )
        segment = _segment(state.get("current_segment_id"))
        work = state.get("work_dir")
        if isinstance(segment, dict):
            context = _scope(Path(work), segment.get("id"))[1] if work is not None else {}
            payload["failure_scope_fingerprint"] = guard.failure_scope_fingerprint(
                segment,
                signature_context=context,
            )
        payload["surgical_runtime_policy"] = POLICY
        return payload

    namespace["log"] = log
    namespace["get_backend"] = get_backend
    namespace["prepare_reference"] = prepare_reference
    namespace["read_segments"] = read_segments
    namespace["load_retry_epoch"] = load_retry_epoch
    namespace["invalidate_segment_for_retry"] = invalidate_segment_for_retry
    namespace["_build_generation_length_request"] = build_generation_length_request
    namespace["seed_for_attempt"] = seed_for_attempt
    namespace["_acceptable_candidates"] = acceptable_candidates
    namespace["_raw_failure_evidence"] = raw_failure_evidence
    namespace["SURGICAL_RUNTIME_POLICY"] = POLICY
    namespace["_SURGICAL_RUNTIME_STATE"] = state


__all__ = ["POLICY", "install_surgical_runtime"]
