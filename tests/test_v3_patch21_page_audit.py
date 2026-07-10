"""Regression tests for v3 patch 21 — deterministic page audit/polish."""

from converters.caption import build_caption
from converters.md_telegraph import _postprocess_telegraph_nodes
from core.page_audit import audit_telegraph_page
from core.text_utils import (
    find_mixed_greek_cyrillic_tokens,
    has_mixed_greek_cyrillic,
    normalize_common_typos,
    normalize_source_map_text,
    scrub_third_person_phrases,
)


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_patch21_common_live_typos_and_strange_fire_title():
    text = (
        "Стину Лоусону, душевпопечение, авторитет Слово Божьего, "
        "МакАртора, епифанита, Странный огонь"
    )
    assert normalize_common_typos(text) == (
        "Стиву Лоусону, душепопечение, авторитет Слова Божьего, "
        "МакАртура, Епафродита, Чуждый огонь"
    )


def test_patch21_mixed_greek_cyrillic_detector_is_token_based():
    assert has_mixed_greek_cyrillic("μεлеτάω и ὑπόκрисις")
    assert find_mixed_greek_cyrillic_tokens("Русский текст и μελετάω рядом") == []
    assert normalize_common_typos("μεлеτάω; ὑπόκрисις") == "μελετάω; ὑπόκρισις"


def test_patch21_caption_normalizes_title_author_main_topic_and_partial_warning():
    cap = build_caption(
        "Джон МакАртора",
        "Странный огонь и авторитет Слово Божьего",
        100,
        1.0,
        ai_data={
            "real_author": "Джон МакАртора",
            "real_title": "Странный огонь и авторитет Слово Божьего",
            "main_topic": "Ошибка про душевпопечение и Стину Лоусону.",
            "_partial_publication_warning": "Частичный разбор: не созданы конспект, размышление.",
        },
    )
    assert "МакАртора" not in cap
    assert "Странный огонь" not in cap
    assert "Чуждый Огонь" in cap or "Чуждый огонь" in cap
    assert "Слова Божьего" in cap
    assert "душепопечение" in cap
    assert "Стиву Лоусону" in cap
    assert "⚠️ Частичный разбор" in cap


def test_patch21_third_person_scrubber_for_study_leaks():
    text = (
        "Привычное понимание упускает функцию. "
        "Джон МакАртур подчеркивает этот аспект, говоря о том, что верность пастора "
        "должна подтверждаться жизнью."
    )
    out = scrub_third_person_phrases(text)
    assert "Джон МакАртур подчеркивает" not in out
    assert "Верность пастора должна подтверждаться жизнью" in out


def test_patch21_telegraph_postprocess_applies_third_person_and_source_safety():
    nodes = [
        {"tag": "p", "children": ["Джон МакАртур подчеркивает этот аспект, говоря о том, что верность важна."]},
        {"tag": "p", "children": ["• Джон МакАртур, Странный огонь (John MacArthur, Strange Fire)."]},
        {"tag": "p", "children": ["• Кевин ДеЯнг, Грег Гилберт, Greg Gilbert, What Is the Mission of the Church?."]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    flat = "\n".join(_flat(n) for n in out)
    assert "Джон МакАртур подчеркивает" not in flat
    assert "Верность важна" in flat
    assert "Странный огонь" not in flat
    assert "Чуждый огонь, Джон МакАртур (Strange Fire, John MacArthur)" in flat
    source_card = next(n for n in out if "Чуждый огонь" in _flat(n))
    assert any(isinstance(c, dict) and c.get("tag") in ("b", "strong") for c in source_card.get("children", []))
    assert "Грег Гилберт, Greg Gilbert" not in flat
    assert "Кевин ДеЯнг и Грег Гилберт" in flat


def test_patch21_source_map_normalizer_prefers_original_titles():
    assert normalize_source_map_text(
        "• Джон МакАртур, Чуждый огонь (John MacArthur, Strange Fire)."
    ) == "**Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."
    assert normalize_source_map_text(
        "• Кевин ДеЯнг, Грег Гилберт, Greg Gilbert, What Is the Mission of the Church?."
    ).startswith("**What Is the Mission")


def test_patch21_page_audit_reports_unfixed_classes():
    issues = audit_telegraph_page(
        "T",
        [{"tag": "p", "children": ["μεлеτάω. Джон МакАртур подчеркивает этот аспект."]}],
        page_type="study",
    )
    codes = {i.code for i in issues}
    assert "mixed_greek_cyrillic" in codes
    assert "third_person" in codes


def test_page_audit_strict_controls(monkeypatch):
    from core.page_audit import get_page_audit_mode, should_abort_for_page_audit

    issues = audit_telegraph_page(
        "Карта источников",
        [
            {"tag": "h3", "children": ["Карта источников"]},
            {"tag": "p", "children": ["• Джон МакАртур, Странный огонь (John MacArthur, Strange Fire)."]},
        ],
        page_type="study",
    )
    assert any(i.code == "source_map_original_title" for i in issues)

    monkeypatch.delenv("PAGE_AUDIT_MODE", raising=False)
    monkeypatch.delenv("CONTENT_AUDIT_MODE", raising=False)
    assert get_page_audit_mode() == "warn"
    assert should_abort_for_page_audit(issues) is False

    monkeypatch.setenv("PAGE_AUDIT_MODE", "strict")
    monkeypatch.setenv("PAGE_AUDIT_STRICT_CODES", "source_map_original_title")
    assert should_abort_for_page_audit(issues) is False
    monkeypatch.setenv("PAGE_AUDIT_BLOCK_PUBLICATION", "1")
    assert should_abort_for_page_audit(issues) is True

    monkeypatch.setenv("PAGE_AUDIT_MODE", "off")
    assert should_abort_for_page_audit(issues) is False


def test_telegraph_create_edit_wire_page_audit_strict_abort():
    src = __import__("pathlib").Path("converters/md_telegraph.py").read_text(encoding="utf-8")
    assert "should_abort_for_page_audit" in src
    assert "page_audit_strict" in src
