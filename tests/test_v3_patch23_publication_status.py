"""Regression tests for v3 patch 23 — publication status + audit mode."""

from pathlib import Path

from converters.caption import build_caption
from core.content_audit import (
    ContentAuditIssue,
    get_content_audit_mode,
    should_abort_for_content_audit,
)
from core.publication_status import build_publication_status, missing_to_json


def test_publication_status_complete_partial_failed():
    complete = build_publication_status(
        synopsis_url="s", study_url="st", reflection_url="r",
        expect_study=True, expect_reflection=True,
    )
    assert complete.status == "complete"
    assert complete.missing == ()
    assert complete.warning == ""

    partial = build_publication_status(
        synopsis_url="s", study_url="st", reflection_url="",
        expect_study=True, expect_reflection=True,
    )
    assert partial.status == "partial"
    assert partial.missing == ("reflection",)
    assert "размышление" in partial.warning
    assert missing_to_json(partial.missing) == '["reflection"]'

    failed = build_publication_status(
        synopsis_url="", study_url="", reflection_url="",
        expect_study=True, expect_reflection=True,
    )
    assert failed.status == "failed"
    assert failed.missing == ("synopsis", "study", "reflection")
    assert "не созданы конспект, разбор материала, размышление" in failed.warning


def test_partial_status_warning_is_rendered_by_caption():
    status = build_publication_status(
        synopsis_url="", study_url="https://telegra.ph/study", reflection_url="",
        expect_study=True, expect_reflection=True,
    )
    cap = build_caption(
        "A", "T", 100, 1.0,
        ai_data={
            "real_title": "T",
            "real_author": "A",
            "main_topic": "Краткое описание.",
            "_partial_publication_warning": status.warning,
        },
        study_tg_url="https://telegra.ph/study",
    )
    assert "⚠️ Частичный разбор" in cap
    assert "конспект" in cap
    assert "размышление" in cap


def test_content_audit_mode_defaults_warn_and_strict_aborts(monkeypatch):
    issues = [ContentAuditIssue(
        code="mixed_greek_cyrillic_warning",
        location="Study.sections[0].content",
        message="mixed",
    )]

    monkeypatch.delenv("CONTENT_AUDIT_MODE", raising=False)
    assert get_content_audit_mode() == "warn"
    assert should_abort_for_content_audit(issues) is False

    monkeypatch.setenv("CONTENT_AUDIT_MODE", "strict")
    assert get_content_audit_mode() == "strict"
    assert should_abort_for_content_audit(issues) is True

    monkeypatch.setenv("CONTENT_AUDIT_MODE", "off")
    assert get_content_audit_mode() == "off"
    assert should_abort_for_content_audit(issues) is False


def test_database_schema_and_pipeline_wire_publication_status():
    db = Path("core/database.py").read_text(encoding="utf-8")
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")

    assert "publication_status" in db
    assert "publication_missing" in db
    assert "publication_warning" in db
    assert "publication_status: str = \"unknown\"" in db

    assert "from core.publication_status import build_publication_status" in pipe
    assert "_pub_status = build_publication_status(" in pipe
    assert "publication_status=_pub_status.status" in pipe
    assert "publication_missing=missing_to_json(_pub_status.missing)" in pipe
