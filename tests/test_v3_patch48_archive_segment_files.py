"""Regression tests for v3 patch 48 — archive/segment file export commands."""

from pathlib import Path

from core.generated_pages import (
    get_archive_export_path,
    get_segment_export_paths,
    save_generated_page_record,
    build_generated_page_record,
    save_segment_plan_export,
)


def test_archive_export_path_mapping(tmp_path):
    assert get_archive_export_path("latest", base_dir=tmp_path) == tmp_path / "latest.md"
    assert get_archive_export_path("all", base_dir=tmp_path) == tmp_path / "all_links.md"
    assert get_archive_export_path("jsonl", base_dir=tmp_path) == tmp_path / "generated_pages.jsonl"
    assert get_archive_export_path("sqlite", base_dir=tmp_path) == tmp_path / "generated_pages.sqlite"
    assert get_archive_export_path("unknown", base_dir=tmp_path) is None


def test_segment_export_path_mapping_and_file_creation(tmp_path):
    paths = get_segment_export_paths("abc123", base_dir=tmp_path)
    assert paths["md"].endswith("abc123_segments.md")
    assert paths["json"].endswith("abc123_segments.json")
    out = save_segment_plan_export(
        video_id="abc123",
        title="T",
        timestamps="0:00 A\n1:30 B",
        duration=200,
        format_name="qa",
        base_dir=tmp_path,
    )
    assert Path(out["md"]).exists()
    assert Path(out["json"]).exists()


def test_archivefile_and_segmentfile_commands_are_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "async def archivefile_command" in commands
    assert "async def segmentfile_command" in commands
    assert "get_archive_export_path" in commands
    assert "get_segment_export_paths" in commands
    assert "asave_segment_plan_export" in commands
    assert "reply_document" in commands
    assert 'CommandHandler("archivefile"' in main
    assert 'CommandHandler("segmentfile"' in main


def test_archive_exports_exist_after_save_record(tmp_path):
    record = build_generated_page_record(
        video_id="v",
        source_url="https://youtu.be/v",
        title="T",
        author="A",
        synopsis_url="https://telegra.ph/t",
    )
    save_generated_page_record(record, base_dir=tmp_path)
    assert (tmp_path / "latest.md").exists()
    assert (tmp_path / "all_links.md").exists()
    assert (tmp_path / "generated_pages.jsonl").exists()
    assert (tmp_path / "generated_pages.sqlite").exists()
