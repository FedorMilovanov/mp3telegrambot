"""Regression tests for v3 patch 38 — selectable timestamp-based segments."""

from pathlib import Path

from core.segment_planner import (
    build_segments_from_timestamps,
    format_segments_text,
    get_segment_by_index,
    parse_timestamp_lines,
    parse_segment_selection, seconds_to_timestamp,
)


def test_segment_planner_builds_qa_segments_from_timestamp_boundaries():
    timestamps = "0:00 Первый вопрос?\n5:00 Второй вопрос?\n12:00 Третий вопрос?"
    segments = build_segments_from_timestamps(timestamps, duration=20 * 60, format_name="qa", end_padding=5)
    assert len(segments) == 3
    assert segments[0].start == 0
    assert segments[0].end == 295
    assert segments[0].kind == "qa"
    assert segments[-1].end == 1200
    assert get_segment_by_index(segments, 2).title == "Второй вопрос?"


def test_segment_planner_accepts_raw_gemini_timestamp_list_and_formats_text():
    raw = [
        {"time": "0:00", "topic": "**Вступление**"},
        {"time": "3:30", "topic": "Главный тезис"},
    ]
    assert parse_timestamp_lines(raw) == [(0, "Вступление"), (210, "Главный тезис")]
    segments = build_segments_from_timestamps(raw, duration=600, format_name="sermon")
    text = format_segments_text(segments, title="T")
    assert "1. 0:00–3:22" in text
    assert "Тема: Вступление" in text
    assert seconds_to_timestamp(3661) == "1:01:01"


def test_parse_segment_selection_supports_lists_ranges_and_all():
    assert parse_segment_selection("1,3,5", 10) == [1, 3, 5]
    assert parse_segment_selection("2-4", 10) == [2, 3, 4]
    assert parse_segment_selection("all", 10, max_count=3) == [1, 2, 3]
    assert parse_segment_selection("9,2,2,bad", 3) == [2]


def test_segment_inline_callback_is_wired():
    callbacks = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    assert 'data.startswith("segcut:")' in callbacks
    assert "_resolve_segment_source" in callbacks
    assert "render_clip" in callbacks
    assert "reply_video" in callbacks


def test_segments_and_cutseg_commands_are_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "async def segments_command" in commands
    assert "async def cutseg_command" in commands
    assert "build_segments_from_timestamps" in commands
    assert "render_clip" in commands
    assert "download_video_for_shorts" in commands
    assert "parse_segment_selection" in commands
    assert "segments_batch_render" in commands
    assert "segcut:{video_id}:{s.index}" in commands
    assert 'CommandHandler("segments"' in main
    assert 'CommandHandler("cutseg"' in main


def test_segment_plan_export_writes_files(tmp_path):
    from core.generated_pages import save_segment_plan_export

    out = save_segment_plan_export(
        video_id="seg-export",
        title="Q&A Test",
        author="A",
        timestamps="0:00 Вопрос 1\n5:00 Вопрос 2",
        duration=700,
        format_name="qa",
        base_dir=tmp_path,
    )
    assert out["count"] == "2"
    md = Path(out["md"]).read_text(encoding="utf-8")
    js = Path(out["json"]).read_text(encoding="utf-8")
    assert "/cutseg seg-export 1" in md
    assert "Вопрос 1" in md
    assert '"video_id": "seg-export"' in js
