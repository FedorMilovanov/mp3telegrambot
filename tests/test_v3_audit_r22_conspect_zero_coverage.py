#!/usr/bin/env python3
"""AUDIT R22 (regression audit of the whole "surgical bug fix" history):
six specific fixes to the conspect/Telegraph rendering pipeline that had
ZERO test coverage — found by cross-referencing every "audit R*"/BUG-*
commit touching converters/md_telegraph.py and core/text_utils.py against
the existing ~126 test files. Each of these protects one real, already-
shipped fix; none are speculative or "protection for protection's sake".
"""
from converters.md_telegraph import (
    _dedup_consecutive_timestamps,
    _final_telegraph_polish,
    _postprocess_telegraph_nodes,
    _section_to_nodes_v2,
)
from core.text_utils import _fix_latin_homoglyphs


def _flat(nodes) -> str:
    parts = []

    def walk(n):
        if isinstance(n, str):
            parts.append(n)
        elif isinstance(n, dict):
            for c in n.get("children", []) or []:
                walk(c)

    for n in nodes:
        walk(n)
    return "".join(parts)


# ── BUG-4 (commit d10ac8b, broadened by b7973c2): dedup consecutive
# paragraphs that start with the identical timestamp ────────────────────

def test_dedup_consecutive_timestamps_removes_duplicate():
    out = _dedup_consecutive_timestamps("12:34 Первая мысль\n12:34 Вторая мысль")
    assert out == "12:34 Первая мысль\nВторая мысль"


def test_dedup_consecutive_timestamps_handles_bare_timestamp_line():
    """b7973c2: the dedup regex was broadened to also match a line that is
    JUST a timestamp (no trailing text) following an identical one."""
    out = _dedup_consecutive_timestamps("12:34 Текст мысли\n12:34")
    assert out == "12:34 Текст мысли\n"


def test_dedup_consecutive_timestamps_leaves_different_timestamps_alone():
    out = _dedup_consecutive_timestamps("12:34 Первая мысль\n12:40 Вторая мысль")
    assert out == "12:34 Первая мысль\n12:40 Вторая мысль"


# ── BUG-14 (commit 06f3475): bold timestamp glued directly to a closing
# quote/punctuation mark gets a space inserted and loses its bold markers ──

def test_quote_glued_bold_timestamp_gets_space_and_loses_bold():
    nodes = _section_to_nodes_v2(
        {
            "title": "Т", "time": "",
            "content": (
                "Он сказал так: «истина освобождает»**17:28** Далее была "
                "вторая мысль про свободу и благодать в этом отрывке текста."
            ),
        },
        yt_url="https://www.youtube.com/watch?v=x", duration=3000,
    )
    flat = _flat(nodes)
    assert "»**17:28**" not in flat
    assert "» 17:28" in flat


# ── R8 (commit 8f9e88e): single Latin homoglyph letters inside a Cyrillic
# word get fixed, but real English words/acronyms stay untouched ───────────

def test_fix_latin_homoglyphs_repairs_single_letter_substitution():
    assert _fix_latin_homoglyphs("Cемья это опора") == "Семья это опора"


def test_fix_latin_homoglyphs_does_not_touch_real_english_runs():
    """Negative-case guard: a genuine multi-letter Latin run (an acronym,
    even mixed with Cyrillic via a hyphen) must not be mangled — only
    single-letter homoglyph runs are translated."""
    assert _fix_latin_homoglyphs("QA-сессия по богословию") == "QA-сессия по богословию"
    assert _fix_latin_homoglyphs("YouTube канал") == "YouTube канал"


# ── R14 (commit ee112fc): capitalize the first letter after a sentence-
# ending period, but NOT after short abbreviations ("см.", "т.д.") ────────

def test_capitalizes_after_real_sentence_boundary():
    out = _postprocess_telegraph_nodes(
        [{"tag": "p", "children": ["Это было их следствием. возрождение меняет всё."]}]
    )
    assert "следствием. Возрождение" in _flat(out)


def test_does_not_capitalize_after_short_abbreviations():
    """The >=5-letter-word guard exists specifically so "см.", "т.д." etc.
    don't get their next word wrongly capitalized."""
    out = _postprocess_telegraph_nodes(
        [{"tag": "p", "children": ["Смотри об этом подробнее, см. пример дальше в тексте."]}]
    )
    assert "см. пример" in _flat(out)

    out2 = _postprocess_telegraph_nodes(
        [{"tag": "p", "children": ["Обсудим книги, статьи и т.д. другие материалы тоже важны."]}]
    )
    assert "т.д. другие" in _flat(out2)


# ── R9 (commit f3e73f4): LTR-mark (U+200E) padded by whitespace draws a
# visible gap in Hebrew/RTL lexicon cards — collapse the whitespace ───────

def test_ltr_mark_space_before_it_is_collapsed():
    """Existing tests strip \\u200e before comparing (so they can't catch a
    regression of the space-collapsing itself) — this test keeps the mark."""
    out = _final_telegraph_polish([{"tag": "p", "children": ["«enosh» ‎— слабый человек"]}])
    flat = _flat(out)
    assert " ‎" not in flat
    assert "‎" in flat


# ── BUG-10 (commit 7eb81cf, tightened by fd91e0e): stray English
# code-switch words get translated ONLY when adjacent to Cyrillic ────────

def test_stray_english_word_translated_in_cyrillic_context():
    out = _final_telegraph_polish([{"tag": "p", "children": ["Это, however, интересно для нас"]}])
    assert "однако" in _flat(out)
    assert "however" not in _flat(out)


def test_stray_english_word_not_touched_in_all_english_context():
    """fd91e0e regression: an all-English phrase (e.g. a book title) must
    not have words replaced just because they match the code-switch dict."""
    out = _final_telegraph_polish(
        [{"tag": "p", "children": ['The book "However Long the Night" is famous']}]
    )
    assert _flat(out) == 'The book "However Long the Night" is famous'
