"""Regression tests for v3 patch 93 — editorial quality guards."""

from converters.md_telegraph import _section_to_nodes_v2
from core.content_audit import audit_expanded_sections, has_content_audit_warnings
from core.synopsis_quality import audit_synopsis_density, should_retry_synopsis_density


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_study_scripture_block_without_role_warns():
    sections = [{
        "title": "Ключевые тексты",
        "content": "fallback",
        "blocks": [{"type": "scripture", "ref": "2 Тим. 3:16", "quote": "Всё Писание богодухновенно"}],
    }]
    _, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    assert any(i.code == "scripture_role_missing_warning" for i in issues)
    assert has_content_audit_warnings(issues) is True


def test_study_source_and_lexicon_thin_blocks_warn():
    sections = [{
        "title": "Sources and Lexicon",
        "content": "fallback",
        "blocks": [
            {"type": "source", "author": "John MacArthur", "title_original": "Strange Fire"},
            {"type": "lexicon", "lemma": "θεόπνευστος", "role_in_argument": "богодухновенность"},
        ],
    }]
    _, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    codes = {i.code for i in issues}
    assert "source_relevance_missing_warning" in codes
    assert "lexicon_role_thin_warning" in codes


def test_reflection_application_without_anchor_warns():
    sections = [{
        "title": "Практика",
        "content": "fallback",
        "blocks": [{"type": "application", "challenge": "Проверь решение", "concrete_step": "Сверь его с Писанием."}],
    }]
    _, _, issues = audit_expanded_sections(sections, label="ReflectionApplication")
    assert any(i.code == "application_anchor_missing_warning" for i in issues)


def test_scripture_common_misreading_field_removed_by_design():
    """R46: убрано насовсем (не запретом, а удалением инструкции/поля/рендера) —
    оператор счёл «Частая ошибка чтения» ненужным редакторским комментарием.
    Даже если модель по инерции пришлёт это поле — рендер его игнорирует."""
    section = {
        "title": "Ключевые тексты",
        "content": "fallback",
        "blocks": [{
            "type": "scripture",
            "ref": "2 Тимофею 3:16",
            "quote": "Всё Писание богодухновенно",
            "role_in_argument": "Этот текст показывает, что Писание имеет Божий источник и достаточно для наставления церкви.",
            "common_misreading": "читать стих как общее уважение к Библии, а не как утверждение её достаточности.",
        }],
    }
    flat = _flat(_section_to_nodes_v2(section))
    assert "Этот текст показывает" in flat
    assert "Частая ошибка чтения" not in flat
    assert "общее уважение" not in flat


def test_synopsis_density_warns_on_low_voice_and_short_paragraphs():
    sections = [{"title": f"S{i}", "time": f"{i * 5}:00", "content": "коротко"} for i in range(10)]
    issues = audit_synopsis_density(sections, 3600)
    codes = {i.code for i in issues}
    assert "synopsis_too_few_paragraphs" in codes
    assert "synopsis_author_voice_low" in codes
    assert should_retry_synopsis_density(issues, 3600) is True


def test_content_audit_block_locations_are_well_formed():
    sections = [{"title": "Ключевые тексты", "content": "", "blocks": [{"type": "scripture", "ref": "2 Тим. 3:16"}]}]
    _, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    assert any(".blocks[0]" in i.location for i in issues)
    assert not any(i.location.endswith("[0") for i in issues)


def test_expanded_pipeline_has_gemini_content_audit_retry_before_publish():
    src = __import__("pathlib").Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "EXPANDED_CONTENT_AUDIT_RETRY" in src
    assert "_retry_expanded_sections_for_content_audit" in src
    assert "content audit retry requested" in src
    assert "allow_model_fallback=False" in src
    assert "content audit after retry" in src
