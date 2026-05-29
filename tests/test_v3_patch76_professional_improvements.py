"""Regression tests for v3 patch 76 — professional follow-up improvements."""

import sqlite3

from core.content_audit import audit_expanded_sections, has_content_audit_warnings
from core.generated_pages import build_generated_page_record, get_generated_page_record, save_generated_page_record
from core.person_names import canonical_person_name
from core.source_titles import canonical_author_name, normalize_source_card_line
from core.synopsis_quality import (
    filter_organizational_announcement_sections,
    format_synopsis_quality_issues,
    looks_like_organizational_announcement,
)


def test_person_names_and_source_titles_share_canonical_display_names():
    assert canonical_person_name("Р. Ч. Спрол") == "Р. Ч. Спроул"
    assert canonical_author_name("R.C. Sproul") == "Р. Ч. Спроул"
    assert normalize_source_card_line("• R.C. Sproul, The Holiness of God") == "• **Святость Бога**, Р. Ч. Спроул (The Holiness of God, R.C. Sproul)."


def test_synopsis_organizational_announcement_filter_removes_only_leading_logistics():
    announcement = {
        "title": "Анонс конференции",
        "time": "0:00",
        "content": "Регистрация на конференцию в Grand Rapids откроется сегодня. Reformation Heritage Books приглашает участников.",
    }
    sermon = {
        "title": "Дух Святой оживляет сухие кости",
        "time": "3:42",
        "content": "Чтение Писания и объяснение Иезекииля 37 о духовной смерти и Божьем оживлении.",
    }
    assert looks_like_organizational_announcement(announcement) is True
    filtered, issues = filter_organizational_announcement_sections([announcement, sermon])
    assert filtered == [sermon]
    assert issues[0].code == "synopsis_organizational_announcement_removed"
    assert "organizational" in format_synopsis_quality_issues(issues)

    filtered2, issues2 = filter_organizational_announcement_sections([sermon])
    assert filtered2 == [sermon]
    assert issues2 == []


def test_structured_block_required_fields_warn_but_do_not_drop_section():
    sections = [{
        "title": "Sources",
        "content": "fallback",
        "blocks": [
            {"type": "source", "author": "Джон МакАртур", "why_relevant": "нет title_original"},
            {"type": "lexicon", "lemma": "ἑλκύω"},
            {"type": "paragraph", "text": "Valid paragraph."},
        ],
    }]
    out, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    assert len(out[0]["blocks"]) == 3
    warning_codes = [i.code for i in issues]
    assert warning_codes.count("block_schema_warning") == 2
    assert has_content_audit_warnings(issues) is True


def test_generated_pages_archive_persists_prompt_variant(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPT_EXPERIMENT_TAG", "study-reflection-v1")
    record = build_generated_page_record(
        video_id="variant",
        source_url="https://youtu.be/v",
        title="T",
        author="A",
        synopsis_url="https://telegra.ph/v",
    )
    assert record["prompt_variant"] == "study-reflection-v1"
    save_generated_page_record(record, base_dir=tmp_path)
    loaded = get_generated_page_record("variant", base_dir=tmp_path)
    assert loaded["prompt_variant"] == "study-reflection-v1"
    latest = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Prompt variant: `study-reflection-v1`" in latest
    with sqlite3.connect(tmp_path / "generated_pages.sqlite") as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(generated_pages)").fetchall()]
    assert "prompt_variant" in cols
