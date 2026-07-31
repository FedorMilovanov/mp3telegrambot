#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monolithic-voice facade for the direct VoxCPM2 candidate loop.

The established CLI remains in ``direct_max_quality_cli.py``. This package
shadows that module for production imports and injects one-speaker continuity,
separate synthesis text, Russian stress evidence, conservative length floors and
explicit failure diagnostics without weakening any existing quality gate.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import types
from typing import Any

from tools.voxcpm2 import direct_monolith_contract
from tools.voxcpm2 import russian_pronunciation

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_max_quality_cli.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_max_quality_cli_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить direct max-quality CLI: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "direct-cli-monolithic-voice-v1"
SYNTHESIS_TEXT_POLICY = russian_pronunciation.POLICY

_legacy_read_segments = _legacy.read_segments
_legacy_seed_for_attempt = _legacy.seed_for_attempt
_legacy_generate = _legacy._generate
_legacy_source_prosody_penalty = _legacy.source_prosody_penalty
_legacy_candidate_hard_ok = _legacy.candidate_hard_ok
_legacy_acceptable_candidates = _legacy._acceptable_candidates
_legacy_candidate_failure_summary = _legacy._candidate_failure_summary
_legacy_raw_failure_evidence = _legacy._raw_failure_evidence


def read_segments(path: Path) -> list[dict[str, Any]]:
    segments = _legacy_read_segments(Path(path))
    return direct_monolith_contract.register_segments(segments)


def seed_for_attempt(
    base_seed: int,
    segment_id: int,
    attempt: int,
    retry_epoch: int,
) -> int:
    direct_monolith_contract.set_current_segment_id(segment_id)
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
    segment = direct_monolith_contract.current_segment() or {"text": text}
    synthesis = russian_pronunciation.synthesis_text(segment)
    cadence = str(segment.get("cadence_type") or "")
    estimated_steps = max(2, int(math.floor(max(2, int(max_len)) / 1.40)))
    minimum_ratio = 0.58 if cadence in {"linked", "continuation"} else 0.40
    controlled_min_len = max(int(min_len), int(math.floor(estimated_steps * minimum_ratio)))
    controlled_min_len = min(max(2, controlled_min_len), max(2, int(max_len) - 1))
    return _legacy_generate(
        model,
        text=synthesis,
        reference=reference,
        cfg=cfg,
        steps=steps,
        min_len=controlled_min_len,
        max_len=max_len,
        seed=seed,
    )


def source_prosody_penalty(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> float:
    pronunciation = segment.get("pronunciation")
    if not isinstance(pronunciation, dict):
        pronunciation = russian_pronunciation.prepare_segment(segment)
        segment["pronunciation"] = pronunciation
    display_segment = dict(segment)
    display_segment["text"] = str(
        pronunciation.get("display_text") or segment.get("text") or ""
    )
    base = float(_legacy_source_prosody_penalty(candidate, display_segment))
    monolith = direct_monolith_contract.evaluate_candidate(candidate, segment)
    match = candidate.get("source_prosody_match")
    if not isinstance(match, dict):
        match = {}
        candidate["source_prosody_match"] = match
    match["monolith_identity"] = monolith
    match["synthesis_text_policy"] = SYNTHESIS_TEXT_POLICY
    match["display_text"] = str(pronunciation.get("display_text") or "")
    match["synthesis_text_without_control"] = str(
        pronunciation.get("synthesis_text_without_control") or ""
    )
    return base + direct_monolith_contract.candidate_penalty(candidate)


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
    start = evidence.get("start_artifact") or {}
    stress = evidence.get("stress_evidence") or {}
    return (
        "monolith={failures}, anchorSim={anchor:.3f}, neighbourSim={neighbour_sim}, "
        "adjF0={adj_f0}, f0={f0:.1f}, startLeak={start_leak}, stress={stress}"
    ).format(
        failures=failures,
        anchor=float(identity.get("anchor_spectral_similarity") or 0.0),
        neighbour_sim=(
            f"{float(neighbour.get('spectral_similarity')):.3f}"
            if neighbour.get("spectral_similarity") is not None
            else "n/a"
        ),
        adj_f0=(
            f"{float(neighbour.get('f0_median_ratio')):.3f}"
            if neighbour.get("f0_median_ratio") is not None
            else "n/a"
        ),
        f0=float(identity.get("f0_median") or 0.0),
        start_leak=bool(start.get("suspicious")),
        stress=(
            str(stress.get("reason") or "ok")
            if stress.get("required")
            else "n/a"
        ),
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
    return payload


# Legacy main resolves all these globals in its own module dictionary.
_legacy.read_segments = read_segments
_legacy.seed_for_attempt = seed_for_attempt
_legacy._generate = _generate
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
        "POLICY",
        "SYNTHESIS_TEXT_POLICY",
        "_acceptable_candidates",
        "_candidate_failure_summary",
        "_generate",
        "_monolith_diagnostic",
        "_raw_failure_evidence",
        "candidate_hard_ok",
        "main",
        "read_segments",
        "seed_for_attempt",
        "source_prosody_penalty",
    }
)
