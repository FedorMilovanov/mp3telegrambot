"""Regression tests for v3 patch 63 — editorial noise/log/source refinement."""

from pathlib import Path

from core.content_audit import ContentAuditIssue, has_content_audit_warnings
from core.source_packs import get_source_pack
from core.text_utils import normalize_common_typos


def test_content_audit_separates_fixes_from_unresolved_warnings():
    fixes = [
        ContentAuditIssue("normalized_text", "x", "fixed"),
        ContentAuditIssue("third_person_fixed", "x", "fixed"),
    ]
    warnings = fixes + [ContentAuditIssue("translation_semantic_warning", "x", "evil vs lie")]
    assert has_content_audit_warnings(fixes) is False
    assert has_content_audit_warnings(warnings) is True


def test_telegraph_pipelines_log_fix_only_audits_as_info_not_warning():
    pages = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    telegraph = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "has_content_audit_warnings" in pages
    assert "has_content_audit_warnings" in telegraph
    assert "logger.warning if has_content_audit_warnings" in pages
    assert "logger.warning if has_content_audit_warnings" in telegraph


def test_source_pack_isaiah_servant_no_longer_forces_chou():
    pack = get_source_pack(["Исаия 53", "Раб Иеговы"], "Страдающий Раб", duration_seconds=3600)
    assert "isaiah_servant" in pack
    assert "Chou —" not in pack
    assert "Oswalt — The Book of Isaiah" in pack
    assert "реформаторы/пуритане не являются диспенсационалистами" in pack


def test_russian_title_specific_normalization():
    assert normalize_common_typos("Вопросы и Ответы") == "Вопросы и ответы"
    assert normalize_common_typos("Свидания, Брак и Семейная Жизнь") == "Свидания, брак и семейная жизнь"
