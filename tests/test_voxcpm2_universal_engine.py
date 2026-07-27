from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.voxcpm2.production import segmented_voice_clone as engine


def _candidate(*, duration: float, tail: bool = False, clipping: float = 0.0) -> dict:
    return {
        "duration": duration,
        "tail_info": {"suspicious": tail},
        "clipping_ratio": clipping,
        "leading_silence": 0.0,
    }


def test_start_delay_consumes_segment_window(tmp_path: Path) -> None:
    segments_path = tmp_path / "segments.json"
    segments_path.write_text(
        json.dumps(
            [
                {
                    "id": 7,
                    "start": 10.0,
                    "end": 20.0,
                    "start_delay_ms": 250,
                    "tail_guard": 0.4,
                    "text": "Утверждённый русский текст.",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    segment = engine.read_segments(
        segments_path,
        default_max_safe_tempo=1.12,
    )[0]

    assert segment["window_duration"] == pytest.approx(10.0)
    assert segment["placement_duration"] == pytest.approx(9.75)
    assert segment["start"] + 0.25 + segment["placement_duration"] == pytest.approx(
        segment["end"]
    )


def test_duplicate_segment_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            [
                {"id": 1, "start": 0, "end": 2, "text": "Первый блок."},
                {"id": 1, "start": 2, "end": 4, "text": "Второй блок."},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="уникальным"):
        engine.read_segments(path, default_max_safe_tempo=1.12)


def test_delay_that_consumes_whole_window_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "start": 0,
                    "end": 1,
                    "start_delay_ms": 900,
                    "text": "Текст.",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="съедает всё окно"):
        engine.read_segments(path, default_max_safe_tempo=1.12)


def test_good_candidate_does_not_request_retry() -> None:
    candidate = _candidate(duration=7.5)
    assert engine.candidate_flags(candidate, speech_slot=8.0) == []
    assert engine.candidate_has_blocking_failure(candidate, speech_slot=8.0) is False


def test_tail_restart_requests_retry_but_is_not_automatically_fatal() -> None:
    candidate = _candidate(duration=7.5, tail=True)
    assert "tail_restart" in engine.candidate_flags(candidate, speech_slot=8.0)
    assert engine.candidate_has_blocking_failure(candidate, speech_slot=8.0) is False


def test_extreme_duration_is_blocking() -> None:
    assert engine.candidate_has_blocking_failure(
        _candidate(duration=2.0), speech_slot=8.0
    ) is True
    assert engine.candidate_has_blocking_failure(
        _candidate(duration=14.0), speech_slot=8.0
    ) is True


def test_short_clean_audio_is_never_slowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean = tmp_path / "clean.wav"
    fitted = tmp_path / "fitted.wav"
    clean.write_bytes(b"clean")
    fitted.write_bytes(b"fitted")
    durations = {str(clean): 5.0, str(fitted): 9.0}
    captured: list[list[str]] = []
    monkeypatch.setattr(engine, "probe_duration", lambda path: durations[str(path)])
    monkeypatch.setattr(engine, "run_checked", lambda command: captured.append(command))

    report = engine.fit_without_slowdown(
        clean,
        fitted,
        placement_duration=9.0,
        tail_guard=0.5,
        max_safe_tempo=1.12,
    )

    assert report["tempo"] == 1.0
    assert report["slowed_down"] is False
    filter_value = captured[0][captured[0].index("-af") + 1]
    assert "atempo=" not in filter_value


def test_unsafe_tempo_is_rejected_before_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean = tmp_path / "clean.wav"
    fitted = tmp_path / "fitted.wav"
    clean.write_bytes(b"clean")
    monkeypatch.setattr(engine, "probe_duration", lambda _path: 10.0)
    called = False

    def fail_if_called(_command: list[str]) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(engine, "run_checked", fail_if_called)

    with pytest.raises(RuntimeError, match="не помещается"):
        engine.fit_without_slowdown(
            clean,
            fitted,
            placement_duration=8.0,
            tail_guard=0.5,
            max_safe_tempo=1.12,
        )
    assert called is False


def test_checkpoint_rejects_changed_or_corrupted_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    fitted = tmp_path / "fitted.wav"
    fitted.write_bytes(b"audio-v1")
    signature = {"engine_schema": engine.ENGINE_SCHEMA_VERSION, "segment": {"id": 1}}
    monkeypatch.setattr(engine, "probe_duration", lambda _path: 4.0)

    engine.write_checkpoint(
        checkpoint,
        fitted,
        signature=signature,
        report={"selected_attempt": 1},
    )
    assert engine.load_valid_checkpoint(
        checkpoint,
        fitted,
        signature=signature,
        expected_duration=4.0,
    ) == {"selected_attempt": 1}

    fitted.write_bytes(b"audio-v2-corrupted")
    assert engine.load_valid_checkpoint(
        checkpoint,
        fitted,
        signature=signature,
        expected_duration=4.0,
    ) is None


def test_reference_or_model_change_invalidates_signature() -> None:
    segment = {
        "id": 1,
        "text": "Текст.",
        "start": 0.0,
        "end": 5.0,
        "tail_guard": 0.3,
        "start_delay_ms": 0,
        "reference_profile": "extended",
        "minimum_candidates": 1,
        "max_safe_tempo": 1.12,
        "placement_duration": 5.0,
    }
    first = engine.checkpoint_signature(
        segment=segment,
        steps=16,
        cfg=1.8,
        base_seed=100,
        reference_sha256="reference-a",
        model_fingerprint="model-a",
    )
    second = engine.checkpoint_signature(
        segment=segment,
        steps=16,
        cfg=1.8,
        base_seed=100,
        reference_sha256="reference-b",
        model_fingerprint="model-a",
    )
    third = engine.checkpoint_signature(
        segment=segment,
        steps=16,
        cfg=1.8,
        base_seed=100,
        reference_sha256="reference-a",
        model_fingerprint="model-b",
    )
    assert first != second
    assert first != third


def test_tail_restart_cleanup_fades_only_the_detected_tail() -> None:
    samples = np.ones(1000, dtype=np.float32)
    cleaned, trimmed, trim_time = engine.clean_tail_restart(
        samples,
        1000,
        {"suspicious": True, "silence_start": 0.70},
    )
    assert trimmed is True
    assert trim_time == pytest.approx(0.73)
    assert len(cleaned) == 730
    assert cleaned[-1] == pytest.approx(0.0)
