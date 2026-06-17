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


def test_source_card_title_author_parenthetical_prefers_original_not_invented_ru_title():
    assert normalize_source_card_line(
        "• Умерщвление греха, Джон Оуэн (Of the Mortification of Sin, John Owen)."
    ) == "• **Of the Mortification of Sin**, Джон Оуэн (John Owen)."
    assert normalize_source_card_line(
        "• Все ради блага, Томас Уотсон (All Things for Good, Thomas Watson)."
    ) == "• **All Things for Good**, Томас Уотсон (Thomas Watson)."
    assert normalize_source_card_line(
        "• Пламенная проповедь, Мартин Ллойд-Джонс (Preaching and Preachers, Martyn Ллойд-Джонс)."
    ) == "• **Preaching and Preachers**, Мартин Ллойд-Джонс (Martyn Lloyd-Jones)."


def test_source_card_known_calvin_institutes_keeps_official_ru_title():
    assert normalize_source_card_line(
        "• Наставление в христианской вере, Жан Кальвин (Institutes of the Christian Religion, John Calvin)."
    ) == "• **Наставление в христианской вере**, Жан Кальвин (Institutes of the Christian Religion, John Calvin)."


def test_source_card_known_religious_affections_keeps_official_ru_title_and_author():
    assert normalize_source_card_line(
        "• Религиозные чувства, Джонатан Эдвардс (Religious Affections, Jonathan Edwards)."
    ) == "• **Религиозные чувства**, Джонатан Эдвардс (Religious Affections, Jonathan Edwards)."


def test_source_card_registry_covers_common_reformed_source_pack_authors():
    assert normalize_source_card_line("• John Murray, Redemption Accomplished and Applied") == (
        "• **Искупление совершённое и применённое**, Джон Мюррей "
        "(Redemption Accomplished and Applied, John Murray)."
    )
    assert normalize_source_card_line("• B.B. Warfield, The Inspiration and Authority of the Bible") == (
        "• **The Inspiration and Authority of the Bible**, Б. Б. Уорфилд (B.B. Warfield)."
    )
    assert normalize_source_card_line("• J.C. Ryle, Holiness") == (
        "• **Святость**, Дж. Ч. Райл (Holiness, J.C. Ryle)."
    )
    assert normalize_source_card_line("• Louis Berkhof, Systematic Theology") == (
        "• **Systematic Theology**, Луис Беркхоф (Louis Berkhof)."
    )


def test_source_pack_author_surnames_have_registry_aliases():
    import re
    from core.source_packs import _PACKS
    from core.source_titles import AUTHOR_CANONICAL

    ignored = {
        "MacArthur Study Bible", "1689 London Baptist Confession", "Westminster Confession of Faith",
    }
    missing = set()
    for items in _PACKS.values():
        for item in items:
            if "—" not in item:
                continue
            head = item.split("—", 1)[0].strip()
            if head in ignored or re.search(r"\d", head):
                continue
            for author in [p.strip() for p in head.split(" and ")]:
                if author and re.search(r"[A-Za-z]", author) and author not in AUTHOR_CANONICAL:
                    missing.add(author)
    assert missing == set()


def test_source_card_surname_aliases_keep_full_original_author_in_parenthetical():
    assert normalize_source_card_line("• Owen, Mortification of Sin") == (
        "• **Mortification of Sin**, Джон Оуэн (John Owen)."
    )
    assert normalize_source_card_line("• Warfield, Inspiration and Authority of the Bible") == (
        "• **Inspiration and Authority of the Bible**, Б. Б. Уорфилд (B.B. Warfield)."
    )


def test_tim_keller_is_silently_dropped_from_source_cards():
    from core.source_titles import is_disallowed_source_author

    assert is_disallowed_source_author("Tim Keller")
    assert normalize_source_card_line("• Tim Keller, Center Church") == ""
    section = {
        "title": "Sources",
        "content": "fallback",
        "blocks": [
            {"type": "source", "author": "Tim Keller", "title_original": "Center Church", "why_relevant": "Не должен появиться."},
            {"type": "source", "author": "John Owen", "title_original": "Mortification of Sin", "why_relevant": "Полезно."},
        ],
    }
    flat = _flat(_section_to_nodes_v2(section))
    assert "Keller" not in flat and "Келлер" not in flat and "Center Church" not in flat
    assert "Оуэн" in flat
