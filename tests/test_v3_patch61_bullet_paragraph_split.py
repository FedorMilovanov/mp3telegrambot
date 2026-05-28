"""Regression tests for v3 patch 61 — split glued Study bullet paragraphs."""

from converters.md_telegraph import _postprocess_telegraph_nodes


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    return ""


def test_postprocess_splits_glued_bullet_cards_into_paragraphs():
    nodes = [{
        "tag": "p",
        "children": [
            "• ", {"tag": "b", "children": ["Библейская экклезиология"]},
            " — устройство церкви. • ", {"tag": "b", "children": ["Спасение господством"]},
            " — сотериология. • ", {"tag": "b", "children": ["Церковная дисциплина"]},
            " — исправление согрешающего."
        ],
    }]
    out = _postprocess_telegraph_nodes(nodes)
    paragraphs = [n for n in out if isinstance(n, dict) and n.get("tag") == "p"]
    assert len(paragraphs) == 3
    assert _flat(paragraphs[0]).startswith("• Библейская экклезиология")
    assert _flat(paragraphs[1]).startswith("• Спасение господством")
    assert _flat(paragraphs[2]).startswith("• Церковная дисциплина")


def test_postprocess_does_not_split_single_bullet_paragraph():
    nodes = [{"tag": "p", "children": ["• ", {"tag": "b", "children": ["Один пункт"]}, " — текст."]}]
    out = _postprocess_telegraph_nodes(nodes)
    assert len([n for n in out if isinstance(n, dict) and n.get("tag") == "p"]) == 1
