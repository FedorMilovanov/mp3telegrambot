from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.voxcpm2 import final_encoded_delivery_qa as qa
from tools.voxcpm2.direct_retry_epoch import load_retry_epoch


def _segment(duration: float) -> dict[str, object]:
    return {
        "id": 19,
        "start": 0.0,
        "end": duration,
        "tail_guard": 0.18,
        "start_delay_ms": 0,
        "reference_profile": "extended",
        "text": "Она знает, что грядёт, — и смеётся.",
    }


def _falling_speech(duration: float = 1.55) -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (105.0 - 155.0) / duration
    phase = 2.0 * np.pi * (155.0 * time + 0.5 * slope * time**2)
    envelope = np.linspace(0.24, 0.07, len(time), dtype=np.float64)
    speech = envelope * np.sin(phase)
    fade = int(0.04 * sample_rate)
    speech[:fade] *= np.linspace(0.0, 1.0, fade)
    speech[-fade:] *= np.linspace(1.0, 0.0, fade)
    return speech.astype(np.float32), sample_rate


def _noisy_final_audio() -> tuple[np.ndarray, int]:
    speech, sample_rate = _falling_speech()
    valley = np.zeros(int(0.06 * sample_rate), dtype=np.float32)
    rng = np.random.default_rng(20260730)
    burst = rng.normal(0.0, 0.16, int(0.12 * sample_rate)).astype(np.float32)
    burst *= np.hanning(len(burst)).astype(np.float32)
    silence = np.zeros(int(0.27 * sample_rate), dtype=np.float32)
    return np.concatenate([speech, valley, burst, silence]), sample_rate


def test_post_aac_gate_accepts_clean_resolved_final_segment() -> None:
    speech, sample_rate = _falling_speech()
    tail = np.zeros(int(0.35 * sample_rate), dtype=np.float32)
    audio = np.concatenate([speech, tail])

    report = qa.evaluate_encoded_segment(
        audio,
        sample_rate,
        _segment(len(audio) / sample_rate),
    )

    assert qa.POLICY == "post-aac-russian-delivery-v2"
    assert report["policy"] == qa.POLICY
    assert report["passed"] is True
    assert report["late_tail"]["suspicious"] is False


def test_post_aac_gate_rejects_late_broadband_synthesis_burst() -> None:
    audio, sample_rate = _noisy_final_audio()

    report = qa.evaluate_encoded_segment(
        audio,
        sample_rate,
        _segment(len(audio) / sample_rate),
    )

    assert report["passed"] is False
    assert report["late_tail"]["suspicious"] is True
    assert "late_broadband_burst" in report["failures"]


def test_failed_final_encoded_segment_advances_only_its_seed_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio, _sample_rate = _noisy_final_audio()
    segment = _segment(len(audio) / qa.SAMPLE_RATE)
    segments_path = tmp_path / "segments_ru_final.json"
    segments_path.write_text(
        json.dumps([segment], ensure_ascii=False),
        encoding="utf-8",
    )
    work = tmp_path / "segment_work"
    fitted = work / "segments_fitted" / "19_extended_fitted.wav"
    clean = work / "segments_clean" / "19_extended_clean.wav"
    checkpoint = work / "checkpoints" / "segment_19.json"
    for path in (fitted, clean, checkpoint):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"checkpoint")

    monkeypatch.setattr(qa, "_decode_segment", lambda *_args, **_kwargs: audio)
    report_path = tmp_path / "post_aac_delivery.json"

    with pytest.raises(RuntimeError, match="новый seed epoch"):
        qa.verify_final_encoded_russian(
            russian_only_video=tmp_path / "russian_only.mp4",
            segments_path=segments_path,
            report_path=report_path,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["invalidated_for_retry"]["retry_epoch"] == 1
    assert load_retry_epoch(work, 19) == 1
    assert not fitted.exists()
    assert not clean.exists()
    assert not checkpoint.exists()
