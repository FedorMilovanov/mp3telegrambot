"""Regression tests for v3 patch 30 — canonical source title registry."""

from core.source_titles import (
    AUTHOR_CANONICAL,
    OFFICIAL_RU_TITLES,
    canonical_author_name,
    correct_known_ru_title,
    dedupe_bilingual_authors,
    normalize_source_card_line,
    official_ru_title,
)
from core.text_utils import normalize_source_map_text
from converters.md_telegraph import _postprocess_telegraph_nodes


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_source_title_registry_contains_official_titles_and_authors():
    assert AUTHOR_CANONICAL["John MacArthur"] == "Джон МакАртур"
    assert OFFICIAL_RU_TITLES[("John MacArthur", "Strange Fire")] == "Чуждый огонь"
    assert official_ru_title("John MacArthur", "Strange Fire") == "Чуждый огонь"
    assert canonical_author_name("John Owen") == "Джон Оуэн"


def test_source_title_registry_corrects_wrong_ru_title_but_prefers_original_card_title():
    assert correct_known_ru_title("Странный огонь") == "Чуждый огонь"
    assert normalize_source_card_line(
        "• Джон МакАртур, Странный огонь (John MacArthur, Strange Fire)."
    ) == "• **Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."
    assert normalize_source_map_text(
        "• Джон МакАртур, Чуждый огонь (John MacArthur, Strange Fire)."
    ) == "• **Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."


def test_source_title_registry_dedupes_generic_bilingual_authors():
    line = "• Кевин ДеЯнг, Грег Гилберт, Greg Gilbert, What Is the Mission of the Church?."
    assert normalize_source_card_line(line).startswith(
        "• **What Is the Mission"
    )
    assert "Greg Gilbert" not in normalize_source_card_line(line)

    assert dedupe_bilingual_authors("Джон Оуэн, John Owen, The Death of Death") == (
        "Джон Оуэн, The Death of Death"
    )


def test_source_title_registry_canonicalizes_english_author_source_cards():
    assert normalize_source_card_line("• John Owen, The Death of Death in the Death of Christ") == (
        "• **Смерть смерти в смерти Христа**, Джон Оуэн (The Death of Death in the Death of Christ, John Owen)."
    )
    assert normalize_source_card_line("John Calvin, Commentary on Isaiah") == (
        "**Комментарии на Исаию**, Жан Кальвин (Commentary on Isaiah, John Calvin)."
    )


def test_telegraph_postprocess_uses_source_title_registry():
    nodes = [
        {"tag": "p", "children": ["• John Owen, The Death of Death in the Death of Christ"]},
        {"tag": "p", "children": ["• Джон МакАртур, Странный огонь (John MacArthur, Strange Fire)."]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    flat = "\n".join(_flat(n) for n in out)
    assert "Смерть смерти в смерти Христа, Джон Оуэн (The Death of Death in the Death of Christ, John Owen)" in flat
    assert "Странный огонь" not in flat
    assert "Чуждый огонь, Джон МакАртур (Strange Fire, John MacArthur)" in flat
    assert any(isinstance(c, dict) and c.get("tag") in ("b", "strong") for c in out[0].get("children", []))
    assert any(isinstance(c, dict) and c.get("tag") in ("b", "strong") for c in out[1].get("children", []))
