from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.voxcpm2 import direct_max_quality_render as render
from tools.voxcpm2.direct_max_quality_analysis import candidate_hard_ok, required_tempo
from tools.voxcpm2.direct_max_quality_io import (
    MAX_TEMPO,
    MIN_SPEECH_SLOT_SECONDS,
    SPEECH_SLOT_POLICY,
    read_segments,
    speech_slot_seconds,
)


def _candidate(duration: float, *, actual_slot: float) -> dict[str, Any]:
    return {
        "duration": duration,
        "actual_speech_slot": actual_slot,
        "clipping_ratio": 0.0,
        "activity": {"active_ratio": 0.82, "max_internal_gap": 0.0},
        "pitch": {"voiced_ratio": 0.76, "f0_median": 120.0, "f0_p90": 145.0},
        "voice_match": {
            "f0_median_ratio": 1.0,
            "f0_p90_ratio": 1.0,
            "spectral_similarity": 0.92,
        },
        "tail_info": {"suspicious": False},
    }


def test_short_cue_uses_exact_slot_instead_of_one_second_floor() -> None:
    slot = speech_slot_seconds(0.35, 0.22)

    assert SPEECH_SLOT_POLICY == "exact-srt-slot-minus-tail-v1"
    assert MIN_SPEECH_SLOT_SECONDS == pytest.approx(0.12)
    assert slot == pytest.approx(0.13)


def test_impossible_tail_guard_fails_before_synthesis() -> None:
    with pytest.raises(RuntimeError, match="не оставляет безопасного времени"):
        speech_slot_seconds(0.30, 0.20)


def test_candidate_gate_prefers_recorded_actual_slot_over_legacy_argument() -> None:
    slot = speech_slot_seconds(0.35, 0.22)
    acceptable = _candidate(slot * 1.35, actual_slot=slot)
    too_long = _candidate(slot * (MAX_TEMPO + 0.02), actual_slot=slot)

    # The provisional argument deliberately reproduces the old fake 1-second
    # caller. The candidate's exact cue slot must win.
    assert candidate_hard_ok(acceptable, 1.0) is True
    assert required_tempo(acceptable, 1.0) == pytest.approx(1.35)
    assert candidate_hard_ok(too_long, 1.0) is False
    assert required_tempo(too_long, 1.0) > MAX_TEMPO


def test_segment_reader_persists_exact_slot_and_rejects_unfit_window(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "start": 0.0,
                    "end": 0.35,
                    "tail_guard": 0.22,
                    "text": "Да.",
                    "reference_profile": "extended",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    segment = read_segments(valid)[0]
    assert segment["speech_slot"] == pytest.approx(0.13)
    assert segment["speech_slot_policy"] == SPEECH_SLOT_POLICY

    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "start": 0.0,
                    "end": 0.30,
                    "tail_guard": 0.20,
                    "text": "Да.",
                    "reference_profile": "extended",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Сегмент #1:.*безопасного времени"):
        read_segments(invalid)


def test_fitter_builds_atempo_for_real_short_slot_without_trimming_words(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean.wav"
    fitted = tmp_path / "fitted.wav"
    durations = iter((0.18, 0.35))
    commands: list[list[str]] = []

    monkeypatch.setattr(render._legacy, "probe_duration", lambda _path: next(durations))
    monkeypatch.setattr(
        render._legacy,
        "run_checked",
        lambda command, **_kwargs: commands.append(list(command)),
    )

    report = render.fit_without_slowdown(clean, fitted, 0.35, 0.22)

    assert report["speech_slot"] == pytest.approx(0.13)
    assert report["speech_slot_policy"] == SPEECH_SLOT_POLICY
    assert report["tempo"] == pytest.approx(0.18 / 0.13)
    filter_graph = commands[0][commands[0].index("-af") + 1]
    assert "atempo=" in filter_graph
    assert "atrim=duration=0.350000" in filter_graph
    assert "max(1.0" not in Path(render._legacy.__file__).read_text(encoding="utf-8")
