"""Regression tests for v3 patch 24 — generated pages archive exports."""

import json
import sqlite3
from pathlib import Path

from core.generated_pages import (
    build_generated_page_record,
    extract_scripture_refs,
    save_generated_page_record,
)


def test_generated_pages_archive_writes_sqlite_jsonl_and_markdown(tmp_path):
    record = build_generated_page_record(
        video_id="vid1",
        source_url="https://youtu.be/x",
        title="Миссия Церкви",
        author="Том Пеннингтон",
        format_name="sermon",
        duration=123,
        youtube_url="https://youtu.be/x",
        rutube_url="https://rutube.ru/video/y/",
        synopsis_url="https://telegra.ph/syn",
        study_url="https://telegra.ph/study",
        reflection_url="https://telegra.ph/refl",
        hashtags=["МиссияЦеркви", "ВеликоеПоручение"],
        key_categories=["ecclesiology"],
        scripture_refs=["Мф. 28:16–20"],
        publication_status="complete",
        model="gemini-x",
        prompt_version="fp",
        created_at=1_777_777_777,
    )

    save_generated_page_record(record, base_dir=tmp_path)

    assert (tmp_path / "generated_pages.sqlite").exists()
    assert (tmp_path / "generated_pages.jsonl").exists()
    assert (tmp_path / "latest.md").exists()
    assert (tmp_path / "all_links.md").exists()
    assert (tmp_path / "README.md").exists()
    assert list((tmp_path / "by_date").glob("*.md"))

    latest = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Миссия Церкви" in latest
    assert "https://telegra.ph/study" in latest
    assert "Status: `complete`" in latest

    line = (tmp_path / "generated_pages.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line)["video_id"] == "vid1"

    with sqlite3.connect(tmp_path / "generated_pages.sqlite") as conn:
        row = conn.execute("SELECT title, publication_status FROM generated_pages WHERE video_id='vid1'").fetchone()
    assert row == ("Миссия Церкви", "complete")


def test_generated_pages_archive_upserts_markdown_latest_without_duplicates(tmp_path):
    r1 = build_generated_page_record(
        video_id="same",
        source_url="https://youtu.be/a",
        title="Old",
        author="A",
        synopsis_url="https://telegra.ph/old",
        publication_status="partial",
        created_at=100,
    )
    r2 = build_generated_page_record(
        video_id="same",
        source_url="https://youtu.be/a",
        title="New",
        author="A",
        synopsis_url="https://telegra.ph/new",
        publication_status="complete",
        created_at=200,
    )
    save_generated_page_record(r1, base_dir=tmp_path)
    save_generated_page_record(r2, base_dir=tmp_path)

    latest = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "New" in latest
    assert "https://telegra.ph/new" in latest
    assert "Old" not in latest

    # JSONL remains append-only event log.
    assert len((tmp_path / "generated_pages.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_extract_scripture_refs_from_ai_data():
    ai = {"terms_data": {"scripture": ["Мф. 28:16–20||mission", "Рим. 8:28", "Мф. 28:16–20||dup"]}}
    assert extract_scripture_refs(ai) == ["Мф. 28:16–20", "Рим. 8:28"]


def test_main_pipeline_wires_generated_pages_archive():
    source = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "from core.generated_pages import" in source
    assert "build_generated_page_record(" in source
    assert "await asave_generated_page_record(_archive_record)" in source
    assert "Generated pages archive saved" in source
