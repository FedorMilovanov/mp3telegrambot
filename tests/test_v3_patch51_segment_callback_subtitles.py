"""Regression tests for v3 patch 51 — segment rendering uses shared module."""

from pathlib import Path


def test_segment_callback_uses_shared_render_module():
    """segcut callback delegates to render_and_send_segment instead of inline code."""
    src = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    block = src[src.find('if data.startswith("segcut:")'):src.find("# ── Short trim", src.find('if data.startswith("segcut:")'))]
    assert "render_and_send_segment" in block
    assert "release_render_lock" in block


def test_segment_render_module_has_subtitle_pipeline():
    """services/segment_render.py contains full subtitle pipeline."""
    src = Path("services/segment_render.py").read_text(encoding="utf-8")
    assert "segments_subtitles" in src
    assert "transcribe_short_clip" in src
    assert "burn_subtitles_into_short" in src
    assert "HAS_FASTER_WHISPER" in src
    assert "final_clip_path = sub_path" in src
    assert "clip_path.unlink" in src
    assert "sub_path.unlink" in src
    assert "reply_video" in src
