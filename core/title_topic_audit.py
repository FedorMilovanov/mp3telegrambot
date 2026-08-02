#!/usr/bin/env python3
"""Title/main-topic consistency diagnostics."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_STOP = {
    "джон", "макартур", "пол", "вошер", "альберт", "молер", "проповедь", "лекция",
    "часть", "разбор", "вопросы", "ответы", "бог", "христос", "иисус", "святой",
}

# Lexical overlap is meaningful only when a title carries enough independent
# content words. One- and two-term names are often series/brand/metaphorical
# titles (for example «Люди Слова» or «Узкие врата»); zero overlap there is not
# evidence that Gemini analysed the wrong audio.
_MIN_AUDITABLE_TITLE_TERMS = 3


@dataclass(frozen=True)
class TitleTopicIssue:
    code: str
    message: str
    overlap: float
    title_terms: tuple[str, ...]


def _terms(text: str) -> set[str]:
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", str(text or "").lower().replace("ё", "е"))
    return {w for w in words if w not in _STOP}


def _overlap_score(title: str, body: str) -> float:
    title_terms = _terms(title)
    if not title_terms:
        return 0.0
    body_terms = _terms(body)
    if not body_terms:
        return 0.0
    return len(title_terms & body_terms) / max(len(title_terms), 1)


def _body_for_ai(ai_data: dict | None) -> str:
    ai = ai_data or {}
    body = str(ai.get("main_topic") or "")
    ts = ai.get("timestamps")
    if isinstance(ts, list):
        body += " " + " ".join(str(x.get("topic", "")) for x in ts if isinstance(x, dict))
    else:
        body += " " + str(ts or "")
    return body


def audit_title_topic_consistency(
    real_title: str,
    main_topic: str = "",
    timestamps: Any = None,
) -> TitleTopicIssue | None:
    """Return an issue only when lexical evidence is strong enough to audit.

    Short/metaphorical/series titles are deliberately treated as inconclusive,
    not as errors. Descriptive titles with at least three substantive terms are
    still checked and can trigger the safe fallback-title comparison.
    """
    title_terms = _terms(real_title)
    if len(title_terms) < _MIN_AUDITABLE_TITLE_TERMS:
        return None

    body = str(main_topic or "")
    if isinstance(timestamps, list):
        body += " " + " ".join(str(x.get("topic", "")) for x in timestamps if isinstance(x, dict))
    else:
        body += " " + str(timestamps or "")
    body_terms = _terms(body)
    if not body_terms:
        return None

    overlap = len(title_terms & body_terms) / max(len(title_terms), 1)
    # One shared generic/weak word should not silence the audit; use ratio, not
    # a boolean intersection guard.
    if overlap < 0.34:
        return TitleTopicIssue(
            code="title_topic_low_overlap",
            message="real_title has low lexical overlap with main_topic/timestamps",
            overlap=overlap,
            title_terms=tuple(sorted(title_terms)),
        )
    return None


def choose_safe_public_title(ai_data: dict | None, fallback_title: str = "") -> str:
    """Return a safer display title when a descriptive AI title is inconsistent.

    The parser records ``title_topic_warning`` only for auditable titles. The
    platform title wins solely when it is measurably more aligned with the same
    topic/timestamps; short editorial or series titles therefore remain intact.
    """
    ai_data = ai_data or {}
    fallback = str(fallback_title or "").strip()
    current = str(ai_data.get("real_title") or "").strip()
    if ai_data.get("title_topic_warning") and len(fallback) >= 8:
        body = _body_for_ai(ai_data)
        fallback_score = _overlap_score(fallback, body)
        current_score = _overlap_score(current, body)
        if fallback_score >= 0.34 and fallback_score > current_score:
            return fallback
    return current or fallback
