from __future__ import annotations

import numpy as np

from tools.voxcpm2.direct_russian_cadence import (
    classify_cadence,
    evaluate_candidate_cadence,
    prosody_contour,
)


def _chirp(
    start_hz: float,
    end_hz: float,
    *,
    duration: float = 2.0,
    tail: float = 0.18,
):
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
