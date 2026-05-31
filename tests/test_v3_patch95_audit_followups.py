#!/usr/bin/env python3
"""Regression tests for audit follow-up fixes (2026-05).

Covers:
- _final_telegraph_polish drops empty container nodes (empty <p>) that would
  otherwise render as stray vertical whitespace, while keeping self-closing
  tags (hr/br/img).
- gemini_analyze exposes a GC-safe background-delete helper (_spawn_safe_delete)
  that retains strong task references in _BG_DELETE_TASKS.
"""
from converters.md_telegraph import _final_telegraph_polish


def test_polish_drops_empty_paragraph_keeps_hr_and_img():
    nodes = [
        {"tag": "p", "children": ["настоящий текст"]},
        {"tag": "p", "children": []},                 # empty -> dropped
        {"tag": "hr"},                                  # self-closing -> kept
        {"tag": "img", "attrs": {"src": "https://x"}},  # img -> kept
        {"tag": "p", "children": ["**"]},               # orphan-only -> empty -> dropped
    ]
    out = _final_telegraph_polish(nodes)
    assert {"tag": "p", "children": []} not in out
    assert {"tag": "hr"} in out
    assert any(n.get("tag") == "img" for n in out)
    assert any(n.get("tag") == "p" and n.get("children") == ["настоящий текст"] for n in out)
    # No empty <p> survives.
    assert all(not (isinstance(n, dict) and n.get("tag") == "p" and not n.get("children")) for n in out)


def test_polish_keeps_self_closing_br():
    out = _final_telegraph_polish([{"tag": "br"}, {"tag": "p", "children": ["x"]}])
    assert {"tag": "br"} in out


def test_gemini_background_delete_helper_is_gc_safe():
    import services.gemini_analyze as g
    assert hasattr(g, "_spawn_safe_delete")
    assert hasattr(g, "_BG_DELETE_TASKS")
    assert isinstance(g._BG_DELETE_TASKS, set)
    # No-op guard: must not raise on empty file_name / None client.
    g._spawn_safe_delete(None, "")
    g._spawn_safe_delete(object(), "")
