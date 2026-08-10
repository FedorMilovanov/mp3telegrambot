"""Regression coverage for RU-speech-proven Factory cut boundaries."""

import pytest

from services.shorts_factory_timing import (
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
    assert item["livedub_ru_boundary_proof"] == "exact-vot-ru-silencedetect-v2"
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


def test_no_nearby_ru_boundary_rejects_candidate_instead_of_cutting_english():
    with pytest.raises(RuntimeError, match="доказанные русские границы"):
        align_candidates_to_ru_speech(
            [_short()],
            source_duration=200.0,
            speech_intervals=[(120.0, 170.0)],
            delay_seconds=0.0,
        )


def test_long_internal_ru_gap_rejects_untranslated_short_region():
    with pytest.raises(RuntimeError, match="доказанные русские границы"):
        align_candidates_to_ru_speech(
            [_short()],
            source_duration=200.0,
            speech_intervals=[(99.5, 120.0), (125.1, 146.0)],
            delay_seconds=0.0,
        )


def test_unproved_runtime_timeline_has_no_english_timestamp_fallback():
    with pytest.raises(RuntimeError, match="refusing unverified English-timeline cuts"):
        align_factory_livedub_candidates(
            [_short()],
            source_duration=200.0,
        )
