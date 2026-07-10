#!/usr/bin/env python3
"""AUDIT R23 (user report, 2026-07-10):

1. "• - убери ты это везде, надоедает" — the "•" bullet in front of every
   Source Card line ("Карта источников") is gone. Fixed at the single
   render choke point (render_source_card forces bullet="") plus the one
   call site that used to hardcode "• " in converters/md_telegraph.py.

2. "**Holiness**, Дж. К. Райл. - и почему всякие такие книги без русского
   перевода?" — Gemini wrote the author's initials as "Дж. К." (К/K) instead
   of the canonical "Дж. Ч." (Ч/Ch, for "Charles"). This unrecognized
   spelling variant broke original_author_name()'s reverse lookup back to
   "J.C. Ryle", which in turn broke official_ru_title()'s lookup of the
   already-known translation "Святость" — the book silently fell back to
   its untranslated English title. Fixed with an alias, same pattern
   already used for R.C. Sproul's К/Ч-initial typos.

3. A live regression FOUND while fixing #1: normalize_source_card_line()'s
   "this line is already a finished canonical card, don't re-touch names
   inside the parenthetical" guard was anchored on a leading "• "/"- " —
   once that bullet stopped being rendered, the guard silently stopped
   matching, and the English parenthetical author (e.g. "John MacArthur")
   got re-translated to Russian ("Джон МакАртур"), producing a duplicate:
   "Джон МакАртур (Strange Fire, Джон МакАртур)". Fixed by making the
   bullet optional in that guard's regex.
"""
from converters.md_telegraph import _structured_blocks_to_nodes_v2
from core.source_titles import (
    build_source_card,
    canonical_author_name,
    normalize_source_card_line,
    official_ru_title,
    original_author_name,
    render_source_card,
)


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


def test_source_card_block_path_has_no_bullet():
    blocks = [{
        "type": "source", "author": "John MacArthur",
        "title_original": "Strange Fire", "why_relevant": "Полезна для темы",
    }]
    flat = _flat(_structured_blocks_to_nodes_v2(blocks, yt_url=""))
    assert "•" not in flat
    # AUDIT R23 regression: the parenthetical original-language author must
    # stay English, not get re-translated into a duplicate Russian name.
    assert "Strange Fire, John MacArthur" in flat
    assert "Джон МакАртур)" not in flat


def test_render_source_card_ignores_bullet_kwarg_entirely():
    card = build_source_card(author="R.C. Sproul", title_original="The Holiness of God", bullet="• ")
    rendered = render_source_card(card)
    assert not rendered.startswith("•")
    assert rendered == "**Святость Бога**, Р. Ч. Спроул (The Holiness of God, R.C. Sproul)."


def test_normalize_source_card_line_strips_bullet_from_raw_model_text():
    """Raw model text (simulating what Gemini might still emit) legitimately
    starts with "•" — the repair function must strip it, not preserve it."""
    out = normalize_source_card_line("• John Owen, The Death of Death in the Death of Christ")
    assert not out.startswith("•")
    assert out == "**Смерть смерти в смерти Христа**, Джон Оуэн (The Death of Death in the Death of Christ, John Owen)."


def test_already_rendered_bulletless_card_is_not_double_processed():
    """The 'already a finished canonical card' guard must recognize the
    NEW bulletless shape too, or names inside the parenthetical get
    silently re-translated on a second pass (the exact regression found
    while fixing #1 above)."""
    already_rendered = "**Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."
    assert normalize_source_card_line(already_rendered) == already_rendered


def test_ryle_wrong_initial_still_resolves_to_known_translation():
    """Gemini wrote "Дж. К. Райл" (K) instead of the canonical "Дж. Ч. Райл"
    (Ch, for "Charles") — this must not break the known official-title
    lookup for "Holiness" -> "Святость"."""
    assert original_author_name("Дж. К. Райл") == "J.C. Ryle"
    assert official_ru_title("Дж. К. Райл", "Holiness") == "Святость"

    card = build_source_card(author="Дж. К. Райл", title_original="Holiness", original_author="Дж. К. Райл")
    rendered = render_source_card(card, trailing_period=False)
    assert rendered == "**Святость**, Дж. Ч. Райл (Holiness, J.C. Ryle)"


def test_ryle_via_normalize_source_card_line():
    assert normalize_source_card_line("• Дж. К. Райл, Holiness") == (
        "**Святость**, Дж. Ч. Райл (Holiness, J.C. Ryle)."
    )
    assert normalize_source_card_line("• J.C. Ryle, Holiness") == (
        "**Святость**, Дж. Ч. Райл (Holiness, J.C. Ryle)."
    )


def test_canonical_author_name_ryle_unaffected():
    assert canonical_author_name("J.C. Ryle") == "Дж. Ч. Райл"
    assert canonical_author_name("Дж. К. Райл") == "Дж. Ч. Райл"
