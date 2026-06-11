"""Regression tests for v3 patch 85 — typed SourceCard rendering."""

from core.source_titles import SourceCard, build_source_card, normalize_source_card_line, render_source_card
from converters.md_telegraph import _section_to_nodes_v2


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_render_source_card_title_first_with_original_parenthetical():
    card = SourceCard(
        title_ru="Чуждый огонь",
        title_original="Strange Fire",
        author_ru="Джон МакАртур",
        author_original="John MacArthur",
        bullet="• ",
    )
    assert render_source_card(card) == "• **Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."


def test_build_source_card_uses_registry_and_does_not_invent_ru_title():
    known = build_source_card(author="R.C. Sproul", title_original="The Holiness of God", bullet="• ")
    assert render_source_card(known) == "• **Святость Бога**, Р. Ч. Спроул (The Holiness of God, R.C. Sproul)."

    unknown = build_source_card(author="Kevin DeYoung", title_original="What Is the Mission of the Church?", bullet="• ")
    assert render_source_card(unknown) == "• **What Is the Mission of the Church?**, Кевин ДеЯнг (Kevin DeYoung)."


def test_normalize_source_card_line_uses_typed_renderer():
    assert normalize_source_card_line("• John Owen, The Death of Death in the Death of Christ") == (
        "• **Смерть смерти в смерти Христа**, Джон Оуэн (The Death of Death in the Death of Christ, John Owen)."
    )


def test_source_card_normalizes_weird_model_source_shapes():
    assert normalize_source_card_line(
        "• John Bunyan. — Книга использовалась отцом автора для воскресных семейных вечеров, The Pilgrim's Progress."
    ) == (
        "• **Путешествие Пилигрима**, Джон Баньян (The Pilgrim's Progress, John Bunyan). — "
        "Книга использовалась отцом автора для воскресных семейных вечеров."
    )
    assert normalize_source_card_line(
        "• Missionary to the New Hebrides: An Autobiography, John G. Paton, John G. Paton. — "
        "Свидетельство миссионера Патона о верности его отца в молитве показывает непреходящую силу семейного поклонения."
    ) == (
        "• **Missionary to the New Hebrides: An Autobiography**, Джон Патон (John G. Paton). — "
        "Свидетельство миссионера Патона о верности его отца в молитве показывает непреходящую силу семейного поклонения."
    )


def test_structured_source_block_renders_same_title_first_policy():
    section = {
        "title": "Sources",
        "content": "fallback",
        "blocks": [
            {"type": "source", "author": "John MacArthur", "title_original": "Strange Fire", "why_relevant": "Полезна для темы"},
        ],
    }
    flat = _flat(_section_to_nodes_v2(section))
    assert "Чуждый огонь, Джон МакАртур (Strange Fire, John MacArthur)" in flat
    assert "Полезна для темы" in flat
