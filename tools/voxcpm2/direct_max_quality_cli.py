#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal direct renderer entrypoint with layered production hardening."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_direct_max_quality_cli_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing direct renderer base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._direct_max_quality_cli_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_surgical_guard import install_guard_contract
from tools.voxcpm2.direct_universal_runtime import install_direct_runtime
from tools.voxcpm2.direct_surgical_runtime import install_surgical_runtime
from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish
from tools.voxcpm2.direct_final_audit_v3 import install_final_audit
from tools.voxcpm2.direct_failure_recovery import install_main_failure_recovery

install_guard_contract()
install_direct_runtime(globals())
install_surgical_runtime(globals())
install_global_polish()
install_final_audit(globals())
install_main_failure_recovery(globals())

if _ORIGINAL_NAME == "__main__":
    _main = globals().get("main")
    if not callable(_main):
        raise RuntimeError("Direct renderer base did not export main().")
    _main()

_BASE_ALL = tuple(globals().get('__all__', ()))

from dataclasses import replace

from pathlib import Path

import types

from typing import Any

from services.speech_backends import (
    BackendGenerationLengthRequest,
    BackendGenerationRequest,
)

from tools.voxcpm2 import direct_monolith_contract

from tools.voxcpm2 import russian_pronunciation

from tools.voxcpm2 import source_prosody_policy

POLICY = "direct-cli-monolithic-voice-v5"

GENERATION_REQUEST_FACTORY_POLICY = "typed-generation-request-factory-v3"

GENERATION_LENGTH_HINT_POLICY = "cadence-minimum-completion-ratio-v1"

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

_legacy_read_segments = read_segments

_legacy_seed_for_attempt = seed_for_attempt

_legacy_generate = _generate

_legacy_build_generation_length_request = _build_generation_length_request

_legacy_build_generation_request = _build_generation_request

_legacy_source_prosody_penalty = source_prosody_penalty

_legacy_candidate_hard_ok = candidate_hard_ok

_legacy_acceptable_candidates = _acceptable_candidates

_legacy_candidate_failure_summary = _candidate_failure_summary

_legacy_raw_failure_evidence = _raw_failure_evidence

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
    """Compatibility seam; production cadence planning uses typed requests."""
    segment = direct_monolith_contract.current_segment() or {"text": text}
    synthesis = russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)
    kwargs = {
        "text": synthesis,
        "reference": reference,
        "cfg": cfg,
        "steps": steps,
        "min_len": min_len,
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

def _build_generation_length_request(
    segment: dict[str, Any],
    *,
    duration_budget: float,
    attempt: int,
    previous_output_durations: tuple[float, ...],
) -> BackendGenerationLengthRequest:
    """Add cadence intent without interpreting backend-specific length units."""
    base_request = _legacy_build_generation_length_request(
        segment,
        duration_budget=duration_budget,
        attempt=attempt,
        previous_output_durations=previous_output_durations,
    )
    cadence = str(segment.get("cadence_type") or "")
    minimum_ratio = 0.58 if cadence in {"linked", "continuation"} else 0.40
    metadata = dict(base_request.metadata)
    metadata.update(
        {
            "policy": GENERATION_LENGTH_HINT_POLICY,
            "cadence_type": cadence,
        }
    )
    return replace(
        base_request,
        minimum_completion_ratio=minimum_ratio,
        metadata=metadata,
    )

def _build_generation_request(
    session: Any,
    **kwargs: Any,
) -> BackendGenerationRequest:
    """Extend the neutral request without replacing backend length options."""
    base_request = _legacy_build_generation_request(session, **kwargs)
    segment = direct_monolith_contract.current_segment() or {
        "text": base_request.text,
    }
    synthesis = russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)

    continuation_reference: Path | None = None
    continuation_text = ""
    if (
        _CONTINUATION_REFERENCE is not None
        and bool(getattr(session, "supports_continuation_context", False))
    ):
        continuation_reference = _CONTINUATION_REFERENCE
        continuation_text = _CONTINUATION_TEXT

    return BackendGenerationRequest(
        text=synthesis,
        reference_audio=base_request.reference_audio,
        seed=base_request.seed,
        duration_budget=base_request.duration_budget,
        style_instruction=str(segment.get("style_instruction") or ""),
        continuation_reference=continuation_reference,
        continuation_text=continuation_text,
        backend_options=base_request.backend_options,
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

read_segments = read_segments

seed_for_attempt = seed_for_attempt

_generate = _generate

_build_generation_length_request = _build_generation_length_request

_build_generation_request = _build_generation_request

set_continuation_context = set_continuation_context

source_prosody_penalty = source_prosody_penalty

candidate_hard_ok = candidate_hard_ok

_acceptable_candidates = _acceptable_candidates

_candidate_failure_summary = _candidate_failure_summary

_raw_failure_evidence = _raw_failure_evidence

main = main

__all__ = sorted(
    set(name for name in _BASE_ALL if not name.startswith("__"))
    | {
        "CONTINUATION_POLICY",
        "GENERATION_LENGTH_HINT_POLICY",
        "GENERATION_REQUEST_FACTORY_POLICY",
        "POLICY",
        "PRONUNCIATION_VARIANT_POLICY",
        "SYNTHESIS_TEXT_POLICY",
        "_acceptable_candidates",
        "_backend_generate",
        "_build_generation_length_request",
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
