#!/usr/bin/env python3
"""Narrow deterministic polish for public Russian editorial text.

This is not a free-form rewriter. It corrects only high-confidence calques
observed in production output and keeps legitimate technical/military uses
untouched. Generative prompts still own style; this module is the publication
boundary that prevents a known awkward phrase from reaching subscribers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RussianStyleFix:
    code: str
    before: str
    after: str


# English “equipping men” in sermon/conference metadata was rendered as
# «духовное укомплектование мужей». «Укомплектование» is natural for staffing
# units or supplying equipment, not for spiritual formation. The guard requires
# both the spiritual adjective and a male-audience noun, so legitimate phrases
# such as «укомплектование подразделения» are not touched.
_SPIRITUAL_EQUIPPING_RE = re.compile(
    r"(?i)\b(?P<form>"
    r"духовное\s+укомплектование|"
    r"духовного\s+укомплектования|"
    r"духовному\s+укомплектованию|"
    r"духовным\s+укомплектованием|"
    r"духовном\s+укомплектовании"
    r")\s+(?:мужей|мужчин)\b"
)

_SPIRITUAL_EQUIPPING_REPLACEMENTS = {
    "духовное укомплектование": "духовная подготовка мужчин",
    "духовного укомплектования": "духовной подготовки мужчин",
    "духовному укомплектованию": "духовной подготовке мужчин",
    "духовным укомплектованием": "духовной подготовкой мужчин",
    "духовном укомплектовании": "духовной подготовке мужчин",
}


def polish_public_russian(text: str) -> tuple[str, tuple[RussianStyleFix, ...]]:
    """Return polished text and an auditable list of deterministic fixes."""
    source = str(text or "")
    fixes: list[RussianStyleFix] = []

    def replace_spiritual_equipping(match: re.Match[str]) -> str:
        before = match.group(0)
        form = match.group("form").lower()
        after = _SPIRITUAL_EQUIPPING_REPLACEMENTS[form]
        if before[:1].isupper():
            after = after[:1].upper() + after[1:]
        fixes.append(
            RussianStyleFix(
                code="spiritual_equipping_calque",
                before=before,
                after=after,
            )
        )
        return after

    polished = _SPIRITUAL_EQUIPPING_RE.sub(replace_spiritual_equipping, source)
    return polished, tuple(fixes)


def polish_public_russian_text(text: str) -> str:
    """Convenience form for publication surfaces that only need the text."""
    return polish_public_russian(text)[0]


__all__ = [
    "RussianStyleFix",
    "polish_public_russian",
    "polish_public_russian_text",
]
