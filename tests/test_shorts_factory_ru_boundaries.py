"""Regression coverage for RU-speech-proven Factory cut boundaries."""

from pathlib import Path

import pytest

import services.shorts_factory_timing as timing
from services.shorts_factory_timing import (
    RU_BOUNDARY_PROOF,
    align_candidates_to_ru_speech,
    align_factory_livedub_candidates,
    speech_intervals_from_silence_log,
)


def _short(start: float = 100.0, end: float = 145.0) -> dict:
    return {
        "title": "candidate",
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": end - start,
        "start": "1:40",
        "end": "2:25",
    }


def test_silencedetect_log_is_inverted_into_exact_speech_spans():
    stderr = """
[silencedetect @ x] silence_start: 0
[silencedetect @ x] silence_end: 1.200 | silence_duration: 1.200
[silencedetect @ x] silence_start: 10.000
[silencedetect @ x] silence_end: 12.500 | silence_duration: 2.500
[silencedetect @ x] silence_start: 19.000
"""

    assert speech_intervals_from_silence_log(stderr, duration=20.0) == [
        (1.2, 10.0),
        (12.5, 19.0),
    ]


def test_candidate_start_in_translation_gap_moves_to_first_proved_ru_speech():
    aligned = align_candidates_to_ru_speech(
        [_short()],
        source_duration=200.0,
        speech_intervals=[(101.2, 146.0)],
        delay_seconds=0.0,
    )

    assert len(aligned) == 1
    item = aligned[0]
    assert item["start_seconds"] == pytest.approx(101.2)
    assert item["end_seconds"] == pytest.approx(146.08)
    assert item["livedub_ru_boundary_proof"] == RU_BOUNDARY_PROOF
    assert item["livedub_ru_start_shift_seconds"] == pytest.approx(1.2)
    assert item["livedub_ru_speech_coverage"] > 0.99


def test_candidate_end_in_translation_gap_stops_at_last_proved_ru_speech():
    aligned = align_candidates_to_ru_speech(
        [_short(end=149.0)],
        source_duration=200.0,
        speech_intervals=[(99.5, 146.0), (153.0, 170.0)],
        delay_seconds=0.0,
    )

    assert len(aligned) == 1
    item = aligned[0]
    assert item["start_seconds"] == pytest.approx(99.5)
    assert item["end_seconds"] == pytest.approx(146.08)
    assert item["end_seconds"] < 149.0


def test_anchor_inside_long_ru_phrase_stays_ru_instead_of_false_rejection():
    aligned = align_candidates_to_ru_speech(
        [_short(start=110.0, end=150.0)],
        source_duration=200.0,
        speech_intervals=[(100.0, 160.0)],
        delay_seconds=0.0,
    )

    assert len(aligned) == 1
    assert aligned[0]["start_seconds"] == pytest.approx(110.0)
    assert aligned[0]["end_seconds"] == pytest.approx(150.0)


def test_mix_delay_is_applied_even_inside_one_long_ru_phrase():
    aligned = align_candidates_to_ru_speech(
        [_short(start=110.0, end=150.0)],
        source_duration=200.0,
        speech_intervals=[(100.6, 160.6)],
        delay_seconds=0.6,
    )

    assert len(aligned) == 1
    item = aligned[0]
    assert item["livedub_ru_target_start_seconds"] == pytest.approx(110.6)
    assert item["livedub_ru_target_end_seconds"] == pytest.approx(150.6)
    assert item["start_seconds"] == pytest.approx(110.6)
    assert item["end_seconds"] == pytest.approx(150.6)


def test_no_nearby_ru_boundary_rejects_only_that_candidate():
    aligned = align_candidates_to_ru_speech(
        [_short()],
        source_duration=200.0,
        speech_intervals=[(120.0, 170.0)],
        delay_seconds=0.0,
    )

    assert aligned == []


def test_long_internal_ru_gap_rejects_untranslated_short_region():
    aligned = align_candidates_to_ru_speech(
        [_short()],
        source_duration=200.0,
        speech_intervals=[(99.5, 120.0), (125.1, 146.0)],
        delay_seconds=0.0,
    )

    assert aligned == []


def test_source_speech_without_corresponding_ru_rejects_candidate(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC", "1.5")
    aligned = align_candidates_to_ru_speech(
        [_short(start=100.0, end=145.0)],
        source_duration=200.0,
        speech_intervals=[(100.0, 120.0), (123.0, 146.0)],
        delay_seconds=0.0,
        source_speech_intervals=[(100.0, 146.0)],
        source_speech_proof="provider-source-srt+mix-delay",
    )

    assert aligned == []


def test_source_silence_does_not_turn_natural_ru_pause_into_translation_failure():
    aligned = align_candidates_to_ru_speech(
        [_short(start=100.0, end=145.0)],
        source_duration=200.0,
        speech_intervals=[(100.0, 119.0), (125.0, 146.0)],
        delay_seconds=0.0,
        source_speech_intervals=[(100.0, 119.0), (125.0, 146.0)],
        source_speech_proof="provider-source-srt+mix-delay",
    )

    assert len(aligned) == 1
    assert aligned[0]["livedub_source_without_ru_max_burst_seconds"] == 0.0
    assert aligned[0]["livedub_ru_max_internal_gap_seconds"] == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_source_caption_speech_is_shifted_into_ru_mix_timeline(
    monkeypatch,
    tmp_path,
):
    ru_path = tmp_path / "vot.mp3"
    ru_path.write_bytes(b"x" * 2048)
    monkeypatch.setattr(timing, "read_ru_audio_provenance", lambda _root: ru_path)

    async def fake_ru(_path: Path):
        return {
            "audio_name": "vot.mp3",
            "audio_duration_seconds": 60.0,
            "delay_seconds": 0.6,
            "intervals": [(10.6, 20.6)],
        }

    async def fake_source(**_kwargs):
        return [(10.0, 20.0)], "provider-source-srt"

    monkeypatch.setattr(timing, "_detect_exact_ru_speech", fake_ru)
    monkeypatch.setattr(timing, "_download_source_speech_intervals", fake_source)

    evidence = await timing.prepare_factory_ru_boundary_evidence(
        url="https://example.test/video",
        workdir=tmp_path,
        source_language="en",
    )

    assert len(evidence["source_speech_intervals"]) == 1
    shifted_start, shifted_end = evidence["source_speech_intervals"][0]
    assert shifted_start == pytest.approx(10.6)
    assert shifted_end == pytest.approx(20.6)
    assert evidence["source_speech_proof"] == "provider-source-srt+mix-delay"
    assert evidence["proof"] == RU_BOUNDARY_PROOF


@pytest.mark.asyncio
async def test_exact_ru_audio_duration_has_no_metadata_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(timing.shutil, "which", lambda _name: None)

    assert await timing._probe_audio_duration(tmp_path / "missing.mp3") == 0.0


def test_unproved_runtime_timeline_has_no_original_timestamp_fallback():
    with pytest.raises(RuntimeError, match="refusing unverified original-timeline cuts"):
        align_factory_livedub_candidates(
            [_short()],
            source_duration=200.0,
        )
