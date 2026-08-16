#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal VoxCPM2 production hardening installed by compatibility wrappers.

This module contains only project-wide behavior. It does not know any video ID,
caption text, speaker, or one-off SRT. The wrappers keep the previously audited
implementation available as a base snapshot and apply these invariants on every
ready-SRT direct dubbing job.
"""
from __future__ import annotations

import json
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Callable

from tools.voxcpm2 import direct_timing_guard

POLICY = "voxcpm2-universal-production-hardening-v1"
_PROGRESS_POLICY = "candidate-aware-project-progress-v1"
_MODEL_TQDM_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)*\s*\d{1,3}%\|.*\|\s*\d+/\d+\s*\["
)


def _segments_by_id(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in segments:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
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


def install_worker_progress(namespace: MutableMapping[str, Any]) -> None:
    """Ignore model-internal tqdm and keep explicit project progress authoritative."""
    original = namespace.get("_progress_from_line_v44")
    if not callable(original):
        raise RuntimeError("dub_worker_hardened не содержит _progress_from_line_v44.")

    def _progress_from_line_v44(line: str, current: int) -> tuple[int, str]:
        text = str(line or "").strip()
        if _MODEL_TQDM_RE.match(text):
            return int(current), ""
        return original(line, current)

    namespace["_MODEL_TQDM_RE"] = _MODEL_TQDM_RE
    namespace["_progress_from_line_v44"] = _progress_from_line_v44
    namespace["_RUNTIME_VERSION"] = "dub-worker-quality-v4.7-universal-tts"


def install_runtime_fingerprint(namespace: MutableMapping[str, Any]) -> None:
    modules = tuple(namespace.get("_RENDER_MODULES") or ())
    additions = (
        "tools/voxcpm2/direct_timing_guard.py",
        "tools/voxcpm2/direct_universal_runtime.py",
        "tools/voxcpm2/direct_retry_epoch.py",
        "tools/voxcpm2/_direct_retry_epoch_base.py",
        "tools/voxcpm2/direct_max_quality_cli.py",
        "tools/voxcpm2/generic_clean_direct_runtime.py",
        "tools/voxcpm2/_generic_clean_direct_runtime_base.py",
        "tools/voxcpm2/clean_production_core/__init__.py",
        "services/speech_backends/voxcpm2.py",
        "services/speech_backends/base.py",
        "services/speech_backends/control_plane.py",
    )
    namespace["_RENDER_MODULES"] = tuple(dict.fromkeys((*modules, *additions)))


def install_generic_preflight(namespace: MutableMapping[str, Any]) -> None:
    """Run final semantic-block timing validation before references and model."""
    original = namespace.get("_run_clean_voxcpm_and_master")
    if not callable(original):
        raise RuntimeError(
            "generic_clean_direct_runtime не содержит _run_clean_voxcpm_and_master."
        )
    clean = namespace["clean"]
    direct_io = namespace["direct_io"]
    read_json_value = namespace["_read_json_value"]

    def _run_clean_voxcpm_and_master(**kwargs: Any) -> Path:
        root = Path(kwargs["root"]).resolve()
        request = dict(kwargs["request"])
        duration = float(kwargs["duration"])
        segments_json = Path(kwargs["segments_json"]).resolve()
        settings = clean.clean_runtime_contract.normalize_settings(
            request, duration=duration,
        )
        backend = clean.get_backend(
            request.get("speech_backend") or clean.DEFAULT_BACKEND_ID
        )
        if backend.backend_id == "voxcpm2":
            repo = Path(namespace["__file__"]).resolve().parents[2]
            runtime = backend.runtime_paths(repo, request)
            model_path = backend.discover_model(runtime.archive_root)
            model_config = model_path / "config.json"
            if not model_config.is_file():
                raise RuntimeError(
                    f"Не найден config.json выбранной TTS-модели: {model_config}"
                )
            segments_payload = read_json_value(segments_json)
            if not isinstance(segments_payload, list):
                raise RuntimeError(
                    "segments_ru_final.json повреждён до timing preflight."
                )
            speech_options = request.get("speech_options") or {}
            if not isinstance(speech_options, dict):
                raise RuntimeError("speech_options должен быть JSON-объектом.")
            context = {
                "policy": POLICY,
                "backend": backend.backend_id,
                "adapter_policy": backend.adapter_policy,
                "cfg": float(settings["cfg"]),
                "steps": int(settings["steps"]),
                "base_seed": int(settings["base_seed"]),
                "max_tempo": float(direct_io.MAX_TEMPO),
                "model_config_sha256": direct_io.sha256_file(model_config),
                "speech_model_profile": str(
                    request.get("speech_model_profile") or ""
                ),
                "speech_profile_fingerprint": str(
                    request.get("speech_profile_fingerprint") or ""
                ),
                "speech_options": speech_options,
            }
            work_dir = root / "segment_work"
            direct_timing_guard.write_signature_context(work_dir, context)
            report = direct_timing_guard.run_pre_model_guard(
                segments_payload,
                work_dir=work_dir,
                max_tempo=direct_io.MAX_TEMPO,
                signature_context=context,
            )
            namespace["production"].log(
                "universal timing preflight passed before voice references/model: "
                f"warnings={report.get('warning_ids') or []}"
            )
        return original(**kwargs)

    namespace["_run_clean_voxcpm_and_master"] = _run_clean_voxcpm_and_master


def install_direct_runtime(namespace: MutableMapping[str, Any]) -> None:
    """Install universal candidate, retry-scope and progress behavior."""
    required = (
        "read_segments", "load_retry_epoch", "invalidate_segment_for_retry",
        "seed_for_attempt", "_acceptable_candidates", "_raw_failure_evidence",
        "MAX_TEMPO", "log",
    )
    missing = [
        name for name in required
        if name != "MAX_TEMPO" and not callable(namespace.get(name))
    ]
    if "MAX_TEMPO" not in namespace:
        missing.append("MAX_TEMPO")
    if missing:
        raise RuntimeError("direct CLI contract missing: " + ", ".join(missing))

    original_read_segments: Callable[..., Any] = namespace["read_segments"]
    original_load_retry_epoch: Callable[..., Any] = namespace["load_retry_epoch"]
    original_invalidate: Callable[..., Any] = namespace["invalidate_segment_for_retry"]
    original_seed_for_attempt: Callable[..., Any] = namespace["seed_for_attempt"]
    original_acceptable: Callable[..., Any] = namespace["_acceptable_candidates"]
    original_raw_failure: Callable[..., Any] = namespace["_raw_failure_evidence"]
    log: Callable[[str], Any] = namespace["log"]
    max_tempo = float(namespace["MAX_TEMPO"])
    state: dict[str, Any] = {
        "segments": {}, "work_dir": None,
        "current_segment_id": None, "total_segments": 0,
    }

    def read_segments(path: Path) -> list[dict[str, Any]]:
        segments = list(original_read_segments(Path(path)))
        state["segments"] = _segments_by_id(segments)
        state["total_segments"] = len(segments)
        return segments

    def _segment(segment_id: Any) -> dict[str, Any] | None:
        try:
            return state["segments"].get(int(segment_id))
        except (TypeError, ValueError, OverflowError):
            return None

    def _scope(
        work_dir: Path, segment_id: Any,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        segment = _segment(segment_id)
        context = direct_timing_guard.load_signature_context(work_dir)
        if not isinstance(segment, dict):
            return None, context, ""
        profile = str(segment.get("reference_profile") or "extended")
        reference = work_dir.resolve() / "references_guarded" / f"{profile}.wav"
        hash_file = namespace.get("sha256_file")
        if reference.is_file() and callable(hash_file):
            context = {
                **context,
                "reference_profile": profile,
                "reference_sha256": str(hash_file(reference)),
            }
        fingerprint = direct_timing_guard.failure_scope_fingerprint(
            segment, signature_context=context,
        )
        return segment, context, fingerprint

    def load_retry_epoch(work_dir: Path, segment_id: Any) -> int:
        state["work_dir"] = Path(work_dir).resolve()
        segment, _context, scope = _scope(Path(work_dir), segment_id)
        if isinstance(segment, dict) and scope:
            try:
                return int(original_load_retry_epoch(
                    work_dir, segment_id, scope_fingerprint=scope,
                ))
            except TypeError:
                pass
        return int(original_load_retry_epoch(work_dir, segment_id))

    def invalidate_segment_for_retry(
        work_dir: Path, segment: dict[str, Any], *, reason: str,
        fitted_path: Path | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _segment_value, context, scope = _scope(
            Path(work_dir), segment.get("id")
        )
        enriched = dict(evidence or {})
        enriched["failure_scope_fingerprint"] = scope or (
            direct_timing_guard.failure_scope_fingerprint(
                segment, signature_context=context,
            )
        )
        return original_invalidate(
            work_dir, segment, reason=reason,
            fitted_path=fitted_path, evidence=enriched,
        )

    def seed_for_attempt(
        base_seed: int, segment_id: int, attempt: int, retry_epoch: int,
    ) -> int:
        state["current_segment_id"] = int(segment_id)
        total = max(1, int(state.get("total_segments") or 1))
        ordered = sorted(int(value) for value in state["segments"])
        try:
            position = ordered.index(int(segment_id)) + 1
        except ValueError:
            position = min(max(1, int(segment_id)), total)
        segment = _segment(segment_id) or {"text": ""}
        slot = float(segment.get("speech_slot") or 1.0)
        work_dir = state.get("work_dir")
        context = (
            _scope(Path(work_dir), segment_id)[1]
            if work_dir is not None else {}
        )
        if int(attempt) == 1 and work_dir is not None:
            direct_timing_guard.enforce_retry_epoch_budget(
                work_dir=Path(work_dir), segment=segment,
                retry_epoch=int(retry_epoch), signature_context=context,
            )
        plan = direct_timing_guard.candidate_efficiency_plan(
            segment, speech_slot=max(0.001, slot),
            retry_epoch=int(retry_epoch), max_tempo=max_tempo,
        )
        max_attempts = int(plan.get("max_attempts") or 5)
        log("DUB_PROGRESS " + json.dumps({
            "progress": _progress_value(
                position=position, total=total, attempt=int(attempt),
                max_attempts=max_attempts,
            ),
            "stage": (
                f"voxcpm2 · сегмент {position}/{total} · "
                f"вариант {int(attempt)}/{max_attempts} · "
                f"epoch {int(retry_epoch)}"
            ),
            "policy": _PROGRESS_POLICY,
            "risk_band": plan.get("risk_band"),
        }, ensure_ascii=False))
        return int(original_seed_for_attempt(
            base_seed, segment_id, attempt, retry_epoch,
        ))

    def _current_segment() -> dict[str, Any] | None:
        return _segment(state.get("current_segment_id"))

    def _acceptable_candidates(
        candidates: list[dict[str, Any]], speech_slot: float,
    ) -> list[dict[str, Any]]:
        acceptable = list(original_acceptable(candidates, speech_slot))
        segment = _current_segment()
        if not isinstance(segment, dict) or acceptable:
            return acceptable
        retry_epoch = int(segment.get("retry_epoch") or 0)
        work_dir = state.get("work_dir")
        context = (
            _scope(Path(work_dir), segment.get("id"))[1]
            if work_dir is not None else {}
        )
        timing_failure = direct_timing_guard.evaluate_dynamic_timing_failure(
            candidates, segment=segment, speech_slot=float(speech_slot),
            retry_epoch=retry_epoch, max_tempo=max_tempo,
        )
        if timing_failure is not None and work_dir is not None:
            block = direct_timing_guard.persist_timing_block(
                Path(work_dir), segment=segment,
                signature_context=context, retry_epoch=retry_epoch,
                evidence=timing_failure,
            )
            raise RuntimeError(
                direct_timing_guard.format_timing_block_message(
                    block, repeated=False,
                )
            )
        plan = direct_timing_guard.candidate_efficiency_plan(
            segment, speech_slot=float(speech_slot),
            retry_epoch=retry_epoch, max_tempo=max_tempo,
        )
        budget = int(plan.get("max_attempts") or 5)
        if len(candidates) >= budget:
            summary = ", ".join(
                f"#{int(item.get('attempt') or 0)}: "
                f"score={float(item.get('score') or 0.0):.1f}, "
                f"tempo={float(item.get('required_tempo') or 0.0):.3f}"
                for item in candidates
            )
            raise RuntimeError(
                f"Сегмент #{int(segment.get('id') or 0)}: адаптивный бюджет "
                f"{budget} кандидатов исчерпан (risk={plan.get('risk_band')}); "
                f"hard-quality кандидат не найден. {summary}"
            )
        return acceptable

    def _raw_failure_evidence(
        candidates: list[dict[str, Any]], *,
        speech_slot: float, retry_epoch: int,
    ) -> dict[str, Any]:
        payload = dict(original_raw_failure(
            candidates, speech_slot=speech_slot, retry_epoch=retry_epoch,
        ))
        segment = _current_segment()
        work_dir = state.get("work_dir")
        if isinstance(segment, dict):
            context = (
                _scope(Path(work_dir), segment.get("id"))[1]
                if work_dir is not None else {}
            )
            payload["failure_scope_fingerprint"] = (
                direct_timing_guard.failure_scope_fingerprint(
                    segment, signature_context=context,
                )
            )
        payload["universal_runtime_policy"] = POLICY
        return payload

    namespace["read_segments"] = read_segments
    namespace["load_retry_epoch"] = load_retry_epoch
    namespace["invalidate_segment_for_retry"] = invalidate_segment_for_retry
    namespace["seed_for_attempt"] = seed_for_attempt
    namespace["_acceptable_candidates"] = _acceptable_candidates
    namespace["_raw_failure_evidence"] = _raw_failure_evidence
    namespace["UNIVERSAL_RUNTIME_POLICY"] = POLICY


__all__ = [
    "POLICY", "install_direct_runtime", "install_generic_preflight",
    "install_runtime_fingerprint", "install_worker_progress",
]
