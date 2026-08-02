#!/usr/bin/env python3
"""Title/main-topic consistency diagnostics and source-title context recovery."""
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

_SESSION_RE = re.compile(
    r"(?i)(?<!\w)(сессия|часть|выпуск|эпизод|урок|лекция)\s*"
    r"(?:№\s*)?([0-9]+|[IVXLCDM]+)(?!\w)"
)
_BRACKETED_SERIES_RE = re.compile(r"^\s*[\[【]\s*([^\]】]{2,100}?)\s*[\]】]")
_QUOTED_TITLE_RE = re.compile(r"[«\"]\s*([^«»\"]{2,140}?)\s*[»\"]")


@dataclass(frozen=True)
class TitleTopicIssue:
    code: str
    message: str
    overlap: float
    title_terms: tuple[str, ...]


@dataclass(frozen=True)
class SourceTitleContext:
    """Structured editorial context recoverable from platform metadata."""

    series: str = ""
    session: str = ""
    quoted_title: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "real_series": self.series,
            "real_session": self.session,
            "source_quoted_title": self.quoted_title,
        }


def _clean_context_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-—–|,;:()[]{}\"")


def extract_source_title_context(source_title: str) -> SourceTitleContext:
    """Recover series/session/editorial title without guessing from audio.

    The extraction is intentionally narrow and deterministic. It preserves
    metadata such as ``[Не от Мира]`` and ``Сессия 1`` that should not be forced
    into ``real_title`` or ``real_event`` but must not disappear from the
    processing record either.
    """
    source = re.sub(r"\s+", " ", str(source_title or "")).strip()
    if not source:
        return SourceTitleContext()

    series = ""
    m_series = _BRACKETED_SERIES_RE.search(source)
    if m_series:
        candidate = _clean_context_value(m_series.group(1))
        if candidate and not re.search(r"(?i)^(official|audio|video|hd|4k)$", candidate):
            series = candidate

    session = ""
    m_session = _SESSION_RE.search(source)
    if m_session:
        label = m_session.group(1).lower()
        canonical = {
            "сессия": "Сессия",
            "часть": "Часть",
            "выпуск": "Выпуск",
            "эпизод": "Эпизод",
            "урок": "Урок",
            "лекция": "Лекция",
        }[label]
        session = f"{canonical} {m_session.group(2)}"

    quoted_title = ""
    quoted = [_clean_context_value(x) for x in _QUOTED_TITLE_RE.findall(source)]
    quoted = [x for x in quoted if x and not re.match(r"(?i)^#?(проповедь|лекция)\b", x)]
    if quoted:
        quoted_title = quoted[-1]

    return SourceTitleContext(series=series, session=session, quoted_title=quoted_title)


def enrich_source_title_context(ai_data: dict | None, source_title: str) -> dict:
    """Populate missing structured context fields without overwriting AI data."""
    ai = ai_data if isinstance(ai_data, dict) else {}
    context = extract_source_title_context(source_title)
    for key, value in context.as_dict().items():
        if value and not str(ai.get(key) or "").strip():
            ai[key] = value
    return ai


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
    """Return a safer display title and preserve source editorial hierarchy.

    The parser records ``title_topic_warning`` only for auditable titles. The
    platform title wins solely when it is measurably more aligned with the same
    topic/timestamps; short editorial or series titles therefore remain intact.
    Source series/session metadata is recovered into dedicated fields before
    the display decision, so it is retained without contaminating ``real_title``.
    """
    ai_data = ai_data if isinstance(ai_data, dict) else {}
    fallback = str(fallback_title or "").strip()
    enrich_source_title_context(ai_data, fallback)

    current = str(ai_data.get("real_title") or "").strip()
    if ai_data.get("title_topic_warning") and len(fallback) >= 8:
        body = _body_for_ai(ai_data)
        fallback_score = _overlap_score(fallback, body)
        current_score = _overlap_score(current, body)
        if fallback_score >= 0.34 and fallback_score > current_score:
            return fallback
    return current or fallback
