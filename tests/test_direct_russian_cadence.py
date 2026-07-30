from __future__ import annotations

import numpy as np

from tools.voxcpm2.direct_russian_cadence import (
    classify_cadence,
    evaluate_candidate_cadence,
    prosody_contour,
)


def _chirp(start_hz: float, end_hz: float, *, duration: float = 2.0, tail: float = 0.18):
    sample_rate = 48_000
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (end_hz - start_hz) / duration
    phase = 2.0 * np.pi * (start_hz * time + 0.5 * slope * time**2)
    audio = 0.24 * np.sin(phase)
    fade = int(0.05 * sample_rate)
    audio[:fade] *= np.linspace(0.0, 1.0, fade)
    audio[-fade:] *= np.linspace(1.0, 0.0, fade)
    return np.concatenate(
        [audio.astype(np.float32), np.zeros(int(tail * sample_rate), dtype=np.float32)]
    ), sample_rate


def _evaluate(text: str, start_hz: float, end_hz: float):
    audio, sample_rate = _chirp(start_hz, end_hz)
    candidate = {
        "samples": audio,
        "sample_rate": sample_rate,
        "duration": len(audio) / sample_rate,
    }
    segment = {
        "text": text,
        "start": 0.0,
        "end": 2.35,
        "tail_guard": 0.17,
    }
    return evaluate_candidate_cadence(candidate, segment)


def test_classify_russian_cadence_contract():
    assert classify_cadence("Завершение.") == "terminal"
    assert classify_cadence("Удар!") == "firm_terminal"
    assert classify_cadence("Вопрос?") == "question"
    assert classify_cadence("Продолжение,") == "continuation"
    assert classify_cadence("Цитата:") == "continuation"
    assert classify_cadence("Ожидание…") == "suspense"
    assert classify_cadence("Помните мой любимый стих") == "linked"


def test_rising_declarative_is_rejected_but_falling_one_passes():
    rising = _evaluate("И не на то, что выйдет замуж.", 80.0, 200.0)
    falling = _evaluate("И не на то, что выйдет замуж.", 200.0, 80.0)

    assert rising["ending_delta_semitones"] > 1.4
    assert rising["hard_ok"] is False
    assert "terminal_rises" in rising["failures"]
    assert falling["ending_delta_semitones"] < -1.0
    assert falling["hard_ok"] is True
    assert falling["penalty"] < rising["penalty"]


def test_linked_phrase_must_not_close_like_a_full_stop():
    closed = _evaluate("Помните мой любимый стих", 240.0, 60.0)
    open_phrase = _evaluate("Помните мой любимый стих", 100.0, 160.0)

    assert closed["ending_delta_semitones"] < -4.2
    assert closed["hard_ok"] is False
    assert "continuation_closes" in closed["failures"]
    assert open_phrase["hard_ok"] is True


def test_prosody_contour_exposes_five_bin_emphasis_shape():
    audio, sample_rate = _chirp(100.0, 160.0)
    contour = prosody_contour(audio, sample_rate)

    assert contour["available"] is True
    assert len(contour["energy_contour"]) == 5
    assert len(contour["pitch_contour"]) == 5
    assert contour["voiced_ending"] is True


def test_terminal_octave_glitch_is_corrected_before_cadence_decision():
    sample_rate = 48_000
    first_duration = 1.72
    glitch_duration = 0.18
    first_time = np.arange(int(first_duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (112.0 - 170.0) / first_duration
    first_phase = 2.0 * np.pi * (170.0 * first_time + 0.5 * slope * first_time**2)
    first = 0.22 * np.sin(first_phase)
    glitch_time = np.arange(int(glitch_duration * sample_rate), dtype=np.float64) / sample_rate
    glitch = 0.18 * np.sin(2.0 * np.pi * 224.0 * glitch_time)
    audio = np.concatenate(
        [
            first.astype(np.float32),
            glitch.astype(np.float32),
            np.zeros(int(0.18 * sample_rate), dtype=np.float32),
        ]
    )
    result = evaluate_candidate_cadence(
        {
            "samples": audio,
            "sample_rate": sample_rate,
            "duration": len(audio) / sample_rate,
        },
        {
            "text": "Который обещает помочь ей.",
            "start": 0.0,
            "end": 2.18,
            "tail_guard": 0.10,
        },
    )

    assert result["ending_delta_semitones"] < 0.5
    assert "terminal_rises" not in result["failures"]


def _enveloped_chirp(start_hz: float, end_hz: float, start_amp: float, end_amp: float):
    sample_rate = 48_000
    duration = 2.0
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (end_hz - start_hz) / duration
    phase = 2.0 * np.pi * (start_hz * time + 0.5 * slope * time**2)
    envelope = np.linspace(start_amp, end_amp, len(time), dtype=np.float64)
    audio = envelope * np.sin(phase)
    fade = int(0.015 * sample_rate)
    audio[:fade] *= np.linspace(0.0, 1.0, fade)
    audio[-fade:] *= np.linspace(1.0, 0.0, fade)
    return np.concatenate(
        [audio.astype(np.float32), np.zeros(int(0.12 * sample_rate), dtype=np.float32)]
    ), sample_rate


def test_slight_terminal_rise_requires_an_energy_resolution():
    unresolved_audio, sample_rate = _enveloped_chirp(110.0, 132.0, 0.08, 0.28)
    resolved_audio, _ = _enveloped_chirp(110.0, 132.0, 0.28, 0.06)
    segment = {
        "text": "И не на то, что выйдет замуж.",
        "start": 0.0,
        "end": 2.25,
        "tail_guard": 0.13,
    }
    unresolved = evaluate_candidate_cadence(
        {"samples": unresolved_audio, "sample_rate": sample_rate, "duration": len(unresolved_audio) / sample_rate},
        segment,
    )
    resolved = evaluate_candidate_cadence(
        {"samples": resolved_audio, "sample_rate": sample_rate, "duration": len(resolved_audio) / sample_rate},
        segment,
    )

    assert 0.45 < unresolved["ending_delta_semitones"] < 1.40
    assert unresolved["ending_energy_delta_db"] > -1.0
    assert unresolved["hard_ok"] is False
    assert "terminal_not_resolved" in unresolved["failures"]
    assert resolved["ending_energy_delta_db"] < -1.0
    assert "terminal_not_resolved" not in resolved["failures"]
