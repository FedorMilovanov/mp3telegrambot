from __future__ import annotations

from pathlib import Path

import numpy as np

from tools.voxcpm2.direct_max_quality_analysis import (
    candidate_hard_ok,
    candidate_score,
    pitch_profile,
)
from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_ENCODE_SR,
    EXPECTED_OUTPUT_SR,
    MAX_TEMPO,
    POLICY,
    REFERENCE_TAIL_SILENCE,
)
from tools.voxcpm2.direct_max_quality_render import _generate


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "voxcpm2_cpu_shorts_production.py"
)


def _candidate(*, duration: float, voiced: float, median: float, p90: float, active: float, gap: float) -> dict:
    return {
        "duration": duration,
        "tail_info": {"suspicious": False},
        "clipping_ratio": 0.0,
        "leading_silence": 0.05,
        "trailing_silence": 0.10,
        "activity": {"active_ratio": active, "max_internal_gap": gap},
        "pitch": {"voiced_ratio": voiced, "f0_median": median, "f0_p90": p90},
    }


def test_renderer_audio_contract_is_native_voxcpm2() -> None:
    assert POLICY == "voxcpm2-direct-max-quality-v2"
    assert EXPECTED_ENCODE_SR == 16000
    assert EXPECTED_OUTPUT_SR == 48000
    assert REFERENCE_TAIL_SILENCE == 0.0
    assert MAX_TEMPO <= 1.35


def test_48khz_pitch_diagnostic_keeps_male_f0() -> None:
    sample_rate = 48_000
    seconds = 1.2
    frequency = 105.0
    time = np.arange(int(sample_rate * seconds), dtype=np.float32) / sample_rate
    samples = (0.20 * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    profile = pitch_profile(samples, sample_rate)
    assert profile["voiced_ratio"] > 0.50
    assert 98.0 <= profile["f0_median"] <= 112.0
    assert 98.0 <= profile["f0_p90"] <= 112.0


def test_nonsense_high_register_candidate_cannot_win() -> None:
    reference = {"f0_median": 105.0, "f0_p90": 145.0}
    good = _candidate(
        duration=3.5,
        voiced=0.62,
        median=108.0,
        p90=149.0,
        active=0.76,
        gap=0.10,
    )
    bad = _candidate(
        duration=0.28,
        voiced=0.07,
        median=205.0,
        p90=230.0,
        active=0.08,
        gap=0.95,
    )
    good_score = candidate_score(good, 3.6, reference)
    bad_score = candidate_score(bad, 3.6, reference)
    assert candidate_hard_ok(good, 3.6)
    assert not candidate_hard_ok(bad, 3.6)
    assert bad_score > good_score + 200


def test_generation_has_headroom_for_the_final_word() -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def generate(
            self,
            *,
            text,
            reference_wav_path,
            cfg_value,
            inference_timesteps,
            min_len,
            max_len,
            normalize,
            denoise,
            retry_badcase,
            retry_badcase_max_times,
            retry_badcase_ratio_threshold,
            seed,
        ):
            self.kwargs = locals()
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


def test_direct_cli_uses_official_quality_controls_without_wrappers() -> None:
    render = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_render.py").read_text(encoding="utf-8")
    cli = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py").read_text(encoding="utf-8")
    analysis = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_analysis.py").read_text(encoding="utf-8")
    io = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_io.py").read_text(encoding="utf-8")
    stable = EXAMPLE.read_text(encoding="utf-8")
    combined = render + cli + analysis + io + stable
    assert '"retry_badcase": True' in render
    assert '"retry_badcase_max_times": 2' in render
    assert '"reference_sha256"' in cli
    assert '"model_config_sha256"' in cli
    assert "candidate_hard_ok" in cli
    assert "F0×=" in cli
    assert "AudioVAE:" in cli
    assert "reshape(-1, factor).mean(axis=1)" in analysis
    assert "REFERENCE_TAIL_SILENCE = 0.0" in io
    assert "runpy.run_path" not in combined
    assert "semantic_tts_guard" not in combined
    assert "class _SubprocessProxy" not in combined
    assert "install_runtime_adapters()" not in stable
    assert "REPO_ROOT" in stable


def test_stable_bot_and_powershell_path_imports_one_cli() -> None:
    stable = EXAMPLE.read_text(encoding="utf-8")
    assert "from tools.voxcpm2.direct_max_quality_cli import main" in stable
    assert "VoxCPM.from_pretrained" not in stable
    assert "model.generate" not in stable
