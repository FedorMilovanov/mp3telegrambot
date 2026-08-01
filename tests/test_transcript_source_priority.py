from __future__ import annotations

import ast
from pathlib import Path

from tools.voxcpm2.generic_gemini_runtime import (
    clean_manual_caption_line,
    parse_creator_vtt_preserving_text,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "tools" / "voxcpm2" / "generic_project_runtime.py"
CLEAN = ROOT / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def test_manual_captions_are_selected_before_automatic_and_whisper() -> None:
    source = _source(PROJECT)
    choose = source[
        source.index("def choose_caption_track"):
        source.index("def parse_manual_vtt")
    ]
    assert choose.index('manual = metadata.get("subtitles")') < choose.index(
        'automatic = metadata.get("automatic_captions")'
    )
    assert 'return "manual", ranked[0]' in choose
    assert 'return "automatic", ranked[0]' in choose
    assert 'return "whisper", ""' in choose


def test_failed_manual_download_falls_back_to_auto_then_whisper() -> None:
    source = _source(PROJECT)
    acquire = source[
        source.index("def acquire_transcript"):
        source.index("def _compact_context")
    ]
    manual_download = acquire.index("kind=preferred_kind")
    automatic_fallback = acquire.index(
        'used_kind, used_language = "automatic", ranked[0]'
    )
    whisper_fallback = acquire.index("whisper_transcribe_auto")
    assert manual_download < automatic_fallback < whisper_fallback
    assert "pipeline.normalize_cues(cues, duration)" in acquire


def test_creator_vtt_parser_preserves_repetition_and_removes_render_states(
    tmp_path: Path,
) -> None:
    path = tmp_path / "creator.vtt"
    path.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:03.000
<c>Remember</c>
<c>Remember this</c>
<c>Remember this</c>

00:00:03.000 --> 00:00:06.000
Again
And then
Again

00:00:06.000 --> 00:00:07.000
[Music]
""",
        encoding="utf-8",
    )

    cues = parse_creator_vtt_preserving_text(path)

    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (0.0, 3.0, "Remember this"),
        (3.0, 6.0, "Again And then Again"),
    ]
    assert clean_manual_caption_line("<b>Tom &amp; Jerry</b>") == "Tom & Jerry"
    assert clean_manual_caption_line("[Applause]") == ""

    clean = _source(CLEAN)
    assert "production.parse_manual_vtt = checked.parse_creator_vtt_preserving_text" in clean
