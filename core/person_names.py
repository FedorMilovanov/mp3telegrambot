#!/usr/bin/env python3
"""Canonical person-name normalization for captions, titles and generated prose."""
from __future__ import annotations

import re

_PERSON_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Ар Си Спраул", "Р. Ч. Спроул"),
    ("Ар-Си Спраул", "Р. Ч. Спроул"),
    ("Р. Ч. Спрол", "Р. Ч. Спроул"),
    ("Р. Ч. Спроул", "Р. Ч. Спроул"),
    ("Р. К. Спрол", "Р. Ч. Спроул"),
    ("R.C. Sproul", "Р. Ч. Спроул"),
    ("R. C. Sproul", "Р. Ч. Спроул"),
    ("RC Sproul", "Р. Ч. Спроул"),
    ("Эс Льюиса Джонсона", "С. Льюиса Джонсона"),
    ("Эс Льюис Джонсон", "С. Льюис Джонсон"),
    ("S. Lewis Johnson", "С. Льюис Джонсон"),
    ("Мартина Лойда Джонса", "Мартина Ллойд-Джонса"),
    ("Мартина Ллойд Джонса", "Мартина Ллойд-Джонса"),
    ("Мартин Лойд Джонс", "Мартин Ллойд-Джонс"),
    ("Мартин Ллойд Джонс", "Мартин Ллойд-Джонс"),
    ("Лойда Джонса", "Ллойд-Джонса"),
    ("Ллойд Джонса", "Ллойд-Джонса"),
    ("Lloyd-Jones", "Ллойд-Джонс"),
    ("Martyn Lloyd-Jones", "Мартин Ллойд-Джонс"),
    ("Си Джей Махони", "Си Джей Мэхэни"),
    ("Си Джей Махани", "Си Джей Мэхэни"),
    ("C.J. Mahaney", "Си Джей Мэхэни"),
    ("C. J. Mahaney", "Си Джей Мэхэни"),
)


def normalize_person_names(text: str) -> str:
    if not text:
        return text
    if not str(text).strip():
        return text
    out = str(text)
    changed = False
    for src, dst in _PERSON_REPLACEMENTS:
        if src in out:
            out = out.replace(src, dst)
            changed = True
    # Initials spacing polish: "Р.Ч. Спроул" -> "Р. Ч. Спроул"
    out2 = re.sub(r"\b([А-ЯA-Z])\.\s*([А-ЯA-Z])\.\s*", lambda m: f"{m.group(1)}. {m.group(2)}. ", out)
    if out2 != out:
        changed = True
        out = out2
    out2 = re.sub(r" {2,}", " ", out)
    if out2 != out:
        changed = True
        out = out2
    return out.strip() if changed else out


def canonical_person_name(value: str) -> str:
    """Return one canonical display form for a person/source author name."""
    return normalize_person_names(str(value or "").strip())
