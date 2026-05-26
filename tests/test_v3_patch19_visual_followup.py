"""Tests for v3 patch 19 — visual polish follow-up from live runs.

Covers bugs seen after patch 18 review:
- scripture quote followed by a dot must not create a standalone '.' paragraph;
- inline explanation after scripture quote is split cleanly, without leading dot;
- source-map cards must not have one random bold card among plain cards;
- Reflection prompt must not conflict with THIRD_PERSON_BAN by asking to rotate author names.
"""

from converters.md_telegraph import _postprocess_telegraph_nodes, _section_to_nodes_v2


def _p(children):
    return {"tag": "p", "children": children}


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    return ""


def test_scripture_trailing_dot_is_removed_not_split_to_dot_paragraph():
    nodes = [
        _p([
            "• ",
            {"tag": "b", "children": ["1 Тимофею 6:20:"]},
            " ",
            {"tag": "i", "children": ["«О, Тимофей! храни преданное тебе»"]},
            ".",
        ]),
        _p(["Этот отрывок выступает главным императивом."]),
    ]
    out = _postprocess_telegraph_nodes(nodes)
    assert [_flat(n) for n in out] == [
        "• 1 Тимофею 6:20: «О, Тимофей! храни преданное тебе»",
        "Этот отрывок выступает главным императивом.",
    ]


def test_scripture_inline_explanation_strips_leading_dot_after_split():
    nodes = [
        _p([
            "• ",
            {"tag": "b", "children": ["1 Тимофею 6:20:"]},
            " ",
            {"tag": "i", "children": ["«О, Тимофей! храни преданное тебе»"]},
            ". Этот отрывок выступает главным императивом.",
        ])
    ]
    out = _postprocess_telegraph_nodes(nodes)
    assert len(out) == 2
    assert _flat(out[0]) == "• 1 Тимофею 6:20: «О, Тимофей! храни преданное тебе»"
    assert _flat(out[1]) == "Этот отрывок выступает главным императивом."


def test_source_map_section_unwraps_bold_card_heading():
    nodes = _section_to_nodes_v2({
        "title": "Карта источников для дальнейшего изучения",
        "content": "• **Чикагское заявление о безошибочности Писания (1978).**\n\nКлючевой документ.",
    })
    card = next(n for n in nodes if isinstance(n, dict) and n.get("tag") == "p" and _flat(n).startswith("•"))
    assert _flat(card).startswith("• Чикагское заявление")
    assert not any(isinstance(c, dict) and c.get("tag") in ("b", "strong") for c in card.get("children", []))


def test_source_map_unwrap_keeps_italic_book_titles():
    nodes = _section_to_nodes_v2({
        "title": "Карта источников для дальнейшего изучения",
        "content": "• **Джон Оуэн, *Of the Mortification of Sin*.**\n\nПуританская работа.",
    })
    card = next(n for n in nodes if isinstance(n, dict) and n.get("tag") == "p" and _flat(n).startswith("•"))
    assert not any(isinstance(c, dict) and c.get("tag") in ("b", "strong") for c in card.get("children", []))
    assert any(isinstance(c, dict) and c.get("tag") in ("i", "em") for c in card.get("children", []))


def test_reflection_prompt_no_longer_requires_author_name_rotation():
    from core.prompts import REFLECTION_APPLICATION_PROMPT
    assert "Чередуй полное имя и фамилию" not in REFLECTION_APPLICATION_PROMPT
    assert "ПРАВИЛО УПОМИНАНИЯ АВТОРА В REFLECTION" in REFLECTION_APPLICATION_PROMPT
    assert "Когда Джон МакАртур обращается" in REFLECTION_APPLICATION_PROMPT
    assert "перепиши напрямую" in REFLECTION_APPLICATION_PROMPT


def test_source_map_prompt_explicitly_forbids_bold_document_card():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "Чикагское заявление о безошибочности Писания" in STUDY_ANALYSIS_PROMPT
    assert "один источник жирный, остальные нет" in STUDY_ANALYSIS_PROMPT
