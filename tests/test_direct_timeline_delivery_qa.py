from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tools.voxcpm2.direct_timeline_delivery_qa import verify_timeline_delivery


def _segment(text: str) -> dict[str, object]:
    return {
        "id": 1,
        "start": 0.0,
        "end": 2.25,
        "start_delay_ms": 0,
        "tail_guard": 0.13,
        "text": text,
    }


def _write_timeline(path: Path, *, rising: bool, growing: bool) -> None:
    sample_rate = 48_000
    speech_seconds = 2.0
    time = np.arange(int(speech_seconds * sample_rate), dtype=np.float64) / sample_rate
    start_hz, end_hz = (110.0, 132.0) if rising else (150.0, 105.0)
    slope = (end_hz - start_hz) / speech_seconds
    phase = 2.0 * np.pi * (start_hz * time + 0.5 * slope * time**2)
    start_amp, end_amp = (0.08, 0.28) if growing else (0.28, 0.06)
    audio = np.linspace(start_amp, end_amp, len(time)) * np.sin(phase)
    fade = int(0.015 * sample_rate)
    audio[:fade] *= np.linspace(0.0, 1.0, fade)
    audio[-fade:] *= np.linspace(1.0, 0.0, fade)
    timeline = np.concatenate(
        [audio.astype(np.float32), np.zeros(int(0.25 * sample_rate), dtype=np.float32)]
    )
    sf.write(path, timeline, sample_rate, subtype="PCM_24")


def test_rejects_unresolved_terminal_after_timeline_assembly(tmp_path: Path) -> None:
    timeline = tmp_path / "timeline.wav"
    _write_timeline(timeline, rising=True, growing=True)
    work = tmp_path / "segment_work"
    fitted = work / "segments_fitted" / "01_extended_fitted.wav"
    checkpoint = work / "checkpoints" / "segment_01.json"
    fitted.parent.mkdir(parents=True)
    checkpoint.parent.mkdir(parents=True)
    fitted.write_bytes(b"fitted")
    checkpoint.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="terminal_not_resolved"):
        verify_timeline_delivery(
            timeline,
            [(_segment("И не на то, что выйдет замуж."), fitted)],
        )

    assert timeline.with_suffix(".delivery_qa.json").is_file()
    assert not fitted.exists()
    assert not checkpoint.exists()


def test_accepts_resolved_terminal_after_timeline_assembly(tmp_path: Path) -> None:
    timeline = tmp_path / "timeline.wav"
    _write_timeline(timeline, rising=False, growing=False)
    report = verify_timeline_delivery(
        timeline,
        [(_segment("И не на то, что выйдет замуж."), Path("unused.wav"))],
    )
    assert report["passed"] is True
    assert report["failed_segment_ids"] == []


def test_assembled_timeline_rejects_late_broadband_noise(tmp_path: Path) -> None:
    sample_rate = 48_000
    speech_seconds = 1.55
    time = np.arange(int(speech_seconds * sample_rate), dtype=np.float64) / sample_rate
    slope = (105.0 - 155.0) / speech_seconds
    phase = 2.0 * np.pi * (155.0 * time + 0.5 * slope * time**2)
    speech = 0.22 * np.sin(phase)
    fade = int(0.04 * sample_rate)
    speech[:fade] *= np.linspace(0.0, 1.0, fade)
    speech[-fade:] *= np.linspace(1.0, 0.0, fade)
    quiet = np.zeros(int(0.06 * sample_rate), dtype=np.float32)
    rng = np.random.default_rng(20260730)
    noise = rng.normal(0.0, 0.16, int(0.12 * sample_rate)).astype(np.float32)
    noise *= np.hanning(len(noise)).astype(np.float32)
    tail = np.zeros(int(0.27 * sample_rate), dtype=np.float32)
    timeline_audio = np.concatenate([speech.astype(np.float32), quiet, noise, tail])
    timeline = tmp_path / "timeline.wav"
    sf.write(timeline, timeline_audio, sample_rate, subtype="PCM_24")
    segment = _segment("Она знает, что грядёт, — и смеётся.")
    segment["end"] = len(timeline_audio) / sample_rate

    with pytest.raises(RuntimeError, match="late_broadband_burst"):
        verify_timeline_delivery(timeline, [(segment, tmp_path / "missing.wav")])
