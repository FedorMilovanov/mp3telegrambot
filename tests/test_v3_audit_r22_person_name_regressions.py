#!/usr/bin/env python3
"""AUDIT R22 (regression audit of the whole "surgical bug fix" history):
two real, live bugs found in core/person_names.py while auditing R20's
consolidation of the known-author registry.

1. `known_author_from_text` matched a known surname as a bare substring
   ANYWHERE in the combined text — including inside an unrelated larger
   word. "The Rich Man and the Beggar" (a real, common sermon topic on
   Luke 16) contains "Begg" inside "Beggar" and was misattributed to
   Alistair Begg; "bagpiper" similarly misattributed to John Piper. The
   substring fallback exists ONLY because names with periods (R.C. Sproul)
   are unreliable with \\b — it must not apply to plain names.

2. `KNOWN_AUTHOR_RU` (used by parse_title()/LiveDub captions) and
   `_PERSON_REPLACEMENTS` (used by normalize_person_names(), a SEPARATE
   caption-polish path) held two independently-maintained canonical
   spellings for the same people — "Мартин Лойд-Джонс" vs "...Ллойд-
   Джонс", "Джоэл Бики" vs "Джоэль Бики" — so the same preacher's name
   could render two different ways depending on which function touched
   the text.

A THIRD, previously-undiscovered instance of the same bug class turned up
while writing these tests: core/source_titles.py::AUTHOR_CANONICAL (used
by canonical_author_name() for Source Card citations in Study/Reflection
pages) independently held the same two stale spellings, and Gemini's own
prompt text (core/prompts.py) taught the model to GENERATE the stale
"Мартин Ллойд-Джонс"/"Джоэл Бики" forms directly — all three sources are
now aligned, plus a defensive normalize_person_names() entry catches any
residual/cached double-л text.
"""
from pathlib import Path

from core.person_names import (
    KNOWN_AUTHOR_RU,
    _PERSON_REPLACEMENTS,
    known_author_from_text,
    normalize_person_names,
)
from core.source_titles import AUTHOR_CANONICAL, canonical_author_name
from core.utils import parse_title


def test_known_author_not_matched_inside_unrelated_word():
    """"Beggar" contains "Begg", "bagpiper" contains "piper" — neither
    should trigger a false author match."""
    assert known_author_from_text("The Rich Man and the Beggar") == ""
    assert known_author_from_text("bagpiper practice session") == ""


def test_known_author_still_matches_real_mentions():
    """The word-boundary fix must not break genuine whole-word matches."""
    assert known_author_from_text("A sermon by Alistair Begg") == "Алистер Бегг"
    assert known_author_from_text("John Piper on prayer") == "Джон Пайпер"
    assert known_author_from_text("Paul Washer preaches") == "Пол Вошер"


def test_known_author_dotted_name_substring_still_works():
    """R.C. Sproul-style names with periods can't rely on \\b (punctuation
    boundary is unreliable) — the substring path must stay for THESE."""
    assert known_author_from_text("Prayer with R.C. Sproul") == "Р. Ч. Спроул"


def test_parse_title_no_longer_mistags_beggar_as_begg():
    """Live-log-shaped regression: a real Luke 16 sermon title must not be
    misattributed to Alistair Begg via the "Beggar" substring bug."""
    performer, title = parse_title(
        "The Rich Man and the Beggar: Luke 16 and the Reality of Hell",
        "Grace Community Church",
    )
    assert performer != "Алистер Бегг"
    assert title != "Алистер Бегг"


def test_known_author_ru_and_person_replacements_spelling_consistent():
    """Any FULL-NAME key present in BOTH registries must have the identical
    canonical RU spelling in each — otherwise the same author renders two
    different ways depending on which function processed the text.

    Restricted to multi-word keys ("Martyn Lloyd-Jones"): a surname-only
    shorthand key in KNOWN_AUTHOR_RU ("Lloyd-Jones") deliberately maps to
    the full display name, while the same key in _PERSON_REPLACEMENTS
    deliberately maps to just the surname (in-place text substitution) —
    that difference is by design, not a spelling desync."""
    replacements = dict(_PERSON_REPLACEMENTS)
    mismatches = [
        (en, ru, replacements[en])
        for en, ru in KNOWN_AUTHOR_RU.items()
        if " " in en and en in replacements and replacements[en] != ru
    ]
    assert not mismatches, f"spelling mismatch between registries: {mismatches}"


def test_lloyd_jones_spelling_consistent_across_both_functions():
    assert known_author_from_text("Martyn Lloyd-Jones") == "Мартин Лойд-Джонс"
    assert normalize_person_names("Martyn Lloyd-Jones").endswith("Лойд-Джонс")
    assert "Ллойд" not in normalize_person_names("Martyn Lloyd-Jones")


def test_beeke_spelling_consistent_across_both_functions():
    assert known_author_from_text("Joel Beeke") == "Джоэль Бики"
    assert normalize_person_names("Joel Beeke") == "Джоэль Бики"


def test_known_author_ru_and_author_canonical_spelling_consistent():
    """core/source_titles.py::AUTHOR_CANONICAL is a THIRD, independent
    registry (Source Card citations) — must agree with KNOWN_AUTHOR_RU on
    every full-name key shared between them."""
    mismatches = [
        (en, ru, AUTHOR_CANONICAL[en])
        for en, ru in KNOWN_AUTHOR_RU.items()
        if " " in en and en in AUTHOR_CANONICAL and AUTHOR_CANONICAL[en] != ru
    ]
    assert not mismatches, f"spelling mismatch vs AUTHOR_CANONICAL: {mismatches}"


def test_lloyd_jones_and_beeke_consistent_through_source_card_path():
    """Live gap found during this audit: canonical_author_name() pipes
    AUTHOR_CANONICAL's value through normalize_person_names(), but with no
    matching _PERSON_REPLACEMENTS source for the already-hyphenated
    Cyrillic double-л form, the stale spelling survived untouched."""
    assert canonical_author_name("Martyn Lloyd-Jones") == "Мартин Лойд-Джонс"
    assert canonical_author_name("Joel Beeke") == "Джоэль Бики"


def test_gemini_prompt_does_not_teach_stale_spelling():
    """core/prompts.py directly instructs Gemini with example translations —
    if it still teaches the stale double-л/no-soft-sign forms, the model
    will keep generating text no downstream normalization was designed to
    catch as a NEW occurrence (only defensive legacy patterns exist)."""
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "Мартин Ллойд-Джонс" not in src
    assert "Джоэл Бики." not in src
    assert "Мартин Лойд-Джонс" in src
