from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from services.speech_backends import (
    BACKEND_CONTRACT_POLICY,
    DEFAULT_BACKEND_ID,
    backend_ids,
    default_backend,
    get_backend,
)
from tools.voxcpm2 import semantic_tts_guard_v4
from tools.voxcpm2 import timeline_onset_repair
from tools.voxcpm2.professional_audio_qa_v45 import (
    TIMING_RECHECK_POLICY,
    _remeasure_repaired_timing,
)


def _late_speech(*, onset: float, trailing: float, duration: float = 4.0):
    sample_rate = 16_000
    audio = np.zeros(int(duration * sample_rate), dtype=np.float32)
    start = int(onset * sample_rate)
    end = int((duration - trailing) * sample_rate)
    time = np.arange(max(0, end - start), dtype=np.float64) / sample_rate
    speech = 0.16 * np.sin(2.0 * np.pi * 145.0 * time)
    fade = min(int(0.025 * sample_rate), max(1, len(speech) // 4))
    speech[:fade] *= np.linspace(0.0, 1.0, fade)
    speech[-fade:] *= np.linspace(1.0, 0.0, fade)
    audio[start:end] = speech.astype(np.float32)
    return audio, sample_rate


def _timing_report(segment_id: int, *, onset_ms: float, trailing_ms: float):
    return {
        "segments": [
            {
                "id": segment_id,
                "passed": False,
                "semantic": {"passed": True, "heard": "тестовая фраза"},
                "acoustic": {"passed": True},
                "continuity_v45": {"passed": True},
                "voice_match_v45": {"passed": True},
                "timing": {
                    "passed": False,
                    "onset_ms": onset_ms,
                    "trailing_ms": trailing_ms,
                    "max_onset_ms": 220,
                    "min_trailing_ms": 45,
                    "isolated_start_artifact": False,
                },
            }
        ]
    }


@pytest.mark.parametrize("onset", [0.810, 0.870, 2.230])
def test_late_onset_is_shifted_without_resynthesis(tmp_path: Path, onset: float) -> None:
    original, sample_rate = _late_speech(onset=onset, trailing=0.230)
    timeline = tmp_path / "timeline.wav"
    sf.write(timeline, original, sample_rate, subtype="FLOAT")
    segments = [
        {
            "id": 7,
            "start": 0.0,
            "end": 4.0,
            "start_delay_ms": 0,
            "text": "Тестовая фраза.",
        }
    ]

    result = timeline_onset_repair.repair_timeline_onsets(
        timeline,
        segments,
        _timing_report(7, onset_ms=onset * 1000.0, trailing_ms=230.0),
    )

    repaired, repaired_rate = sf.read(timeline, dtype="float32")
    timing = semantic_tts_guard_v4.measure_timing_quality(
        np.asarray(repaired, dtype=np.float32),
        int(repaired_rate),
        max_onset_ms=220,
        min_trailing_ms=45,
    )
    assert result["repaired_segment_ids"] == [7]
    assert result["synthesis_invoked"] is False
    assert result["checkpoints_preserved"] is True
    assert timing["passed"] is True
    assert timing["onset_ms"] <= 220.0
    assert np.sum(np.abs(repaired)) == pytest.approx(
        np.sum(np.abs(original)), rel=2e-3
    )


def test_repaired_window_rechecks_only_timing_and_reuses_semantics(tmp_path: Path) -> None:
    original, sample_rate = _late_speech(onset=0.810, trailing=0.230)
    timeline = tmp_path / "timeline.wav"
    sf.write(timeline, original, sample_rate, subtype="FLOAT")
    segments = [
        {
            "id": 5,
            "start": 0.0,
            "end": 4.0,
            "start_delay_ms": 0,
            "text": "Тестовая фраза.",
        }
    ]
    report = _timing_report(5, onset_ms=810.0, trailing_ms=230.0)
    semantic_before = dict(report["segments"][0]["semantic"])
    timeline_onset_repair.repair_timeline_onsets(timeline, segments, report)

    failed, repaired_report = _remeasure_repaired_timing(
        timeline,
        segments,
        report,
        [5],
    )

    check = repaired_report["segments"][0]
    assert failed == []
    assert check["passed"] is True
    assert check["timing"]["passed"] is True
    assert check["timing"]["recheck_policy"] == TIMING_RECHECK_POLICY
    assert check["semantic"] == semantic_before


def test_non_timing_failure_is_never_repaired(tmp_path: Path) -> None:
    audio, sample_rate = _late_speech(onset=0.810, trailing=0.230)
    timeline = tmp_path / "timeline.wav"
    sf.write(timeline, audio, sample_rate, subtype="FLOAT")
    report = _timing_report(5, onset_ms=810.0, trailing_ms=230.0)
    report["segments"][0]["voice_match_v45"] = {"passed": False}

    result = timeline_onset_repair.repair_timeline_onsets(
        timeline,
        [{"id": 5, "start": 0.0, "end": 4.0, "start_delay_ms": 0}],
        report,
    )

    assert result["changed"] is False
    assert result["repaired_segment_ids"] == []


def test_speech_backend_registry_exposes_voxcpm2_as_an_adapter() -> None:
    backend = default_backend()
    assert BACKEND_CONTRACT_POLICY == "speech-backend-contract-v2"
    assert DEFAULT_BACKEND_ID == "voxcpm2"
    assert backend_ids() == ("voxcpm2",)
    assert backend.backend_id == "voxcpm2"
    assert get_backend("OpenBMB").backend_id == "voxcpm2"
    capabilities = backend.capabilities().as_dict()
    assert capabilities["voice_cloning"] is True
    assert capabilities["checkpointable_segments"] is True
    with pytest.raises(RuntimeError, match="Неизвестный speech backend"):
        get_backend("future-engine")


def test_runtime_contract_fingerprints_backend_and_onset_repair_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "tools"
        / "voxcpm2"
        / "clean_runtime_contract"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "services/speech_backends/base.py",
        "services/speech_backends/registry.py",
        "services/speech_backends/voxcpm2.py",
        "tools/voxcpm2/timeline_onset_repair.py",
        "tools/voxcpm2/professional_audio_qa_v45/__init__.py",
        "tools/voxcpm2/generic_clean_direct_runtime/__init__.py",
        'render["speech_backend"] = backend_payload',
    ):
        assert marker in source


def test_complete_retry_round_checkpoints_are_migratable_without_audio_loss() -> None:
    root = Path(__file__).resolve().parents[1]
    facade = (
        root
        / "tools"
        / "voxcpm2"
        / "generic_clean_direct_runtime"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    main = (
        root
        / "tools"
        / "voxcpm2"
        / "generic_clean_direct_runtime"
        / "__main__.py"
    ).read_text(encoding="utf-8")
    required = (
        'CHECKPOINT_MIGRATION_POLICY = "signature-and-natural-tempo-checkpoint-adoption-v2"',
        "MAX_ACCEPTED_SEED_ROUNDS = 12",
        "accepted_ids != list(range(1, accepted_ids[-1] + 1))",
        "accepted_ids[-1] > len(segments_payload)",
        "selected_raw_pitch_evidence_ok",
        "model_config_sha256",
        "reference_sha256",
        "_legacy.clean.semantic_tts_guard_v4._retarget(",
        "new_base_seed=base_seed",
    )
    for marker in required:
        assert marker in facade
    assert "from . import main" in main
    assert "main()" in main
