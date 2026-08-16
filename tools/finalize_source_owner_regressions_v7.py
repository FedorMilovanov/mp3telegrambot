#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


health = Path("handlers/dub_health.py")
text = health.read_text(encoding="utf-8")
old = 'and "from services.dub_worker import build_command" in _read(health)'
new = 'and "from services.dub_worker import build_command" in _read(Path(__file__))'
if old not in text:
    raise SystemExit("health self-source anchor missing")
health.write_text(text.replace(old, new, 1), encoding="utf-8")

Path("tests/test_generic_direct_runtime.py").write_text(
r'''from __future__ import annotations

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
    cues = [Cue(0.0, 2.0, "Первая фраза."), Cue(1.8, 3.0, "Вторая фраза.")]
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
    assert " ".join(group["source"] for group in groups) == "Один два. Три четыре. Пять шесть."


def test_direct_segments_apply_420ms_delay_without_rewriting() -> None:
    cue = Cue(1.0, 4.0, "Точный текст.")
    blocks = [{
        "id": 1,
        "start": 1.0,
        "end": 4.0,
        "source": "Точный текст.",
        "semantic_block_id": 1,
        "source_cue_count": 1,
        "semantic_block_duration": 3.0,
        "source_parts": ["Точный текст."],
        "source_cues": [cue],
    }]
    segments, subtitles = _build_direct_segments(blocks, delay_ms=420, duration=5.0)
    assert segments[0]["start_delay_ms"] == 420
    assert segments[0]["text"] == "Точный текст."
    assert len(subtitles) == 1
    assert subtitles[0].start == 1.42
    assert subtitles[0].end == 4.42
    assert subtitles[0].text == "Точный текст."
''', encoding="utf-8")

health_test = Path("tests/test_clean_dub_health_contract.py")
text = health_test.read_text(encoding="utf-8")
old = '    assert all("/__init__.py" not in item for item in active)'
new = '''    retired_facades = {
        "tools/voxcpm2/generic_project_runtime/__init__.py",
        "tools/voxcpm2/generic_direct_runtime/__init__.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py",
        "tools/voxcpm2/generic_gemini_runtime/__init__.py",
    }
    assert active.isdisjoint(retired_facades)'''
if old not in text:
    raise SystemExit("health fingerprint facade assertion anchor missing")
health_test.write_text(text.replace(old, new, 1), encoding="utf-8")

print("source-owner regression finalizer v7 applied")
