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

# The parser historically escalates overlap < 0.05 to ERROR. A very short,
# metaphorical or editorial title cannot support that confidence from lexical
# overlap alone, so its compatibility-facing overlap is floored at the warning
# boundary while ``raw_overlap`` preserves the exact measurement.
_SHORT_TITLE_WARNING_FLOOR = 0.05
_STRONG_MISMATCH_THRESHOLD = 0.34

_SESSION_RE = re.compile(
    r"(?i)(?<!\w)(сессия|часть|выпуск|эпизод|урок|лекция)\s*"
    r"(?:№\s*)?([0-9]+|[IVXLCDM]+)(?!\w)"
)
_BRACKETED_SERIES_RE = re.compile(r"^\s*[\[【]\s*([^\]】]{2,100}?)\s*[\]】]")
_QUOTED_TITLE_RE = re.compile(r"[«\"]\s*([^«»\"]{2,140}?)\s*[»\"]")
_RAW_TITLE_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z]{3,}")


@dataclass(frozen=True)
class TitleTopicIssue:
    code: str
    message: str
    overlap: float
    title_terms: tuple[str, ...]
    raw_overlap: float = 0.0
    confidence: str = "strong"


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


def _raw_title_word_count(text: str) -> int:
    return len(_RAW_TITLE_WORD_RE.findall(str(text or "")))


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
    """Return a lexical consistency issue with evidence-calibrated severity.

    Every non-empty title remains auditable, including legacy two-word tests.
    However, one-to-three-word editorial/metaphorical titles do not provide
    enough independent lexical evidence for an ERROR. They return an
    ``inconclusive_short_title`` warning with the exact value in ``raw_overlap``.
    Longer descriptive titles retain the strong low-overlap signal.
    """
    title_terms = _terms(real_title)
    if not title_terms:
        return None

    body = str(main_topic or "")
    if isinstance(timestamps, list):
        body += " " + " ".join(str(x.get("topic", "")) for x in timestamps if isinstance(x, dict))
    else:
        body += " " + str(timestamps or "")
    body_terms = _terms(body)
    if not body_terms:
        return None

    raw_overlap = len(title_terms & body_terms) / max(len(title_terms), 1)
    if raw_overlap >= _STRONG_MISMATCH_THRESHOLD:
        return None

    is_short_or_weak = _raw_title_word_count(real_title) <= 3 or len(title_terms) <= 1
    if is_short_or_weak:
        # ``overlap`` is intentionally floored at the existing parser's warning
        # boundary; ``raw_overlap`` remains the exact diagnostic value.
        escalation_overlap = max(raw_overlap, _SHORT_TITLE_WARNING_FLOOR)
        return TitleTopicIssue(
            code="title_topic_inconclusive_short_title",
            message=(
                "short/editorial title has low lexical overlap, but lexical evidence "
                f"is inconclusive (raw_overlap={raw_overlap:.2f})"
            ),
            overlap=escalation_overlap,
            raw_overlap=raw_overlap,
            confidence="inconclusive",
            title_terms=tuple(sorted(title_terms)),
        )

    return TitleTopicIssue(
        code="title_topic_low_overlap",
        message="real_title has low lexical overlap with main_topic/timestamps",
        overlap=raw_overlap,
        raw_overlap=raw_overlap,
        confidence="strong",
        title_terms=tuple(sorted(title_terms)),
    )


def choose_safe_public_title(ai_data: dict | None, fallback_title: str = "") -> str:
    """Return a safer display title and preserve source editorial hierarchy.

    The parser records ``title_topic_warning`` for both strong and inconclusive
    findings. The platform title wins solely when it is measurably more aligned
    with the same topic/timestamps; short editorial or series titles therefore
    remain intact. Source series/session metadata is recovered into dedicated
    fields before the display decision, so it is retained without contaminating
    ``real_title``.
    """
    ai_data = ai_data if isinstance(ai_data, dict) else {}
    fallback = str(fallback_title or "").strip()
    enrich_source_title_context(ai_data, fallback)

    current = str(ai_data.get("real_title") or "").strip()
    if ai_data.get("title_topic_warning") and len(fallback) >= 8:
        body = _body_for_ai(ai_data)
        fallback_score = _overlap_score(fallback, body)
        current_score = _overlap_score(current, body)
        if fallback_score >= _STRONG_MISMATCH_THRESHOLD and fallback_score > current_score:
            return fallback
    return current or fallback
