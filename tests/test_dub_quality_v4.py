from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools.voxcpm2.dub_quality_v4 import (
    MAX_INTERNAL_GAP_SECONDS,
    build_render_segments_v4,
    group_cues_v4,
    group_ready_srt_v4,
)
from tools.voxcpm2.semantic_tts_guard_v4 import measure_timing_quality
from tools.voxcpm2.voxcpm2_quality_v4_renderer import trim_candidate_edges


def _cue(start: float, end: float, text: str):
    return SimpleNamespace(start=start, end=end, text=text)


def test_gemini_groups_are_short_local_timing_anchors() -> None:
    cues = [
        _cue(0.0, 1.8, "First thought."),
        _cue(1.9, 3.8, "Second thought."),
        _cue(3.9, 5.9, "Third thought."),
        _cue(6.0, 8.0, "Fourth thought."),
    ]
    groups = group_cues_v4(cues)
    assert len(groups) >= 2
    assert all(float(item["end"]) - float(item["start"]) <= 7.25 for item in groups)


def test_overlong_unpunctuated_cue_is_split_without_losing_words() -> None:
    words = [f"word{index}" for index in range(40)]
    groups = group_cues_v4([_cue(0.0, 20.0, " ".join(words))])
    assert len(groups) >= 3
    assert all(float(item["end"]) - float(item["start"]) <= 7.25 for item in groups)
    assert " ".join(item["english"] for item in groups).split() == words


def test_ready_srt_groups_adjacent_cues_into_bounded_semantic_breaths() -> None:
    groups = group_ready_srt_v4(
        [
            _cue(0.0, 2.8, "Первая фраза."),
            _cue(3.1, 5.9, "Вторая фраза."),
            _cue(6.2, 9.0, "Третья фраза."),
        ]
    )

    assert len(groups) == 2
    assert " ".join(item["source"] for item in groups) == (
        "Первая фраза. Вторая фраза. Третья фраза."
    )
    assert groups[0]["start"] == 0.0
    assert groups[-1]["end"] == 9.0
    assert all(float(item["end"]) - float(item["start"]) <= 7.04 for item in groups)
    assert any(int(item["source_cue_count"]) == 2 for item in groups)
    assert all(
        float(gap) <= MAX_INTERNAL_GAP_SECONDS
        for item in groups
        for gap in item["internal_gaps"]
    )


def test_ready_srt_splits_long_cue_but_preserves_verbatim_word_order() -> None:
    words = [f"слово{index}" for index in range(36)]
    groups = group_ready_srt_v4([_cue(0.0, 18.0, " ".join(words))])
    assert len(groups) == 3
    assert all(float(item["end"]) - float(item["start"]) <= 7.25 for item in groups)
    assert " ".join(item["source"] for item in groups).split() == words


def test_render_segments_never_overrun_their_source_windows() -> None:
    groups = [
        {"id": 1, "start": 0.0, "end": 1.2, "source": "First."},
        {"id": 2, "start": 1.2, "end": 3.0, "source": "Second."},
    ]
    translations = [
        {"id": 1, "russian": "Первая."},
        {"id": 2, "russian": "Вторая."},
    ]
    segments, subtitles = build_render_segments_v4(
        groups,
        translations,
        delay_ms=420,
        duration=3.0,
    )
    assert segments[0]["start_delay_ms"] < 420
    assert segments[1]["start_delay_ms"] == 420
    for segment, source in zip(segments, groups, strict=True):
        audible_end = (
            float(segment["start"])
            + int(segment["start_delay_ms"]) / 1000.0
            + (float(segment["end"]) - float(segment["start"]))
        )
        assert abs(audible_end - float(source["end"])) <= 0.002
    assert subtitles[0].end <= subtitles[1].start


def test_edge_trim_skips_isolated_chirp_and_stabilizes_preroll() -> None:
    sample_rate = 16000
    audio = np.zeros(sample_rate * 2, dtype=np.float32)
    audio[240:244] = 0.75
    start = int(sample_rate * 0.55)
    time = np.arange(int(sample_rate * 0.70), dtype=np.float32) / sample_rate
    audio[start : start + len(time)] = 0.12 * np.sin(2 * np.pi * 180 * time)
    trimmed, report = trim_candidate_edges(audio, sample_rate)
    assert report["trimmed_leading"] > 0.40
    assert 0.65 < len(trimmed) / sample_rate < 1.05


def test_edge_trim_rejects_thirty_ms_chirp_before_real_speech() -> None:
    sample_rate = 16000
    audio = np.zeros(sample_rate * 2, dtype=np.float32)
    chirp_start = int(sample_rate * 0.10)
    chirp_time = np.arange(int(sample_rate * 0.03), dtype=np.float32) / sample_rate
    audio[chirp_start : chirp_start + len(chirp_time)] = 0.25 * np.sin(2 * np.pi * 3500 * chirp_time)
    speech_start = int(sample_rate * 0.55)
    speech_time = np.arange(int(sample_rate * 0.70), dtype=np.float32) / sample_rate
    audio[speech_start : speech_start + len(speech_time)] = 0.12 * np.sin(2 * np.pi * 180 * speech_time)

    trimmed, report = trim_candidate_edges(audio, sample_rate)

    assert report["trimmed_leading"] > 0.40
    assert len(trimmed) / sample_rate < 1.05


def test_timing_qa_rejects_thirty_ms_chirp_before_real_speech() -> None:
    sample_rate = 16000
    audio = np.zeros(sample_rate, dtype=np.float32)
    chirp_start = int(sample_rate * 0.02)
    chirp_time = np.arange(int(sample_rate * 0.03), dtype=np.float32) / sample_rate
    audio[chirp_start : chirp_start + len(chirp_time)] = 0.30 * np.sin(2 * np.pi * 3500 * chirp_time)
    speech_start = int(sample_rate * 0.12)
    speech_time = np.arange(int(sample_rate * 0.70), dtype=np.float32) / sample_rate
    audio[speech_start : speech_start + len(speech_time)] = 0.12 * np.sin(2 * np.pi * 180 * speech_time)

    result = measure_timing_quality(audio, sample_rate)

    assert result["passed"] is False
    assert result["isolated_start_artifact"] is True
    assert any(item["suspicious"] for item in result["pre_speech_bursts"])


def test_timing_qa_accepts_stable_onset_and_rejects_late_onset() -> None:
    sample_rate = 16000
    stable = np.zeros(sample_rate, dtype=np.float32)
    stable_start = int(sample_rate * 0.065)
    stable_end = int(sample_rate * 0.88)
    time = np.arange(stable_end - stable_start, dtype=np.float32) / sample_rate
    stable[stable_start:stable_end] = 0.12 * np.sin(2 * np.pi * 180 * time)
    assert measure_timing_quality(stable, sample_rate)["passed"] is True

    late = np.zeros(sample_rate, dtype=np.float32)
    late_start = int(sample_rate * 0.35)
    late_time = np.arange(stable_end - late_start, dtype=np.float32) / sample_rate
    late[late_start:stable_end] = 0.12 * np.sin(2 * np.pi * 180 * late_time)
    result = measure_timing_quality(late, sample_rate)
    assert result["passed"] is False
    assert result["onset_ms"] > 220


def test_timing_qa_rejects_isolated_click_before_speech() -> None:
    sample_rate = 16000
    audio = np.zeros(sample_rate, dtype=np.float32)
    speech_start = int(sample_rate * 0.065)
    speech_end = int(sample_rate * 0.88)
    time = np.arange(speech_end - speech_start, dtype=np.float32) / sample_rate
    audio[speech_start:speech_end] = 0.12 * np.sin(2 * np.pi * 180 * time)
    audio[int(sample_rate * 0.015) : int(sample_rate * 0.015) + 4] = 0.8
    result = measure_timing_quality(audio, sample_rate)
    assert result["passed"] is False
    assert result["isolated_start_artifact"] is True


def test_quality_v4_entrypoints_disable_legacy_prompt_guard() -> None:
    gemini = Path("tools/voxcpm2/generic_gemini_runtime.py").read_text(encoding="utf-8")
    direct = Path("tools/voxcpm2/generic_direct_checked_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "legacy_semantic_guard.install = _disable_legacy_guard_install" in gemini
    assert "legacy_semantic_guard.install = _disable_legacy_guard_install" in direct
    assert "semantic_tts_guard_v4.install()" in gemini
    assert "semantic_tts_guard_v4.install()" in direct


def test_quality_v4_has_no_nested_prompt_retry_or_whole_mix_loudnorm() -> None:
    guard = Path("tools/voxcpm2/semantic_tts_guard_v4.py").read_text(encoding="utf-8")
    renderer = Path("tools/voxcpm2/voxcpm2_quality_v4_renderer.py").read_text(
        encoding="utf-8"
    )
    master = Path("tools/voxcpm2/master_quality_v4.py").read_text(encoding="utf-8")
    assert 'env["VOXCPM_PROMPT_TEXTS_JSON"]' not in guard
    assert 'env.pop("VOXCPM_PROMPT_TEXTS_JSON", None)' in guard
    assert 'values["retry_badcase"]' not in renderer
    assert "pipeline_signature" in guard
    assert 'whole_mix_loudnorm": False' in master
    assert "alimiter=limit=0.985:level=false" in master
