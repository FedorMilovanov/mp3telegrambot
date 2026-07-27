from __future__ import annotations

from tools.voxcpm2.generic_direct_runtime import (
    _build_direct_segments,
    group_srt_cues,
    normalize_srt_cues,
    parse_srt_text,
)
from tools.voxcpm2.generic_short_production import Cue


def test_parse_srt_preserves_user_words_and_punctuation() -> None:
    text = """1
00:00:00,000 --> 00:00:02,000
<i>[Важно:] Это мой окончательный перевод.</i>

2
00:00:02,000 --> 00:00:04,000
Ничего не переписывать!
"""
    cues = parse_srt_text(text)
    assert [cue.text for cue in cues] == [
        "[Важно:] Это мой окончательный перевод.",
        "Ничего не переписывать!",
    ]


def test_normalize_overlapping_srt_keeps_all_text() -> None:
    cues = [
        Cue(0.0, 2.0, "Первая фраза."),
        Cue(1.8, 3.0, "Вторая фраза."),
    ]
    normalized, adjustments = normalize_srt_cues(cues, 4.0)
    combined = " ".join(cue.text for cue in normalized)
    assert "Первая фраза." in combined
    assert "Вторая фраза." in combined
    assert adjustments


def test_grouping_preserves_every_word_in_order() -> None:
    cues = [
        Cue(0.0, 2.0, "Один два."),
        Cue(2.0, 4.0, "Три четыре."),
        Cue(4.0, 6.0, "Пять шесть."),
    ]
    groups = group_srt_cues(cues)
    assert " ".join(group["source"] for group in groups) == (
        "Один два. Три четыре. Пять шесть."
    )


def test_direct_segments_apply_420ms_delay_without_rewriting() -> None:
    groups = [{"id": 1, "start": 1.0, "end": 4.0, "source": "Точный текст."}]
    segments, subtitles = _build_direct_segments(groups, delay_ms=420, duration=5.0)
    assert segments[0]["start_delay_ms"] == 420
    assert segments[0]["text"] == "Точный текст."
    assert subtitles[0].start == 1.42
    assert subtitles[0].text == "Точный текст."


def test_last_cue_reduces_delay_only_when_video_would_cut_it_off() -> None:
    groups = [{"id": 1, "start": 4.8, "end": 5.0, "source": "Финал."}]
    segments, subtitles = _build_direct_segments(groups, delay_ms=420, duration=5.0)
    assert segments[0]["start_delay_ms"] < 420
    assert segments[0]["end"] <= 5.0
    assert subtitles[0].end <= 5.0
    assert segments[0]["text"] == "Финал."
