from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tools.voxcpm2.direct_retry_epoch import load_retry_epoch
from tools.voxcpm2.direct_timeline_delivery_qa import (
    POLICY,
    verify_timeline_delivery,
)


def _segment(text: str) -> dict[str, object]:
    return {
        "id": 1,
        "start": 0.0,
        "end": 2.25,
        "start_delay_ms": 0,
        "tail_guard": 0.13,
        "reference_profile": "extended",
        "text": text,
    }


def _install_identity_reference(timeline: Path) -> Path:
    reference = timeline.parent.parent / "references" / "extended_reference.wav"
    reference.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(timeline, reference)
    return reference


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
    _install_identity_reference(path)


def _phrase_audio(duration: float = 1.0) -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = 45.0 / duration
    phase = 2.0 * np.pi * (105.0 * time + 0.5 * slope * time**2)
    audio = 0.20 * np.sin(phase)
    fade = int(0.025 * sample_rate)
    audio[:fade] *= np.linspace(0.0, 1.0, fade)
    audio[-fade:] *= np.linspace(1.0, 0.0, fade)
    return audio.astype(np.float32), sample_rate


def _write_linked_timeline(path: Path, gap_seconds: float) -> list[tuple[dict[str, object], Path]]:
    first, sample_rate = _phrase_audio()
    second, _ = _phrase_audio()
    gap = np.zeros(int(gap_seconds * sample_rate), dtype=np.float32)
    tail = np.zeros(int(0.18 * sample_rate), dtype=np.float32)
    sf.write(path, np.concatenate([first, gap, second, tail]), sample_rate, subtype="PCM_24")
    _install_identity_reference(path)
    first_segment = {
        "id": 1,
        "start": 0.0,
        "end": 1.12,
        "start_delay_ms": 0,
        "tail_guard": 0.10,
        "reference_profile": "extended",
        "text": "Помните мой любимый стих",
    }
    second_start = 1.0 + gap_seconds
    second_segment = {
        "id": 2,
        "start": second_start,
        "end": second_start + 1.18,
        "start_delay_ms": 0,
        "tail_guard": 0.10,
        "reference_profile": "extended",
        "text": "о женщине из тридцать первой главы Притч?",
    }
    fitted_dir = path.parent / "segment_work" / "segments_fitted"
    fitted_dir.mkdir(parents=True, exist_ok=True)
    first_fitted = fitted_dir / "01_extended_fitted.wav"
    second_fitted = fitted_dir / "02_extended_fitted.wav"
    first_fitted.write_bytes(b"fitted")
    second_fitted.write_bytes(b"fitted")
    return [
        (first_segment, first_fitted),
        (second_segment, second_fitted),
    ]


def test_rejects_unresolved_terminal_and_advances_only_failed_epoch(tmp_path: Path) -> None:
    timeline = tmp_path / "timeline.wav"
    _write_timeline(timeline, rising=True, growing=True)
    work = tmp_path / "segment_work"
    fitted = work / "segments_fitted" / "01_extended_fitted.wav"
    clean = work / "segments_clean" / "01_extended_clean.wav"
    checkpoint = work / "checkpoints" / "segment_01.json"
    for path in (fitted, clean, checkpoint):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"checkpoint")

    with pytest.raises(RuntimeError, match="terminal_not_resolved"):
        verify_timeline_delivery(
            timeline,
            [(_segment("И не на то, что выйдет замуж."), fitted)],
        )

    report_path = timeline.with_suffix(".delivery_qa.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert POLICY == "assembled-monolithic-voice-v1"
    assert report["policy"] in {POLICY, "assembled-russian-delivery-v3"}
    assert report["invalidated_for_retry"][0]["retry_epoch"] == 1
    assert load_retry_epoch(work, 1) == 1
    assert not fitted.exists()
    assert not clean.exists()
    assert not checkpoint.exists()


def test_accepts_resolved_terminal_without_advancing_epoch(tmp_path: Path) -> None:
    timeline = tmp_path / "timeline.wav"
    _write_timeline(timeline, rising=False, growing=False)
    work = tmp_path / "segment_work"
    fitted = work / "segments_fitted" / "01_extended_fitted.wav"
    fitted.parent.mkdir(parents=True, exist_ok=True)
    fitted.write_bytes(b"fitted")
    report = verify_timeline_delivery(
        timeline,
        [(_segment("И не на то, что выйдет замуж."), fitted)],
    )
    assert report["passed"] is True
    assert report["policy"] == POLICY
    assert report["failed_segment_ids"] == []
    assert report["invalidated_for_retry"] == []
    assert load_retry_epoch(work, 1) == 0
    assert fitted.exists()


def test_assembled_timeline_rejects_late_broadband_noise_and_changes_seed_epoch(
    tmp_path: Path,
) -> None:
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
    _install_identity_reference(timeline)
    segment = _segment("Она знает, что грядёт, — и смеётся.")
    segment["end"] = len(timeline_audio) / sample_rate
    work = tmp_path / "segment_work"
    fitted = work / "segments_fitted" / "01_extended_fitted.wav"
    fitted.parent.mkdir(parents=True, exist_ok=True)
    fitted.write_bytes(b"fitted")

    with pytest.raises(RuntimeError, match="late_broadband_burst"):
        verify_timeline_delivery(timeline, [(segment, fitted)])

    assert load_retry_epoch(work, 1) == 1
    assert not fitted.exists()


def test_rejects_long_gap_between_linked_srt_lines_and_advances_first_only(
    tmp_path: Path,
) -> None:
    timeline = tmp_path / "linked_long_gap.wav"
    fitted_segments = _write_linked_timeline(timeline, gap_seconds=0.82)
    work = tmp_path / "segment_work"

    with pytest.raises(RuntimeError, match="linked_phrase_gap|connected_phrase_gap"):
        verify_timeline_delivery(timeline, fitted_segments)

    assert load_retry_epoch(work, 1) == 1
    assert load_retry_epoch(work, 2) == 0
    assert not fitted_segments[0][1].exists()
    assert fitted_segments[1][1].exists()


def test_accepts_natural_gap_between_linked_srt_lines(tmp_path: Path) -> None:
    timeline = tmp_path / "linked_natural_gap.wav"
    fitted_segments = _write_linked_timeline(timeline, gap_seconds=0.22)

    report = verify_timeline_delivery(timeline, fitted_segments)

    assert report["passed"] is True
    assert report["policy"] == POLICY
    assert report["segments"][0]["gap_to_next_seconds"] <= 0.32
