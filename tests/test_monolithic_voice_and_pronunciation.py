from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tools.voxcpm2 import direct_monolith_contract
from tools.voxcpm2 import direct_source_relative_continuity
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import russian_pronunciation
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail


def _tone(rate: int, seconds: float, frequency: float, amplitude: float = 0.2) -> np.ndarray:
    time = np.arange(int(rate * seconds), dtype=np.float64) / rate
    return (np.sin(2.0 * np.pi * frequency * time) * amplitude).astype(np.float32)


def _segment(segment_id: int, text: str = "Обычная фраза.") -> dict[str, object]:
    return {
        "id": segment_id,
        "start": float(segment_id - 1),
        "end": float(segment_id),
        "tail_guard": 0.18,
        "start_delay_ms": 0,
        "text": text,
        "cadence_type": "terminal",
        "expression_tier": "earnest",
        "reference_profile": "extended",
    }


def test_pronunciation_keeps_display_text_and_builds_bounded_gryadyot_variants() -> None:
    segment = _segment(1, "Она надеется на то, что грядёт.")

    prepared = russian_pronunciation.prepare_segment(segment)

    assert segment["text"] == "Она надеется на то, что грядёт."
    assert prepared["display_text"] == segment["text"]
    assert prepared["variant_policy"] == russian_pronunciation.VARIANT_POLICY
    assert prepared["synthesis_variants_without_control"] == [
        "Она надеется на то, что грядёт.",
        "Она надеется на то, что гря-дёт.",
    ]
    assert prepared["synthesis_text"].startswith("(")
    assert "stress the final syllable" in prepared["control_instruction"]
    assert prepared["stress_evidence_required"] is True


def test_pronunciation_attempts_alternate_canonical_and_syllable_hint() -> None:
    segment = _segment(1, "Это то, что грядёт.")
    segment["pronunciation"] = russian_pronunciation.prepare_segment(segment)

    first = russian_pronunciation.variant_for_attempt(segment, 1)
    second = russian_pronunciation.variant_for_attempt(segment, 2)
    third = russian_pronunciation.variant_for_attempt(segment, 3)

    assert first["synthesis_text_without_control"].endswith("грядёт.")
    assert second["synthesis_text_without_control"].endswith("гря-дёт.")
    assert third["synthesis_text_without_control"] == first["synthesis_text_without_control"]


def test_known_final_stress_requires_strong_last_nucleus() -> None:
    rate = 16_000
    strong = np.zeros(rate, dtype=np.float32)
    strong[int(0.30 * rate):int(0.42 * rate)] = _tone(rate, 0.12, 165.0, 0.18)
    strong[int(0.55 * rate):int(0.80 * rate)] = _tone(rate, 0.25, 175.0, 0.25)
    weak = np.zeros(rate, dtype=np.float32)
    weak[int(0.30 * rate):int(0.44 * rate)] = _tone(rate, 0.14, 165.0, 0.22)
    weak[int(0.56 * rate):int(0.63 * rate)] = _tone(rate, 0.07, 175.0, 0.045)
    segment = _segment(1, "Это то, что грядёт.")
    segment["pronunciation"] = russian_pronunciation.prepare_segment(segment)

    accepted = russian_pronunciation.stress_evidence(strong, rate, segment)
    rejected = russian_pronunciation.stress_evidence(weak, rate, segment)

    assert accepted["required"] is True
    assert accepted["passed"] is True
    assert rejected["required"] is True
    assert rejected["passed"] is False


def test_start_reference_leak_and_immediate_tail_are_detected() -> None:
    rate = 16_000
    candidate = np.zeros(int(rate * 1.2), dtype=np.float32)
    rng = np.random.default_rng(17)
    candidate[int(0.02 * rate):int(0.055 * rate)] = (
        rng.standard_normal(int(0.035 * rate)).astype(np.float32) * 0.16
    )
    candidate[int(0.15 * rate):int(0.92 * rate)] = _tone(rate, 0.77, 155.0, 0.20)

    start = direct_monolith_contract._start_artifact(candidate, rate)

    tail = np.zeros(int(rate * 1.45), dtype=np.float32)
    tail[:int(0.92 * rate)] = _tone(rate, 0.92, 150.0, 0.18)
    tail[int(0.95 * rate):int(1.10 * rate)] = (
        rng.standard_normal(int(0.15 * rate)).astype(np.float32) * 0.075
    )
    late = detect_late_broadband_tail(tail, rate)

    assert start["suspicious"] is True
    assert start["artifact_type"] == "detached_reference_leak"
    assert late["suspicious"] is True
    assert late["artifact_type"] == "late_broadband_burst"


def _identity(f0: float, *, bands: list[float] | None = None) -> dict[str, object]:
    return {
        "f0_median": f0,
        "f0_p90": f0 * 1.35,
        "voiced_ratio": 0.65,
        "active_ratio": 0.72,
        "rms_dbfs": -19.0,
        "spectral_centroid_hz": 1050.0,
        "spectral_bands": bands or [0.25, 0.20, 0.18, 0.14, 0.11, 0.07, 0.05],
        "anchor_spectral_similarity": 0.90,
        "anchor_f0_median_ratio": 1.10,
        "anchor_f0_p90_ratio": 1.08,
    }


def _write_checkpoint(work: Path, segment_id: int, identity: dict[str, object]) -> None:
    path = work / "checkpoints" / f"segment_{segment_id:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "report": {
                    "selected_source_prosody_match": {
                        "monolith_identity": {"identity": identity}
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _candidate(work: Path, f0: float) -> dict[str, object]:
    rate = 16_000
    attempts = work / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    identity = _identity(f0)
    return {
        "path": str(attempts / "candidate.wav"),
        "samples": _tone(rate, 0.90, f0, 0.18),
        "sample_rate": rate,
        "duration": 0.90,
        "actual_speech_slot": 0.90,
        "pitch": {
            "f0_median": f0,
            "f0_p90": f0 * 1.35,
            "voiced_ratio": 0.65,
        },
        "activity": {
            "active_ratio": 0.72,
            "rms_dbfs": -19.0,
            "max_internal_gap": 0.0,
        },
        "timbre": {
            "spectral_centroid_hz": identity["spectral_centroid_hz"],
            "bands": identity["spectral_bands"],
        },
        "voice_match": {
            "spectral_similarity": 0.90,
            "f0_median_ratio": 1.10,
            "f0_p90_ratio": 1.08,
        },
        "score": 20.0,
    }


def test_bassy_adjacent_voice_is_rejected(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write_checkpoint(work, 1, _identity(170.0))
    segments = direct_monolith_contract.register_segments([_segment(1), _segment(2)])

    evidence = direct_monolith_contract.evaluate_candidate(
        _candidate(work, 82.0),
        segments[1],
    )

    assert evidence["hard_ok"] is False
    assert (
        "adjacent_f0_median_jump" in evidence["failures"]
        or "source_relative_f0_median_jump" in evidence["failures"]
    )


def test_source_prosody_is_diagnostic_when_original_is_stable() -> None:
    previous_segment = _segment(1)
    current_segment = _segment(2)
    previous_segment["source_prosody"] = {"f0_median": 170.0, "f0_p90": 210.0}
    current_segment["source_prosody"] = {"f0_median": 172.0, "f0_p90": 212.0}

    evidence = direct_source_relative_continuity.evaluate_transition(
        current_identity=_identity(100.0),
        previous_identity=_identity(170.0),
        current_segment=current_segment,
        previous_segment=previous_segment,
    )

    assert evidence["source_available"] is False
    assert evidence["advisory_source_available"] is True
    assert evidence["role"] == "diagnostics_only_until_semantic_alignment"
    assert evidence["absolute_gate_override_allowed"] is False
    assert evidence["ranking_penalty_enabled"] is False
    assert evidence["hard_ok"] is True
    assert evidence["failures"] == []
    assert evidence["penalty"] == 0.0
    assert "source_relative_f0_median_jump" in evidence["warnings"]
    assert abs(float(evidence["source_f0_median_jump_st"])) < 1.0


def test_source_supported_rise_remains_diagnostic_and_cannot_authorize_identity() -> None:
    previous_segment = _segment(1)
    current_segment = _segment(2)
    previous_segment["source_prosody"] = {"f0_median": 100.0, "f0_p90": 125.0}
    current_segment["source_prosody"] = {"f0_median": 180.0, "f0_p90": 225.0}

    evidence = direct_source_relative_continuity.evaluate_transition(
        current_identity=_identity(275.0),
        previous_identity=_identity(160.0),
        current_segment=current_segment,
        previous_segment=previous_segment,
    )

    assert evidence["source_available"] is False
    assert evidence["advisory_source_available"] is True
    assert evidence["absolute_gate_override_allowed"] is False
    assert evidence["ranking_penalty_enabled"] is False
    assert evidence["hard_ok"] is True, evidence
    assert evidence["failures"] == []
    assert evidence["penalty"] == 0.0
    assert float(evidence["generated_f0_median_jump_st"]) > 8.0
    assert float(evidence["allowed_f0_median_jump_st"]) >= 10.0


def test_resume_compares_with_immediate_previous_checkpoint(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write_checkpoint(work, 1, _identity(95.0))
    _write_checkpoint(work, 2, _identity(172.0))
    segments = direct_monolith_contract.register_segments(
        [_segment(1), _segment(2), _segment(3)]
    )

    evidence = direct_monolith_contract.evaluate_candidate(
        _candidate(work, 174.0),
        segments[2],
    )

    assert evidence["resume_policy"] == "nearest-accepted-checkpoint-identity-v1"
    assert evidence["hard_ok"] is True, evidence
    assert 0.95 <= evidence["neighbour"]["f0_median_ratio"] <= 1.08


def test_expression_arc_suppresses_isolated_emotional_burst(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_segments = [_segment(1), _segment(2), _segment(3)]

    def measured(**kwargs):
        result = [dict(item) for item in source_segments]
        result[0]["expression_score"] = 0.0
        result[1]["expression_score"] = 2.0
        result[2]["expression_score"] = 0.0
        for item in result:
            item["source_prosody"] = {}
        return result

    monkeypatch.setattr(expressive_continuity, "_legacy_plan_segments", measured)
    report = tmp_path / "expression.json"

    planned = expressive_continuity.plan_segments(
        source=tmp_path / "source.mp4",
        segments=source_segments,
        duration=3.0,
        report_path=report,
    )

    assert [item["text"] for item in planned] == [item["text"] for item in source_segments]
    assert all(item["reference_profile"] == "extended" for item in planned)
    assert all(item["expression_tier"] != "passionate" for item in planned)
    assert planned[1]["expression_tier"] == "earnest"
    assert max(
        abs(float(planned[index]["expression_score"]) - float(planned[index - 1]["expression_score"]))
        for index in range(1, len(planned))
    ) <= expressive_continuity.MAX_ADJACENT_SCORE_STEP + 1e-9
    assert report.is_file()
