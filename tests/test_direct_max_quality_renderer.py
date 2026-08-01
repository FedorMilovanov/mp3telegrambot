from __future__ import annotations

from pathlib import Path

import numpy as np

from services.dub_worker_release import SOURCE_PROSODY_ROLE_POLICY
from services.speech_backends import default_backend
from tools.voxcpm2 import source_prosody_policy
from tools.voxcpm2.direct_max_quality_analysis import (
    candidate_hard_ok,
    candidate_score,
    pitch_profile,
    spectral_envelope,
)
from tools.voxcpm2.direct_max_quality_cli import _candidate_failure_summary
from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_ENCODE_SR,
    EXPECTED_OUTPUT_SR,
    MAX_TEMPO,
    POLICY,
    REFERENCE_TAIL_SILENCE,
)
from tools.voxcpm2.direct_max_quality_render import _generate
from tools.voxcpm2.direct_source_prosody import candidate_pitch_evidence_ok


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "voxcpm2_cpu_shorts_production.py"
)


def _candidate(
    *,
    duration: float,
    voiced: float,
    median: float,
    p90: float,
    active: float,
    gap: float,
) -> dict:
    sample_rate = 16_000
    time = np.arange(max(1, int(duration * sample_rate)), dtype=np.float32) / sample_rate
    return {
        "attempt": 1,
        "duration": duration,
        "score": 10.0,
        "tail_info": {"suspicious": False},
        "clipping_ratio": 0.0,
        "leading_silence": 0.05,
        "trailing_silence": 0.10,
        "activity": {"active_ratio": active, "max_internal_gap": gap},
        "pitch": {"voiced_ratio": voiced, "f0_median": median, "f0_p90": p90},
        "samples": (0.12 * np.sin(2.0 * np.pi * median * time)).astype(np.float32),
        "sample_rate": sample_rate,
    }


def _complete_voice_match(candidate: dict) -> dict:
    candidate["voice_match"] = {
        "f0_median_ratio": 1.0,
        "f0_p90_ratio": 1.0,
        "spectral_similarity": 0.90,
    }
    return candidate


def test_renderer_audio_contract_is_native_voxcpm2() -> None:
    assert POLICY == "voxcpm2-direct-max-quality-v3"
    assert EXPECTED_ENCODE_SR == 16_000
    assert EXPECTED_OUTPUT_SR == 48_000
    assert REFERENCE_TAIL_SILENCE == 0.0
    assert MAX_TEMPO == 1.36


def test_48khz_pitch_diagnostic_keeps_male_f0() -> None:
    sample_rate = 48_000
    frequency = 105.0
    time = np.arange(int(sample_rate * 1.2), dtype=np.float32) / sample_rate
    samples = (0.20 * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    profile = pitch_profile(samples, sample_rate)
    assert profile["voiced_ratio"] > 0.50
    assert 98.0 <= profile["f0_median"] <= 112.0
    assert 98.0 <= profile["f0_p90"] <= 112.0


def test_nonsense_high_register_candidate_cannot_win() -> None:
    reference = {"f0_median": 105.0, "f0_p90": 145.0}
    good = _complete_voice_match(
        _candidate(
            duration=3.5,
            voiced=0.62,
            median=108.0,
            p90=149.0,
            active=0.76,
            gap=0.10,
        )
    )
    bad = _candidate(
        duration=0.28,
        voiced=0.07,
        median=205.0,
        p90=230.0,
        active=0.08,
        gap=0.95,
    )
    reference["spectral_envelope"] = spectral_envelope(
        good["samples"],
        good["sample_rate"],
    )

    assert candidate_hard_ok(good, 3.6)
    assert candidate_pitch_evidence_ok(good)
    assert not candidate_hard_ok(bad, 3.6)
    assert not candidate_pitch_evidence_ok(bad)
    assert candidate_score(bad, 3.6, reference) > candidate_score(good, 3.6, reference) + 200


def test_voice_match_is_fail_closed_for_missing_or_nonfinite_evidence() -> None:
    candidate = _candidate(
        duration=3.5,
        voiced=0.62,
        median=108.0,
        p90=149.0,
        active=0.76,
        gap=0.10,
    )
    assert candidate_hard_ok(candidate, 3.6) is False

    _complete_voice_match(candidate)
    assert candidate_hard_ok(candidate, 3.6) is True
    candidate["voice_match"]["f0_median_ratio"] = float("nan")
    assert candidate_hard_ok(candidate, 3.6) is False


def test_bad_candidate_diagnostics_are_actionable() -> None:
    candidate = _candidate(
        duration=0.28,
        voiced=0.07,
        median=205.0,
        p90=230.0,
        active=0.08,
        gap=0.95,
    )
    candidate["voice_match"] = {
        "f0_median_ratio": 1.95,
        "f0_p90_ratio": 1.59,
        "spectral_similarity": 0.22,
    }
    summary = _candidate_failure_summary([candidate], 3.6)
    assert "attempt 1" in summary
    assert "duration×=" in summary
    assert "voiced=0.070" in summary
    assert "F0×=1.950/1.590" in summary
    assert "rawPitch=False" in summary
    assert "srcProsody=n/a" in summary


def test_generation_preserves_final_word_headroom_and_supported_retries() -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def generate(
            self,
            *,
            retry_badcase: bool,
            retry_badcase_max_times: int,
            retry_badcase_ratio_threshold: float,
            seed: int,
            **kwargs,
        ):
            self.kwargs = {
                **kwargs,
                "retry_badcase": retry_badcase,
                "retry_badcase_max_times": retry_badcase_max_times,
                "retry_badcase_ratio_threshold": retry_badcase_ratio_threshold,
                "seed": seed,
            }
            return np.zeros(16, dtype=np.float32)

    model = FakeModel()
    _generate(
        model,
        text="Он обязательно закончит последнее слово.",
        reference=Path("reference.wav"),
        cfg=1.8,
        steps=16,
        min_len=2,
        max_len=40,
        seed=7,
    )
    assert model.kwargs["max_len"] == 58
    assert model.kwargs["retry_badcase"] is True
    assert model.kwargs["retry_badcase_max_times"] == 2
    assert model.kwargs["retry_badcase_ratio_threshold"] == 6.0
    assert model.kwargs["seed"] == 7


def test_model_specific_loading_lives_only_in_backend_adapter() -> None:
    raw_cli = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py").read_text(
        encoding="utf-8"
    )
    adapter = (ROOT / "services" / "speech_backends" / "voxcpm2.py").read_text(
        encoding="utf-8"
    )
    stable = EXAMPLE.read_text(encoding="utf-8")

    assert "VoxCPM.from_pretrained" not in raw_cli
    assert "setup_cache(" not in raw_cli
    assert "VoxCPM.from_pretrained" in adapter
    assert "setup_cache(" in adapter
    assert default_backend().adapter_policy == "voxcpm2-speech-backend-adapter-v5"
    assert "from tools.voxcpm2 import direct_max_quality_cli as _direct_cli" in stable
    assert "VoxCPM.from_pretrained" not in stable


def test_source_language_prosody_is_removed_from_candidate_ranking() -> None:
    segment = source_prosody_policy.mark_diagnostic_only(
        {
            "text": "Русская реплика.",
            "source_prosody": {"f0_median": 210.0, "energy": 0.9},
        }
    )

    safe = source_prosody_policy.ranking_view(segment)

    assert source_prosody_policy.POLICY == SOURCE_PROSODY_ROLE_POLICY
    assert source_prosody_policy.is_diagnostic_only(segment) is True
    assert "source_prosody" not in safe
    assert safe["text"] == segment["text"]
