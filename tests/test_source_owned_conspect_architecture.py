from __future__ import annotations

from pathlib import Path

from core.candidate_schema import expanded_page_response_schema
from core.content_audit import audit_expanded_sections
from core.structured_blocks import normalize_structured_block

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_study_runtime_is_compatibility_only_without_import_hooks():
    src = _src("services/study_synthesis_runtime.py")
    assert "MetaPathFinder" not in src
    assert "sys.meta_path" not in src
    assert "structured_blocks.normalize_structured_block =" not in src
    assert "content_audit.audit_expanded_sections =" not in src


def test_manifest_has_no_conspect_mutator_feature():
    src = _src("services/runtime_manifest.py")
    assert '"conspect-quality-bootstrap"' not in src
    assert "services.conspect_bootstrap" not in src
    assert not (ROOT / "services/conspect_bootstrap.py").exists()


def test_word_study_schema_and_normalization_are_source_owned():
    block = (
        expanded_page_response_schema()["properties"]["sections"]["items"]
        ["properties"]["blocks"]["items"]
    )
    assert "word_study" in block["properties"]["type"]["enum"]
    for field in (
        "scripture_ref", "russian_quote", "russian_focus", "original_form",
        "transliteration", "russian_pronunciation", "grammar", "basic_meaning",
        "meaning_in_context", "limits_of_claim", "source",
    ):
        assert field in block["properties"]

    dropped = normalize_structured_block({"type": "word_study"})
    assert dropped and dropped.get("_drop_word_study") is True

    rendered = normalize_structured_block({
        "type": "word_study",
        "scripture_ref": "Ин. 1:1",
        "russian_focus": "Слово",
        "original_form": "λόγος",
        "lemma": "λόγος",
        "meaning_in_context": "слово в контексте пролога",
        "role_in_argument": "уточняет связь тезиса с текстом",
    })
    assert rendered and rendered["type"] == "paragraph"
    assert "λόγος" in rendered["text"]


def test_content_audit_owns_teacherly_warnings_and_small_time_reconcile():
    sections = [
        {
            "title": title,
            "time": "1:00",
            "content": "⏱ **0:50** " + ("Длинный связный текст. " * 45),
        }
        for title in ("Ключевые понятия", "Источники", "Ключевые тексты")
    ]
    fixed, outline, issues = audit_expanded_sections(
        sections,
        [{"title": item["title"], "time": "1:00"} for item in sections],
        label="StudyAnalysis",
    )
    assert all(item["time"] == "0:50" for item in fixed)
    assert all(item["time"] == "0:50" for item in outline)
    codes = {issue.code for issue in issues}
    assert "section_time_reconciled" in codes
    assert "study_template_architecture_warning" in codes
    assert "study_bold_anchor_missing_warning" in codes


def test_telegraph_owns_teacherly_prompt_and_strict_study_retry_improvement():
    src = _src("services/telegraph_pages.py")
    assert "study_synthesis_policy import TEACHERLY_STUDY_PROMPT as STUDY_ANALYSIS_PROMPT" in src
    assert 'improved = after_warnings < before_warnings' in src
    assert 'label != "StudyAnalysis"' in src


def test_legacy_conspect_modules_have_no_assignment_patchers():
    for rel in (
        "services/conspect_quality_contract.py",
        "services/conspect_audit_runtime.py",
    ):
        src = _src(rel)
        assert ".normalize_structured_block =" not in src
        assert ".audit_expanded_sections =" not in src
        assert ".STUDY_ANALYSIS_PROMPT =" not in src
