from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tools" / "voxcpm2" / "professional_audio_v45.py"
ANALYSIS = ROOT / "tools" / "voxcpm2" / "direct_max_quality_analysis.py"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def test_reference_decode_preserves_timbre_without_denoiser() -> None:
    source = _source(REFERENCE)
    assert "afftdn" not in source
    assert '"denoise": False' in source
    assert '"highpass=f=45,lowpass=f=7600"' in source
    assert "loudnorm" not in source
    assert "from tools.voxcpm2.direct_max_quality_analysis import activity_stats, pitch_profile" in source


def test_reference_selection_keeps_real_voice_metrics_and_report() -> None:
    source = _source(REFERENCE)
    assert "pitch_profile(clip, sample_rate)" in source
    assert "activity_stats(clip, sample_rate)" in source
    assert 'pitch["voiced_ratio"] < 0.16' in source
    assert "max_internal_gap" in source
    assert 'output.with_suffix(".selection.json")' in source


def test_steady_voice_threshold_cannot_regress_to_multiplier_above_one() -> None:
    source = _source(ANALYSIS)
    assert "np.percentile(rms, 35)) * 0.50" in source
    assert "10 ** (-45 / 20)" in source
    assert "np.percentile(rms, 35)) * 1.7" not in source
