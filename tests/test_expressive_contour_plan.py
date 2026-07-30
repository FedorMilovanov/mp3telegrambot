from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from tools.voxcpm2 import expressive_continuity


def test_expression_plan_embeds_source_contour_without_changing_text(tmp_path, monkeypatch):
    sample_rate = 16_000
    duration = 4.0
    time = np.arange(int(sample_rate * duration), dtype=np.float64) / sample_rate
    audio = (0.18 * np.sin(2.0 * np.pi * (110.0 + 18.0 * time) * time)).astype(np.float32)
    source = tmp_path / "source.wav"
    sf.write(source, audio, sample_rate)

    def fake_decode(path: Path, decoded: Path):
        return sf.read(path, dtype="float32")

    monkeypatch.setattr(expressive_continuity.audio_policy, "_decode", fake_decode)
    monkeypatch.setattr(
        expressive_continuity.audio_policy,
        "pitch_profile",
        lambda clip, rate: {
            "voiced_ratio": 0.75,
            "f0_median": 125.0,
            "f0_p90": 155.0,
        },
    )
    monkeypatch.setattr(
        expressive_continuity.audio_policy,
        "activity_stats",
        lambda clip, rate: {
            "active_ratio": 0.76,
            "max_internal_gap": 0.0,
            "rms_dbfs": -22.0,
            "peak_dbfs": -10.0,
        },
    )

    original = [
        {
            "id": 1,
            "start": 0.0,
            "end": 1.8,
            "source_end": 1.8,
            "text": "Помните мой любимый стих",
            "source": "Remember my favorite verse",
        },
        {
            "id": 2,
            "start": 2.0,
            "end": 3.8,
            "source_end": 3.8,
            "text": "о женщине из Притч?",
            "source": "about the woman in Proverbs?",
        },
    ]
    report = tmp_path / "expression.json"
    planned = expressive_continuity.plan_segments(
        source=source,
        segments=original,
        duration=duration,
        report_path=report,
    )

    assert [item["text"] for item in planned] == [item["text"] for item in original]
    assert planned[0]["cadence_type"] == "linked"
    assert planned[1]["cadence_type"] == "question"
    for item in planned:
        contour = item["source_prosody"]["contour"]
        assert contour["available"] is True
        assert len(contour["energy_contour"]) == 5
        assert len(contour["pitch_contour"]) == 5
    assert report.is_file()
