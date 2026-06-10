"""Regression tests for v3 patch 51 — inline segment buttons use subtitle settings too."""

from pathlib import Path


def test_segment_callback_uses_same_subtitle_pipeline_as_cutseg():
    src = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    block = src[src.find('if data.startswith("segcut:")'):src.find("# ── Short trim", src.find('if data.startswith("segcut:")'))]
    assert "segments_subtitles" in block
    assert "transcribe_short_clip" in block
    assert "burn_subtitles_into_short" in block
    assert "HAS_FASTER_WHISPER" in block
    assert "final_clip_path = sub_path" in block
    assert "Segment callback subtitles" in block


def test_segment_callback_cleans_subtitle_temp_file():
    src = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    block = src[src.find('if data.startswith("segcut:")'):src.find("# ── Short trim", src.find('if data.startswith("segcut:")'))]
    assert "sub_path.unlink" in block
    assert "clip_path.unlink" in block
    # 2026-06-10: отправка переведена на Path (file:// при local_mode);
    # проверяем что видео по-прежнему шлётся из final_clip_path
    assert "video=final_clip_path" in block
