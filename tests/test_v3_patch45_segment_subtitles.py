"""Regression tests for v3 patch 45 — subtitles for rendered segments."""

from pathlib import Path

from core.database import SETTINGS_DEFAULTS, SETTINGS_GROUPS, SETTINGS_LABELS


def test_segment_subtitle_setting_declared_and_grouped():
    assert SETTINGS_DEFAULTS["segments_subtitles"] is True
    assert SETTINGS_LABELS["segments_subtitles"] == "💬 Субтитры сегментов"
    assert "segments_subtitles" in dict(SETTINGS_GROUPS)["🧩 Сегменты"]


def test_cutseg_wires_subtitle_pipeline_with_fallback():
    # Subtitle logic is now in services/segment_render.py
    src = Path("services/segment_render.py").read_text(encoding="utf-8")
    assert "transcribe_short_clip" in src
    assert "burn_subtitles_into_short" in src
    assert "HAS_FASTER_WHISPER" in src
    assert 'await asettings_get("segments_subtitles")' in src
    assert "final_clip_path = sub_path" in src
    # cutseg_command delegates to render_and_send_segment
    cmd_src = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "render_and_send_segment" in cmd_src


def test_segments_subtitles_do_not_block_segment_render_setting():
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    cutseg = src[src.find("async def cutseg_command"):]
    assert 'await asettings_get("segments_render")' in cutseg
    # segments_subtitles check is now inside render_and_send_segment
    render_src = Path("services/segment_render.py").read_text(encoding="utf-8")
    assert 'await asettings_get("segments_subtitles")' in render_src
