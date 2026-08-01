#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monolithic-voice facade for the direct speech-backend candidate loop."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import types
from typing import Any

from services.speech_backends import BackendGenerationRequest
from tools.voxcpm2 import direct_monolith_contract
from tools.voxcpm2 import russian_pronunciation
from tools.voxcpm2 import source_prosody_policy

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_max_quality_cli.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_max_quality_cli_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить direct max-quality CLI: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "direct-cli-monolithic-voice-v4"
GENERATION_REQUEST_FACTORY_POLICY = "typed-generation-request-factory-v2"
SYNTHESIS_TEXT_POLICY = russian_pronunciation.POLICY
PRONUNCIATION_VARIANT_POLICY = russian_pronunciation.VARIANT_POLICY
_CURRENT_ATTEMPT = 1
_CONTINUATION_REFERENCE: Path | None = None
_CONTINUATION_TEXT = ""
CONTINUATION_POLICY = "backend-capability-gated-previous-block-prompt-v2"


def set_continuation_context(reference: Path | None, text: str = "") -> None:
    global _CONTINUATION_REFERENCE, _CONTINUATION_TEXT
    _CONTINUATION_REFERENCE = Path(reference).resolve() if reference is not None else None
    _CONTINUATION_TEXT = str(text or "").strip()


_legacy_read_segments = _legacy.read_segments
_legacy_seed_for_attempt = _legacy.seed_for_attempt
_legacy_generate = _legacy._generate
_legacy_build_generation_request = _legacy._build_generation_request
_legacy_source_prosody_penalty = _legacy.source_prosody_penalty
_legacy_candidate_hard_ok = _legacy.candidate_hard_ok
_legacy_acceptable_candidates = _legacy._acceptable_candidates
_legacy_candidate_failure_summary = _legacy._candidate_failure_summary
_legacy_raw_failure_evidence = _legacy._raw_failure_evidence


def read_segments(path: Path) -> list[dict[str, Any]]:
    segments = _legacy_read_segments(Path(path))
    marked = [source_prosody_policy.mark_diagnostic_only(item) for item in segments]
    return direct_monolith_contract.register_segments(marked)


def seed_for_attempt(
    base_seed: int,
    segment_id: int,
    attempt: int,
    retry_epoch: int,
) -> int:
    global _CURRENT_ATTEMPT
    direct_monolith_contract.set_current_segment_id(segment_id)
    _CURRENT_ATTEMPT = max(1, int(attempt))
    return int(
        _legacy_seed_for_attempt(
            base_seed,
            segment_id,
            attempt,
            retry_epoch,
        )
    )


def _generate(
    model: Any,
    *,
    text: str,
    reference: Path,
    cfg: float,
    steps: int,
    min_len: int,
    max_len: int,
    seed: int,
) -> Any:
    """Compatibility seam for legacy tests; production uses ``_backend_generate``."""
    segment = direct_monolith_contract.current_segment() or {"text": text}
    synthesis = russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)
    cadence = str(segment.get("cadence_type") or "")
    estimated_steps = max(2, int(math.floor(max(2, int(max_len)) / 1.40)))
    minimum_ratio = 0.58 if cadence in {"linked", "continuation"} else 0.40
    controlled_min_len = max(int(min_len), int(math.floor(estimated_steps * minimum_ratio)))
    controlled_min_len = min(max(2, controlled_min_len), max(2, int(max_len) - 1))
    kwargs = {
        "text": synthesis,
        "reference": reference,
        "cfg": cfg,
        "steps": steps,
        "min_len": controlled_min_len,
        "max_len": max_len,
        "seed": seed,
    }
    if _CONTINUATION_REFERENCE is not None:
        return _legacy_generate(
            model,
            **kwargs,
            continuation_reference=_CONTINUATION_REFERENCE,
            continuation_text=_CONTINUATION_TEXT,
        )
    return _legacy_generate(model, **kwargs)


def _build_generation_request(
    session: Any,
    **kwargs: Any,
) -> BackendGenerationRequest:
    """Extend the neutral request without replacing the backend executor."""
    base_request = _legacy_build_generation_request(session, **kwargs)
    segment = direct_monolith_contract.current_segment() or {
        "text": base_request.text,
    }
    synthesis = russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)
    cadence = str(segment.get("cadence_type") or "")
    max_len = base_request.option_int("max_len", default=2, low=2, high=512)
    planned_min_len = base_request.option_int(
        "min_len",
        default=2,
        low=1,
        high=512,
    )
    estimated_steps = max(2, int(math.floor(max_len / 1.40)))
    minimum_ratio = 0.58 if cadence in {"linked", "continuation"} else 0.40
    controlled_min_len = max(
        planned_min_len,
        int(math.floor(estimated_steps * minimum_ratio)),
    )
    controlled_min_len = min(controlled_min_len, max(2, max_len - 1))

    continuation_reference: Path | None = None
    continuation_text = ""
    if (
        _CONTINUATION_REFERENCE is not None
        and bool(getattr(session, "supports_continuation_context", False))
    ):
        continuation_reference = _CONTINUATION_REFERENCE
        continuation_text = _CONTINUATION_TEXT

    backend_options = dict(base_request.backend_options)
    backend_options["min_len"] = controlled_min_len
    return BackendGenerationRequest(
        text=synthesis,
        reference_audio=base_request.reference_audio,
        seed=base_request.seed,
        duration_budget=base_request.duration_budget,
        style_instruction=str(segment.get("style_instruction") or ""),
        continuation_reference=continuation_reference,
        continuation_text=continuation_text,
        backend_options=backend_options,
    )


def source_prosody_penalty(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> float:
    """Keep source-language prosody as evidence, never as ranking weight."""
    pronunciation = segment.get("pronunciation")
    if not isinstance(pronunciation, dict):
        pronunciation = russian_pronunciation.prepare_segment(segment)
        segment["pronunciation"] = pronunciation
    display_segment = dict(segment)
    display_segment["text"] = str(
        pronunciation.get("display_text") or segment.get("text") or ""
    )
    ranking_segment = source_prosody_policy.ranking_view(display_segment)
    diagnostic_penalty = float(
        _legacy_source_prosody_penalty(candidate, ranking_segment)
    )
    monolith = direct_monolith_contract.evaluate_candidate(candidate, segment)
    match = candidate.get("source_prosody_match")
    if not isinstance(match, dict):
        match = {}
        candidate["source_prosody_match"] = match
    variant = russian_pronunciation.variant_for_attempt(
        segment,
        int(candidate.get("attempt") or _CURRENT_ATTEMPT),
    )
    match["monolith_identity"] = monolith
    match["source_prosody_policy"] = source_prosody_policy.POLICY
    match["source_prosody_ranking_enabled"] = False
    match["diagnostic_penalty"] = diagnostic_penalty
    match["synthesis_text_policy"] = SYNTHESIS_TEXT_POLICY
    match["pronunciation_variant_policy"] = PRONUNCIATION_VARIANT_POLICY
    match["pronunciation_variant"] = variant
    match["display_text"] = str(pronunciation.get("display_text") or "")
    match["synthesis_text_without_control"] = str(
        variant.get("synthesis_text_without_control") or ""
    )
    return float(direct_monolith_contract.candidate_penalty(candidate))


def candidate_hard_ok(candidate: dict[str, Any], speech_slot: float) -> bool:
    return bool(
        _legacy_candidate_hard_ok(candidate, speech_slot)
        and direct_monolith_contract.candidate_hard_ok(candidate)
    )


def _acceptable_candidates(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> list[dict[str, Any]]:
    result = list(_legacy_acceptable_candidates(candidates, speech_slot))
    direct_monolith_contract.record_acceptable(result)
    return result


def _monolith_diagnostic(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("monolith_identity")
    if not isinstance(evidence, dict):
        return "monolith=missing"
    failures = ",".join(str(value) for value in evidence.get("failures") or []) or "ok"
    identity = evidence.get("identity") or {}
    neighbour = evidence.get("neighbour") or {}
    transition = evidence.get("source_relative_transition") or {}
    start = evidence.get("start_artifact") or {}
    stress = evidence.get("stress_evidence") or {}
    variant = (candidate.get("source_prosody_match") or {}).get("pronunciation_variant") or {}
    return (
        "monolith={failures}, anchorSim={anchor:.3f}, neighbourSim={neighbour_sim}, "
        "adjF0={adj_f0}, sourceAdj={source_adj}, allowedAdj={allowed_adj}, "
        "f0={f0:.1f}, startLeak={start_leak}, stress={stress}, variant={variant}"
    ).format(
        failures=failures,
        anchor=float(identity.get("anchor_spectral_similarity") or 0.0),
        neighbour_sim=(
            f"{float(neighbour.get('spectral_similarity')):.3f}"
            if neighbour.get("spectral_similarity") is not None
            else "n/a"
        ),
        adj_f0=(
            f"{float(transition.get('generated_f0_median_jump_st')):.2f}st"
            if transition.get("generated_f0_median_jump_st") is not None
            else "n/a"
        ),
        source_adj=(
            f"{float(transition.get('source_f0_median_jump_st')):.2f}st"
            if transition.get("source_f0_median_jump_st") is not None
            else "n/a"
        ),
        allowed_adj=(
            f"{float(transition.get('allowed_f0_median_jump_st')):.2f}st"
            if transition.get("allowed_f0_median_jump_st") is not None
            else "n/a"
        ),
        f0=float(identity.get("f0_median") or 0.0),
        start_leak=bool(start.get("suspicious")),
        stress=(str(stress.get("reason") or "ok") if stress.get("required") else "n/a"),
        variant=int(variant.get("variant_index") or 0),
    )


def _candidate_failure_summary(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> str:
    base = _legacy_candidate_failure_summary(candidates, speech_slot)
    extras = [
        f"attempt {int(item.get('attempt') or 0)}: {_monolith_diagnostic(item)}"
        for item in candidates
        if isinstance(item, dict)
    ]
    return "; ".join(value for value in (base, *extras) if value)


def _raw_failure_evidence(
    candidates: list[dict[str, Any]],
    *,
    speech_slot: float,
    retry_epoch: int,
) -> dict[str, Any]:
    payload = dict(
        _legacy_raw_failure_evidence(
            candidates,
            speech_slot=speech_slot,
            retry_epoch=retry_epoch,
        )
    )
    payload["monolith_policy"] = direct_monolith_contract.POLICY
    payload["pronunciation_variant_policy"] = PRONUNCIATION_VARIANT_POLICY
    by_attempt = {
        int(item.get("attempt") or 0): item
        for item in candidates
        if isinstance(item, dict)
    }
    attempts = payload.get("attempts")
    if isinstance(attempts, list):
        for row in attempts:
            if not isinstance(row, dict):
                continue
            candidate = by_attempt.get(int(row.get("attempt") or 0))
            evidence = candidate.get("monolith_identity") if isinstance(candidate, dict) else None
            if isinstance(evidence, dict):
                row["monolith_identity"] = evidence
            match = candidate.get("source_prosody_match") if isinstance(candidate, dict) else None
            if isinstance(match, dict) and isinstance(match.get("pronunciation_variant"), dict):
                row["pronunciation_variant"] = match["pronunciation_variant"]
    return payload


_legacy.read_segments = read_segments
_legacy.seed_for_attempt = seed_for_attempt
_legacy._generate = _generate
_legacy._build_generation_request = _build_generation_request
_legacy.set_continuation_context = set_continuation_context
_legacy.source_prosody_penalty = source_prosody_penalty
_legacy.candidate_hard_ok = candidate_hard_ok
_legacy._acceptable_candidates = _acceptable_candidates
_legacy._candidate_failure_summary = _candidate_failure_summary
_legacy._raw_failure_evidence = _raw_failure_evidence
main = _legacy.main


class _WriteThroughModule(types.ModuleType):
    """Keep entrypoint monkeypatches synchronized with legacy main globals."""

    def __setattr__(self, name: str, value: Any) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str) -> Any:
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "CONTINUATION_POLICY",
        "GENERATION_REQUEST_FACTORY_POLICY",
        "POLICY",
        "PRONUNCIATION_VARIANT_POLICY",
        "SYNTHESIS_TEXT_POLICY",
        "_acceptable_candidates",
        "_backend_generate",
        "_build_generation_request",
        "_candidate_failure_summary",
        "_generate",
        "_monolith_diagnostic",
        "_raw_failure_evidence",
        "candidate_hard_ok",
        "main",
        "read_segments",
        "seed_for_attempt",
        "set_continuation_context",
        "source_prosody_penalty",
    }
)
