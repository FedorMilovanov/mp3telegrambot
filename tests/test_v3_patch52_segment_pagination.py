"""Regression tests for v3 patch 52 — paged inline segment menu."""

from pathlib import Path

from core.segment_planner import build_segments_from_timestamps
from handlers.commands import _build_segments_keyboard, _format_segments_page_text


def test_segments_keyboard_paginates_after_eight_items():
    timestamps = "\n".join(f"{i}:00 Segment {i}" for i in range(20))
    segments = build_segments_from_timestamps(timestamps, duration=25 * 60, format_name="qa", end_padding=0, min_segment=10)
    kb1 = _build_segments_keyboard("vid", segments, page=0)
    kb2 = _build_segments_keyboard("vid", segments, page=1)
    assert kb1 is not None and kb2 is not None
    flat1 = [btn.callback_data for row in kb1.inline_keyboard for btn in row]
    flat2 = [btn.callback_data for row in kb2.inline_keyboard for btn in row]
    assert "segcut:vid:1" in flat1
    assert "segcut:vid:8" in flat1
    assert "segpage:vid:1" in flat1
    assert "segcut:vid:9" in flat2
    assert "segpage:vid:0" in flat2


def test_segments_page_text_mentions_page_and_navigation():
    timestamps = "\n".join(f"{i}:00 Segment {i}" for i in range(20))
    segments = build_segments_from_timestamps(timestamps, duration=25 * 60, format_name="qa", end_padding=0, min_segment=10)
    text = _format_segments_page_text("vid", "Title", "qa", segments, page=1)
    assert "стр. 2/3" in text
    assert "/cut vid N" in text
    assert "Листайте кнопками" in text
    assert "9." in text


def test_segpage_callback_is_wired():
    callbacks = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    assert 'data.startswith("segpage:")' in callbacks
    assert "_build_segments_keyboard" in callbacks
    assert "_format_segments_page_text" in callbacks
    assert "edit_message_text" in callbacks
