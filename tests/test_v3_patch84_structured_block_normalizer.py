"""Regression tests for v3 patch 84 — centralized structured block normalization."""

from converters.md_telegraph import _section_to_nodes_v2
from core.content_audit import audit_expanded_sections
from core.structured_blocks import canonical_block_type, normalize_structured_block, normalize_structured_blocks


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_canonical_block_type_aliases_are_centralized():
    assert canonical_block_type("quote") == "pull_quote"
    assert canonical_block_type("blockquote") == "pull_quote"
    assert canonical_block_type("lexical_analysis") == "lexicon"
    assert canonical_block_type("theological_line") == "bullet"
    assert canonical_block_type("source_card") == "source"
    assert canonical_block_type("unknown_model_type") == "paragraph"


def test_normalize_scripture_text_extracts_ref_quote_and_role():
    block = normalize_structured_block({
        "type": "scripture",
        "text": "**2 Тимофею 3:16–17:** *«Всё Писание богодухновенно»*\n\nЭтот текст задаёт стандарт достаточности Писания.",
    })
    assert block["type"] == "scripture"
    assert block["ref"] == "2 Тимофею 3:16–17"
    assert block["quote"] == "Всё Писание богодухновенно"
    assert "стандарт достаточности" in block["role_in_argument"]


def test_normalize_theological_line_to_renderable_bullet():
    block = normalize_structured_block({
        "type": "theological_line",
        "title_original": "Sola Scriptura против внутреннего света",
        "why_relevant": "Показывает, как Лоусон противопоставляет Писание субъективизму.",
    })
    assert block["type"] == "bullet"
    assert "**Sola Scriptura" in block["text"]
    assert "субъективизму" in block["text"]


def test_audit_uses_normalized_blocks_before_schema_warnings():
    sections = [{
        "title": "Ключевые тексты",
        "content": "fallback",
        "blocks": [
            {"type": "scripture", "text": "**2 Тимофею 3:16:** *«Всё Писание богодухновенно»*\n\nРаботает как основание достаточности."},
            {"type": "lexical_analysis", "lemma": "θεόπνευστος", "text": "Указывает на источник Писания в Божьем дыхании."},
            {"type": "quote", "quote": "Писание — единственное безошибочное правило"},
        ],
    }]
    out, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    blocks = out[0]["blocks"]
    assert [b["type"] for b in blocks] == ["scripture", "lexicon", "pull_quote"]
    assert not any(i.code == "block_schema_warning" for i in issues)


def test_renderer_uses_normalized_blocks_for_aliases():
    section = {
        "title": "T",
        "content": "fallback",
        "blocks": normalize_structured_blocks([
            {"type": "scripture", "text": "**2 Тимофею 3:16:** *«Всё Писание богодухновенно»*\n\nРаботает как основание достаточности."},
            {"type": "theological_line", "title_original": "Закрытый канон", "why_relevant": "закрывает идею новых откровений"},
            {"type": "quote", "quote": "Простой пахарь может знать Писание"},
        ]),
    }
    flat = _flat(_section_to_nodes_v2(section))
    assert "2 Тимофею 3:16" in flat
    assert "Работает как основание" in flat
    assert "Закрытый канон" in flat
    assert "новых откровений" in flat
    assert "Простой пахарь" in flat
