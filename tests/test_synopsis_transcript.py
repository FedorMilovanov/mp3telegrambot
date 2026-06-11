"""Regression tests for YouTube transcript-backed Synopsis."""
from pathlib import Path

from services.youtube_transcript import vtt_to_timed_text


def test_vtt_to_timed_text_parses_and_dedupes_cues():
    raw = """WEBVTT

00:00:01.000 --> 00:00:03.000 align:start position:0%
<v Roger>We need family worship.</v>

00:00:03.000 --> 00:00:05.000
<c>We need family worship.</c>

00:00:06.000 --> 00:00:08.000
Start with Scripture and prayer.
"""
    out = vtt_to_timed_text(raw, chunk_seconds=25)
    assert "[0:01] We need family worship. Start with Scripture and prayer." in out
    assert out.count("We need family worship") == 1


def test_synopsis_wires_youtube_transcript_into_prompt():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "download_youtube_transcript_text" in src
    assert "ОРИГИНАЛЬНАЯ АНГЛИЙСКАЯ СТЕНОГРАММА" in src
    assert "главный текстовый скелет речи" in src
    assert "SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS" in src
    assert "transcript-backed mode" in src
    assert "use_schema=not _transcript_attached" in src
    assert "density retry uses transcript-only text path" in src
    assert "use_schema=False" in src  # density retry should not be schema-compressed


def test_transcript_env_documented():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "SYNOPSIS_YT_TRANSCRIPT=1" in env
    assert "SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS" in env
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "timed transcript" in readme
    assert "структурированной почти-дословной стенограммой" in readme
