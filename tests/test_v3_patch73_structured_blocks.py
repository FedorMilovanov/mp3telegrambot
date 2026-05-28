"""Regression tests for v3 patch 73 — structured expanded-page blocks."""

from converters.md_telegraph import _section_to_nodes_v2
from core.candidate_schema import expanded_page_response_schema
from core.content_audit import audit_expanded_sections
from core.code_health import collect_code_health, format_code_health_report


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_expanded_page_schema_allows_structured_blocks_with_content_fallback():
    schema = expanded_page_response_schema()
    section_props = schema["properties"]["sections"]["items"]["properties"]
    assert "content" in section_props
    assert "blocks" in section_props
    block_props = section_props["blocks"]["items"]["properties"]
    for key in ("type", "text", "ref", "quote", "author", "title_original", "role_in_argument"):
        assert key in block_props
    # Backward compatibility: old parser/rendering still receives content.
    assert "content" in schema["properties"]["sections"]["items"]["required"]


def test_section_to_nodes_prefers_blocks_and_preserves_boundaries():
    section = {
        "title": "Ключевые тексты",
        "time": "",
        "content": "ЭТОТ FALLBACK НЕ ДОЛЖЕН ПОПАСТЬ В РЕНДЕР / / ИНАЧЕ БАГ",
        "blocks": [
            {"type": "paragraph", "text": "Первый отдельный абзац."},
            {"type": "bullet", "text": "Первый отдельный пункт."},
            {"type": "scripture", "ref": "Ин. 6:37", "quote": "Все, что дает Мне Отец, ко Мне придет", "timestamp": "18:32"},
            {"type": "source", "author": "Эндрю Фуллер", "title_original": "The Gospel Worthy of All Acceptation", "why_relevant": "Показывает обязанность веровать."},
            {"type": "lexicon", "lemma": "ἑλκύω", "role_in_argument": "Показывает действенное привлечение Отца."},
        ],
    }
    nodes = _section_to_nodes_v2(section, yt_url="https://youtu.be/x", duration=3600)
    flat = _flat(nodes)
    assert "Ключевые тексты" in flat
    assert "Первый отдельный абзац" in flat
    assert "Первый отдельный пункт" in flat
    assert "Ин. 6:37" in flat
    assert "The Gospel Worthy of All Acceptation" in flat
    assert "ἑλκύω" in flat
    assert "FALLBACK" not in flat
    # h3 + each block should create separate top-level nodes, not one glued paragraph.
    assert len([n for n in nodes if isinstance(n, dict) and n.get("tag") in {"p", "h3"}]) >= 6


def test_content_audit_normalizes_block_text_without_dropping_blocks():
    sections = [{
        "title": "T",
        "content": "fallback",
        "blocks": [
            {"type": "paragraph", "text": "цитата / / Вторая глава книгеNine Marks"},
            {"type": "source", "author": "John MacArthur", "title_original": "Strange Fire", "why_relevant": "труде«Учение»"},
        ],
    }]
    out_sections, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    blocks = out_sections[0]["blocks"]
    assert len(blocks) == 2
    assert "цитата\n\nВторая глава" in blocks[0]["text"]
    assert "книге Nine" in blocks[0]["text"]
    assert "труде «Учение»" in blocks[1]["why_relevant"]
    assert any(i.code == "separator_fixed" for i in issues)


def test_code_health_reports_regex_soft_threshold():
    report = collect_code_health(".")
    assert report.regex_threshold == 300
    text = format_code_health_report(".")
    assert "regex" in text.lower()
    assert "threshold" in text.lower() or "порог" in text.lower()
