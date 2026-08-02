#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


media = read("services/media_delivery_probe.py")
media = replace_once(
    media,
    '''    audio_sample_rate: int = 0
    has_video: bool = False
''',
    '''    audio_sample_rate: int = 0
    audio_codec: str = ""
    has_video: bool = False
''',
    "MediaProbe codec field",
)
media = replace_once(
    media,
    '''        audio_sample_rate=_positive_int(audio.get("sample_rate")),
        has_video=bool(video),
''',
    '''        audio_sample_rate=_positive_int(audio.get("sample_rate")),
        audio_codec=str(audio.get("codec_name") or "").strip().lower(),
        has_video=bool(video),
''',
    "probe codec extraction",
)
media = replace_once(
    media,
    '''        "format=duration:stream=codec_type,duration,width,height,sample_rate",
''',
    '''        "format=duration:stream=codec_type,codec_name,duration,width,height,sample_rate",
''',
    "ffprobe codec request",
)
media = replace_once(
    media,
    '''    if probe.audio_sample_rate != 48000:
        reasons.append("unexpected_audio_sample_rate")

    expected = max(0.0, _finite_float(expected_duration))
''',
    '''    if probe.audio_sample_rate != 48000:
        reasons.append("unexpected_audio_sample_rate")
    if probe.audio_codec != "aac":
        reasons.append("unexpected_audio_codec")

    expected = max(0.0, _finite_float(expected_duration))
''',
    "final audio codec gate",
)
media = replace_once(
    media,
    '''        if silence_duration > max_internal_silence:
''',
    '''        if silence_duration >= max(0.0, max_internal_silence - 0.02):
''',
    "silence threshold tolerance",
)
media = replace_once(
    media,
    '''    intervals = parse_silencedetect(process.stderr, duration=probe.duration)
    return evaluate_highlights_delivery(
''',
    '''    if process.returncode != 0:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": ["silence_probe_failed"],
            "probe": asdict(probe),
            "stderr_tail": (process.stderr or "")[-800:],
        }
    intervals = parse_silencedetect(process.stderr, duration=probe.duration)
    return evaluate_highlights_delivery(
''',
    "silence probe return code",
)
write("services/media_delivery_probe.py", media)

tests = read("tests/test_media_delivery_probe.py")
tests = tests.replace(
    "        audio_sample_rate=96000,\n        has_video=True,",
    "        audio_sample_rate=96000,\n        audio_codec=\"aac\",\n        has_video=True,",
    1,
)
tests = tests.replace(
    "        audio_sample_rate=48000,\n        has_video=True,",
    "        audio_sample_rate=48000,\n        audio_codec=\"aac\",\n        has_video=True,",
    1,
)
tests += '''


def test_non_aac_final_highlights_are_rejected() -> None:
    probe = MediaProbe(
        duration=20.0,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        audio_codec="opus",
        has_video=True,
        has_audio=True,
    )
    report = evaluate_highlights_delivery(
        probe,
        [],
        expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert report["accepted"] is False
    assert "unexpected_audio_codec" in report["reasons"]
'''
write("tests/test_media_delivery_probe.py", tests)

quality_tests = read("tests/test_highlights_quality.py")
quality_tests = replace_once(
    quality_tests,
    '''from services.highlights_quality import (
    _drop_overlaps_and_repeats,
''',
    '''from services.highlights_quality import (
    _drop_overlaps_and_repeats,
    _map_probe_segments_to_source,
''',
    "mapping test import",
)
quality_tests += '''


def test_probe_mapping_drops_no_word_segment_crossing_window_edge() -> None:
    windows = [
        {
            "index": 0,
            "probe_start": 0.0,
            "probe_end": 10.0,
            "source_start": 100.0,
            "source_end": 110.0,
        }
    ]
    mapped = _map_probe_segments_to_source(
        [{"start": -0.6, "end": 1.0, "text": "Чужой контекст.", "words": []}],
        windows,
    )
    assert mapped[0] == []


def test_probe_mapping_clips_word_evidence_to_exact_window() -> None:
    windows = [
        {
            "index": 0,
            "probe_start": 0.0,
            "probe_end": 10.0,
            "source_start": 100.0,
            "source_end": 110.0,
        }
    ]
    mapped = _map_probe_segments_to_source(
        [
            {
                "start": -0.3,
                "end": 1.0,
                "text": "Лишнее Проснитесь.",
                "words": [
                    {"start": -0.2, "end": -0.05, "word": "Лишнее"},
                    {"start": 0.1, "end": 0.8, "word": "Проснитесь."},
                ],
            }
        ],
        windows,
    )
    assert len(mapped[0]) == 1
    assert mapped[0][0]["text"] == "Проснитесь."
    assert mapped[0][0]["start"] == 100.0
    assert mapped[0][0]["words"][0]["start"] == 100.1
'''
write("tests/test_highlights_quality.py", quality_tests)

for transient in (
    ROOT / "tools" / "apply_chat_findings_followup.py",
    ROOT / ".github" / "workflows" / "apply-chat-findings-followup.yml",
):
    try:
        transient.unlink()
    except FileNotFoundError:
        pass
