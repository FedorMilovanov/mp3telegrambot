"""Regression tests for v3 patch 36 — Telegraph repair tools."""

from pathlib import Path

from core.generated_pages import build_generated_page_record, get_generated_page_record, save_generated_page_record
from services.telegraph_repair import (
    collect_record_page_urls,
    format_batch_repair_results,
    format_repair_results,
    telegraph_path_from_url,
    TelegraphRepairBatchItem,
    TelegraphRepairResult,
)


def test_telegraph_repair_url_path_and_record_url_collection():
    assert telegraph_path_from_url("https://telegra.ph/Page-05-27") == "Page-05-27"
    assert telegraph_path_from_url("https://example.com/Page") == ""
    record = {
        "synopsis_url": "https://telegra.ph/syn",
        "study_url": "https://telegra.ph/study",
        "reflection_url": "https://telegra.ph/study",  # duplicate
        "rutube_url": "https://rutube.ru/video/x/",
    }
    assert collect_record_page_urls(record) == ["https://telegra.ph/syn", "https://telegra.ph/study"]


def test_repair_result_formatter_reports_ok_changed_and_errors():
    text = format_repair_results([
        TelegraphRepairResult(url="https://telegra.ph/a", ok=True, changed=True, audit_summary="warn"),
        TelegraphRepairResult(url="https://telegra.ph/b", ok=False, error="editPage_failed"),
    ])
    assert "1/2 OK" in text
    assert "changed=1" in text
    assert "editPage_failed" in text
    assert "audit: warn" in text


def test_generated_page_exact_lookup_supports_repair_target(tmp_path):
    record = build_generated_page_record(
        video_id="vid-repair",
        source_url="https://youtu.be/x",
        title="T",
        author="A",
        synopsis_url="https://telegra.ph/syn",
        study_url="https://telegra.ph/study",
        publication_status="complete",
    )
    save_generated_page_record(record, base_dir=tmp_path)
    loaded = get_generated_page_record("vid-repair", base_dir=tmp_path)
    assert loaded is not None
    assert collect_record_page_urls(loaded) == ["https://telegra.ph/syn", "https://telegra.ph/study"]


def test_batch_repair_formatter_summarizes_records():
    batch = [TelegraphRepairBatchItem(
        video_id="v1", title="T",
        results=[TelegraphRepairResult(url="https://telegra.ph/a", ok=True, changed=True)],
    )]
    text = format_batch_repair_results(batch)
    assert "records=1" in text
    assert "pages=1/1 OK" in text
    assert "changed=1" in text
    assert "T [v1]" in text


def test_repair_command_is_registered_and_documented():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "async def repairpage_command" in commands
    assert "async def repairrecent_command" in commands
    assert "repair_telegraph_page_url" in commands
    assert "repair_generated_page_record" in commands
    assert "repair_generated_page_records" in commands
    assert "Gemini не вызывается" in commands
    assert 'CommandHandler("repairpage"' in main
    assert 'CommandHandler("repairrecent"' in main


def test_archive_repair_status_updates_markdown_exports(tmp_path):
    from core.generated_pages import update_generated_page_repair_status

    record = build_generated_page_record(
        video_id="repair-meta",
        source_url="https://youtu.be/r",
        title="Repair Meta",
        author="A",
        synopsis_url="https://telegra.ph/syn",
        publication_status="complete",
        created_at=1_777_777_777,
    )
    save_generated_page_record(record, base_dir=tmp_path)
    update_generated_page_repair_status(
        "repair-meta",
        changed_pages=2,
        errors=["editPage_failed"],
        base_dir=tmp_path,
    )
    loaded = get_generated_page_record("repair-meta", base_dir=tmp_path)
    assert loaded["repair_count"] == 1
    assert loaded["last_repair_changed_pages"] == 2
    assert "editPage_failed" in loaded["last_repair_errors"]
    latest = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Repaired:" in latest
    assert "changed=2" in latest
    assert "Repair errors: editPage_failed" in latest


def test_repair_commands_persist_repair_status():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "aupdate_generated_page_repair_status" in commands
    assert "last_repair" not in commands  # persistence is via helper, not manual SQL
