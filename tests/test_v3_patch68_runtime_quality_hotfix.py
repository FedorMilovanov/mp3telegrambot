"""Regression tests for v3 patch 68/69 runtime quality hotfixes."""
import json
import sqlite3

from converters.caption import build_caption
from core.content_audit import audit_expanded_sections, normalize_generated_markdown_separators
from core.generated_pages import (
    build_generated_page_record,
    collect_quality_warnings,
    get_generated_page_record,
    save_generated_page_record,
    save_segment_plan_export,
)
from services.search import _best_match


def test_search_rejects_one_word_author_only_rutube_false_positive():
    results = [{"id": "bad", "title": "Пол Вошер | Покаяние и вера", "duration": 3990}]
    url, score = _best_match(
        results,
        "Вопросы и ответы: Провозглашение Христа народам - Джоэл Бики, Пол Вошер, Абнер Чау",
        3899,
        "rutube",
        log=False,
    )
    assert url is None
    assert score == 0.0


def test_search_caps_long_video_duration_mismatch_for_vk():
    results = [{"url": "https://vk.example/bad", "title": "Shepherds Conference 2025 - GS7 - Пение и Проповедь", "duration": 5408}]
    url, score = _best_match(
        results,
        "Shepherds Conference - Суверенитет и Спасение - Джозайя Грауман",
        3884,
        "vk",
        log=False,
    )
    assert url is None or score < 0.50


def test_markdown_separator_normalization_preserves_paragraph_breaks_and_spacing():
    text = "• **2 Коринфянам 5:14:** цитата / / Вторая глава объясняет. книгеNine Marks Faith Alone.Глубоко\n-Достаточность"
    normalized = normalize_generated_markdown_separators(text)
    assert "цитата\n\nВторая глава" in normalized
    assert "книге Nine" in normalized
    assert "Faith Alone. Глубоко" in normalized
    assert "\n- Достаточность" in normalized

    sections, _, issues = audit_expanded_sections([{"title": "T", "content": text}], label="StudyAnalysis")
    assert "цитата\n\nВторая глава" in sections[0]["content"]
    assert any(i.code == "separator_fixed" for i in issues)
    assert not any(i.code == "normalized_text" for i in issues)


def test_archive_persists_quality_coverage_caption_and_partial_segments(tmp_path):
    ai = {
        "timestamp_coverage_warning": "timestamps cover only 50% of material duration",
        "title_topic_warning": "low title overlap",
        "quality_warnings": ["custom warning"],
        "segments_status": "partial",
    }
    assert collect_quality_warnings(ai) == [
        "timestamp: timestamps cover only 50% of material duration",
        "title-topic: low title overlap",
        "custom warning",
        "segments: partial timestamp coverage",
    ]

    record = build_generated_page_record(
        video_id="qhotfix",
        source_url="https://youtu.be/q",
        title="T",
        author="A",
        synopsis_url="https://telegra.ph/q",
        quality_warnings=collect_quality_warnings(ai),
        timestamp_coverage_ratio=0.50,
        segments_status="partial",
        caption_trim_stage="timestamps_trimmed",
        caption_timestamps_total=22,
        caption_timestamps_shown=7,
    )
    save_generated_page_record(record, base_dir=tmp_path)
    loaded = get_generated_page_record("qhotfix", base_dir=tmp_path)
    assert loaded["quality_warnings"][0].startswith("timestamp:")
    assert loaded["segments_status"] == "partial"
    assert loaded["caption_timestamps_total"] == 22
    latest = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Quality warnings:" in latest
    assert "Segments: `partial`" in latest
    assert "Caption timestamps: 7/22" in latest
    with sqlite3.connect(tmp_path / "generated_pages.sqlite") as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(generated_pages)").fetchall()]
    assert "quality_warnings" in cols
    assert "timestamp_coverage_ratio" in cols


def test_caption_shows_timestamp_coverage_and_trim_notice():
    caption = build_caption(
        "A",
        "T",
        3884,
        10,
        {
            "real_title": "Суверенитет и Спасение",
            "real_author": "Джозайя Грауман",
            "timestamp_coverage_warning": "timestamps cover only 50%",
            "timestamp_coverage_ratio": 0.5,
            "_caption_timestamps_trimmed": True,
        },
        url="https://youtu.be/x",
    )
    assert "Таймкоды покрывают только 50%" in caption
    assert "Таймкоды сокращены" in caption


def test_segment_export_marks_partial_status(tmp_path):
    out = save_segment_plan_export(
        video_id="segpartial",
        title="T",
        timestamps="0:00 Start\n10:00 Middle\n30:00 Last",
        duration=3600,
        segments_status="partial",
        timestamp_coverage_ratio=0.5,
        base_dir=tmp_path,
    )
    payload = json.loads((tmp_path / "segments" / "segpartial_segments.json").read_text(encoding="utf-8"))
    assert payload["segments_status"] == "partial"
    md = (tmp_path / "segments" / "segpartial_segments.md").read_text(encoding="utf-8")
    assert "неполной сетке таймкодов" in md
