#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2.dub_worker import build_command
from tools.voxcpm2.generic_short_production import (
    Cue,
    clean_caption_text,
    group_cues,
    parse_timestamp,
    parse_vtt,
    transcript_hash,
    validate_translation,
    write_srt,
)
from tools.voxcpm2.generic_short_runtime import install_runtime_adapters


def test_parse_timestamp_supports_vtt_and_srt() -> None:
    assert parse_timestamp("00:01.250") == pytest.approx(1.25)
    assert parse_timestamp("01:02:03,500") == pytest.approx(3723.5)


def test_clean_caption_text_removes_tags_and_music_marker() -> None:
    assert clean_caption_text("<c> Trust&nbsp;Christ </c> [Music]") == "Trust Christ"


def test_parse_vtt_uses_latest_rolling_caption_line(tmp_path: Path) -> None:
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.000\n"
        "<c>God is good.</c>\n\n"
        "00:00:02.000 --> 00:00:05.000\n"
        "God is good.\n"
        "He saves sinners.\n\n"
        "00:00:05.000 --> 00:00:09.000\n"
        "Trust in Christ.\n",
        encoding="utf-8",
    )
    cues = parse_vtt(vtt)
    assert [cue.text for cue in cues] == ["God is good.", "He saves sinners.", "Trust in Christ."]


def test_group_cues_preserves_order_and_avoids_tiny_tail() -> None:
    cues = [
        Cue(0.0, 3.0, "First sentence."),
        Cue(3.0, 7.0, "Second sentence."),
        Cue(7.0, 9.0, "Last words."),
    ]
    groups = group_cues(cues, target_seconds=5.0, max_seconds=8.0)
    assert [item["id"] for item in groups] == list(range(1, len(groups) + 1))
    assert groups[0]["start"] == 0.0
    assert groups[-1]["end"] == 9.0
    assert all(item["end"] > item["start"] for item in groups)


def test_translation_validation_requires_exact_ids() -> None:
    source = [
        {"id": 1, "start": 0.0, "end": 5.0, "english": "One"},
        {"id": 2, "start": 5.0, "end": 10.0, "english": "Two"},
    ]
    valid = validate_translation(
        {"segments": [{"id": 1, "russian": "Один"}, {"id": 2, "russian": "Два"}]},
        source,
    )
    assert [item["russian"] for item in valid] == ["Один", "Два"]
    with pytest.raises(RuntimeError, match="Нарушены ID"):
        validate_translation({"segments": [{"id": 1, "russian": "Один"}]}, source)


def test_transcript_hash_is_stable_and_changes_with_text() -> None:
    left = [{"id": 1, "start": 0.0, "end": 3.0, "english": "Grace"}]
    right = json.loads(json.dumps(left))
    assert transcript_hash(left) == transcript_hash(right)
    right[0]["english"] = "Truth"
    assert transcript_hash(left) != transcript_hash(right)


def test_write_srt_uses_millisecond_timestamps(tmp_path: Path) -> None:
    output = tmp_path / "out.srt"
    write_srt([Cue(0.42, 3.25, "Русский текст")], output)
    text = output.read_text(encoding="utf-8")
    assert "00:00:00,420 --> 00:00:03,250" in text
    assert "Русский текст" in text


def test_registered_recipe_uses_hardened_runtime_eighteen_percent_and_delay() -> None:
    command, spec = build_command("short_tnliocegylk", "render")
    joined = " ".join(command)
    assert spec["runner"] == "python_module"
    assert "tools.voxcpm2.generic_short_runtime" in joined
    assert "-OriginalLevel 0.18" in joined
    assert "-RussianDelayMs 420" in joined
    assert "-VideoId tNlIoCeGyLk" in joined


def test_runtime_adapters_replace_network_and_translation_routes() -> None:
    import tools.voxcpm2.generic_short_production as production
    import tools.voxcpm2.generic_short_runtime as runtime

    install_runtime_adapters()
    assert production.download_source is runtime.download_source
    assert production.download_captions is runtime.download_captions
    assert production.gemini_json is runtime.gemini_json
