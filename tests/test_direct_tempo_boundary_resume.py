from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2 import direct_max_quality_io as direct_io
from tools.voxcpm2 import generic_clean_direct_runtime as direct_runtime
from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (
    voxcpm2_cpu_shorts_production as entrypoint,
)


def test_tempo_boundary_forces_third_attempt_but_allows_tiny_final_margin() -> None:
    speech_slot = 4.32 / 1.358
    assert entrypoint.PREFERRED_MAX_TEMPO == 1.35
    assert entrypoint.HARD_MAX_TEMPO == 1.36
    assert entrypoint._tempo_policy_penalty(4.32, speech_slot) > 90.0
    assert entrypoint._tempo_policy_penalty(4.20, 4.0) == 0.0


def test_validated_late_checkpoint_prefix_can_be_adopted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    work = root / "segment_work"
    checkpoints = work / "checkpoints"
    fitted = work / "segments_fitted"
    checkpoints.mkdir(parents=True)
    fitted.mkdir(parents=True)

    segments = [
        {
            "id": 1,
            "start": 0.0,
            "end": 3.0,
            "start_delay_ms": 420,
            "reference_profile": "extended",
            "tail_guard": 0.18,
            "text": "Первая реплика.",
            "expression_policy": "source-guided-v1",
            "expression_tier": "calm",
            "expression_score": 0.2,
            "style_instruction": "спокойно",
            "source_prosody": {"available": True},
        },
        {
            "id": 2,
            "start": 3.2,
            "end": 6.2,
            "start_delay_ms": 420,
            "reference_profile": "composite",
            "tail_guard": 0.22,
            "text": "Вторая реплика.",
        },
    ]
    (root / "segments_ru_final.json").write_text(
        json.dumps(segments, ensure_ascii=False),
        encoding="utf-8",
    )

    expression = direct_runtime._expected_expression(segments[0])
    signature = {
        "policy": direct_io.POLICY,
        "model_config_sha256": "model-sha",
        "reference_sha256": "reference-sha",
        "text": segments[0]["text"],
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "tail_guard": segments[0]["tail_guard"],
        "start_delay_ms": segments[0]["start_delay_ms"],
        "reference_profile": segments[0]["reference_profile"],
        "expression": expression,
        "steps": 16,
        "cfg": 1.8,
        "base_seed": 2026072800,
    }
    report = {
        **segments[0],
        "renderer_policy": direct_io.POLICY,
        "selected_raw_pitch_evidence_ok": True,
        "fit": {"tempo": 1.20},
    }
    (checkpoints / "segment_01.json").write_text(
        json.dumps({"signature": signature, "report": report}, ensure_ascii=False),
        encoding="utf-8",
    )
    (fitted / "01_extended_fitted.wav").write_bytes(b"RIFF" + b"\0" * 5000)

    assert direct_runtime._legacy_checkpoint_prefix(root, {}) == [1]


def test_checkpoint_prefix_rejects_old_over_limit_segment(tmp_path: Path) -> None:
    root = tmp_path / "project"
    work = root / "segment_work"
    checkpoints = work / "checkpoints"
    fitted = work / "segments_fitted"
    checkpoints.mkdir(parents=True)
    fitted.mkdir(parents=True)

    segments = [
        {
            "id": 1,
            "start": 0.0,
            "end": 3.0,
            "start_delay_ms": 420,
            "reference_profile": "extended",
            "tail_guard": 0.18,
            "text": "Первая реплика.",
        },
        {
            "id": 2,
            "start": 3.2,
            "end": 6.2,
            "start_delay_ms": 420,
            "reference_profile": "extended",
            "tail_guard": 0.18,
            "text": "Вторая реплика.",
        },
    ]
    (root / "segments_ru_final.json").write_text(
        json.dumps(segments, ensure_ascii=False), encoding="utf-8"
    )
    signature = {
        "policy": direct_io.POLICY,
        "model_config_sha256": "model-sha",
        "reference_sha256": "reference-sha",
        "text": segments[0]["text"],
        "start": 0.0,
        "end": 3.0,
        "tail_guard": 0.18,
        "start_delay_ms": 420,
        "reference_profile": "extended",
        "expression": direct_runtime._expected_expression(segments[0]),
        "steps": 16,
        "cfg": 1.8,
        "base_seed": 2026072800,
    }
    report = {
        **segments[0],
        "renderer_policy": direct_io.POLICY,
        "selected_raw_pitch_evidence_ok": True,
        "fit": {"tempo": 1.351},
    }
    (checkpoints / "segment_01.json").write_text(
        json.dumps({"signature": signature, "report": report}, ensure_ascii=False),
        encoding="utf-8",
    )
    (fitted / "01_extended_fitted.wav").write_bytes(b"RIFF" + b"\0" * 5000)

    assert direct_runtime._legacy_checkpoint_prefix(root, {}) == []
