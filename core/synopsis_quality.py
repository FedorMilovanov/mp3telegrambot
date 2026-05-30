#!/usr/bin/env python3
"""Duration-aware Synopsis density and coverage helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import re

from core.core_utils import time_to_seconds


@dataclass(frozen=True)
class SynopsisDensityProfile:
    name: str
    sections: str
    section_len: str
    total_chars: str
    max_tokens: int
    min_sections: int
    min_total_chars: int


@dataclass(frozen=True)
class SynopsisQualityIssue:
    code: str
    message: str


def get_synopsis_density_profile(duration_seconds: int | float = 0) -> SynopsisDensityProfile:
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur and dur <= 20 * 60:
        return SynopsisDensityProfile("short", "3-5", "250-800", "1800", 24000, 3, 1200)
    if dur and dur <= 50 * 60:
        return SynopsisDensityProfile("medium", "6-10", "600-1400", "4500", 40000, 6, 3500)
    if dur and dur <= 90 * 60:
        return SynopsisDensityProfile("long", "10-16", "900-2200", "9000", 60000, 10, 8500)
    return SynopsisDensityProfile("very_long", "12-20", "1000-2600", "16000", 65000, 12, 12000)


def _section_time_seconds(section: dict[str, Any]) -> int | None:
    t = str((section or {}).get("time") or "").strip()
    return time_to_seconds(t) if t else None



def _section_text_chars(section: dict[str, Any]) -> int:
    blocks = section.get("blocks") if isinstance(section, dict) else None
    if isinstance(blocks, list) and blocks:
        return sum(
            len(str(b.get("text") or b.get("quote") or b.get("why_relevant") or b.get("role_in_argument") or ""))
            for b in blocks if isinstance(b, dict)
        )
    return len(str((section or {}).get("content") or "").strip())


ORGANIZATIONAL_ANNOUNCEMENT_RE = re.compile(
    r"\b(регистрац|зарегистр|конференци[яиюе]|пройд[её]т|состоится|"
    r"reformation\s+heritage|grand\s+rapids|registration|announc|conference\s+will)\b",
    re.IGNORECASE,
)
CONTENT_SERMON_RE = re.compile(
    r"\b(писани|евангели|христ|бог|грех|покаяни|вер[ауы]|дух|церк|проповед|"
    r"scripture|gospel|christ|repent|faith|church|sermon)\b",
    re.IGNORECASE,
)


def looks_like_organizational_announcement(section: dict[str, Any]) -> bool:
    """True for leading logistics/registration announcements, not sermon content."""
    text = " ".join(str((section or {}).get(k) or "") for k in ("title", "content"))
    blocks = (section or {}).get("blocks") or []
    if isinstance(blocks, list):
        text += " " + " ".join(
            str(b.get("text") or b.get("quote") or b.get("why_relevant") or "")
            for b in blocks if isinstance(b, dict)
        )
    if not text.strip():
        return False
    org_hits = len(ORGANIZATIONAL_ANNOUNCEMENT_RE.findall(text))
    content_hits = len(CONTENT_SERMON_RE.findall(text))
    return org_hits >= 2 and content_hits <= 2


def filter_organizational_announcement_sections(sections: list[dict]) -> tuple[list[dict], list[SynopsisQualityIssue]]:
    """Drop only leading logistics announcements from Synopsis sections."""
    valid = [s for s in sections or [] if isinstance(s, dict)]
    if len(valid) < 2:
        return valid, []
    first = valid[0]
    if looks_like_organizational_announcement(first):
        title = str(first.get("title") or "").strip()[:80]
        return valid[1:], [SynopsisQualityIssue(
            "synopsis_organizational_announcement_removed",
            f"removed leading organizational announcement section: {title or 'untitled'}",
        )]
    return valid, []

_BOLD_PHRASE_RE = re.compile(r"\*\*[^*\n]{6,160}\*\*")
_YEAR_OR_NAME_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|[А-ЯЁ][а-яё]{3,}\s+[А-ЯЁ][а-яё]{3,})\b")


def _section_paragraph_count(section: dict[str, Any]) -> int:
    blocks = section.get("blocks") if isinstance(section, dict) else None
    if isinstance(blocks, list) and blocks:
        return sum(1 for b in blocks if isinstance(b, dict) and str(b.get("type") or "paragraph") in {"paragraph", "thesis", "pull_quote", "scripture", "bullet"})
    content = str((section or {}).get("content") or "")
    return len([p for p in re.split(r"\n\s*\n", content) if p.strip()])


def _section_author_voice_markers(section: dict[str, Any]) -> int:
    text = str((section or {}).get("content") or "")
    blocks = section.get("blocks") if isinstance(section, dict) else []
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict):
                text += " " + " ".join(str(b.get(k) or "") for k in ("text", "quote", "role_in_argument", "why_relevant"))
                for step in b.get("steps") or []:
                    text += " " + str(step)
    return len(_BOLD_PHRASE_RE.findall(text)) + len(_YEAR_OR_NAME_RE.findall(text))

_TS_INLINE_RE = re.compile(r"(?:⏱|📌)?\s*\b\d{1,2}:\d{2}(?::\d{2})?\b")


def _section_inline_timestamp_count(section: dict[str, Any]) -> int:
    text = str((section or {}).get("content") or "")
    blocks = (section or {}).get("blocks") or []
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict):
                text += " " + " ".join(str(b.get(k) or "") for k in ("text", "quote", "timestamp", "why_relevant", "role_in_argument", "anchor_timestamp"))
                for step in b.get("steps") or []:
                    text += " " + str(step)
    return len(_TS_INLINE_RE.findall(text))

def audit_synopsis_density(sections: list[dict], duration_seconds: int | float = 0) -> list[SynopsisQualityIssue]:
    """Return warnings when Synopsis looks too thin for material duration."""
    profile = get_synopsis_density_profile(duration_seconds)
    issues: list[SynopsisQualityIssue] = []
    valid = [s for s in sections or [] if isinstance(s, dict)]
    total_chars = sum(_section_text_chars(s) for s in valid)
    if len(valid) < profile.min_sections:
        issues.append(SynopsisQualityIssue(
            "synopsis_too_few_sections",
            f"sections={len(valid)} below profile minimum {profile.min_sections} for {profile.name}",
        ))
    if total_chars < profile.min_total_chars:
        issues.append(SynopsisQualityIssue(
            "synopsis_too_few_chars",
            f"content_chars={total_chars} below profile minimum {profile.min_total_chars} for {profile.name}",
        ))
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur >= 30 * 60:
        times = [_section_time_seconds(s) for s in valid]
        times = [t for t in times if t is not None]
        if times and max(times) / dur < 0.70:
            issues.append(SynopsisQualityIssue(
                "synopsis_time_coverage_low",
                f"last_section_time={max(times)} covers only {max(times)/dur:.0%} of duration",
            ))
        inline_count = sum(_section_inline_timestamp_count(s) for s in valid)
        min_inline = max(4, min(len(valid), dur // 900))
        if inline_count < min_inline:
            issues.append(SynopsisQualityIssue(
                "synopsis_missing_inline_anchors",
                f"inline_timestamp_anchors={inline_count} below minimum {min_inline} for transcript-like Synopsis",
            ))
        paragraph_counts = [_section_paragraph_count(s) for s in valid]
        thin_paragraph_sections = sum(1 for count in paragraph_counts if count < 3)
        if valid and thin_paragraph_sections > max(1, len(valid) // 3):
            issues.append(SynopsisQualityIssue(
                "synopsis_too_few_paragraphs",
                f"sections_with_lt3_paragraphs={thin_paragraph_sections}/{len(valid)} for transcript-like Synopsis",
            ))
        voice_markers = sum(_section_author_voice_markers(s) for s in valid)
        min_voice = max(4, len(valid) // 2)
        if voice_markers < min_voice:
            issues.append(SynopsisQualityIssue(
                "synopsis_author_voice_low",
                f"author_voice_markers={voice_markers} below minimum {min_voice}; preserve quotes, names, dates, examples",
            ))
    return issues


def format_synopsis_quality_issues(issues: list[SynopsisQualityIssue]) -> str:
    return " | ".join(f"{i.code}: {i.message}" for i in issues or [])


def synopsis_density_score(sections: list[dict], duration_seconds: int | float = 0) -> int:
    """Simple monotonic score for comparing original vs retry Synopsis."""
    valid = [s for s in sections or [] if isinstance(s, dict)]
    total_chars = sum(_section_text_chars(s) for s in valid)
    times = [_section_time_seconds(s) for s in valid]
    times = [t for t in times if t is not None]
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    # Coverage is a tie-breaker, not a substitute for real content.
    # Old bonus=2000 could make a thin retry beat a substantially deeper original.
    coverage_bonus = min(250, int((max(times) / dur) * 250)) if times and dur else 0
    return total_chars + len(valid) * 350 + coverage_bonus


def should_retry_synopsis_density(issues: list[SynopsisQualityIssue], duration_seconds: int | float = 0) -> bool:
    """Retry only for substantial long-material density problems."""
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur < 45 * 60:
        return False
    critical = {
        "synopsis_too_few_sections",
        "synopsis_too_few_chars",
        "synopsis_missing_inline_anchors",
        "synopsis_too_few_paragraphs",
        "synopsis_author_voice_low",
    }
    return any(i.code in critical for i in issues or [])


_INLINE_TS_EXTRACT_RE = re.compile(r'[⏱📌]\s*\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}')


def audit_inline_timestamp_order(sections: list[dict]) -> list[SynopsisQualityIssue]:
    """Check that inline timestamps in content/blocks don't precede section.time.

    QA_TIMESTAMP_RULE says: 'Таймкод внутри content НИКОГДА не должен указывать
    на время ДО начала этой секции'. This function enforces it deterministically.
    """
    issues: list[SynopsisQualityIssue] = []
    for idx, sec in enumerate(sections or []):
        if not isinstance(sec, dict):
            continue
        section_time_str = str((sec.get("time") or "")).strip()
        section_time = time_to_seconds(section_time_str) if section_time_str else None
        if section_time is None:
            continue

        # Collect text from content and blocks
        text = str(sec.get("content") or "")
        blocks = sec.get("blocks") or []
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict):
                    for k in ("text", "quote", "why_relevant", "role_in_argument"):
                        text += " " + str(b.get(k) or "")

        # Find all inline timestamps
        for m in _INLINE_TS_EXTRACT_RE.finditer(text):
            ts = time_to_seconds(m.group(1))
            if ts is not None and ts < section_time - 5:  # 5s tolerance
                issues.append(SynopsisQualityIssue(
                    code="inline_timestamp_before_section",
                    message=(
                        f"sections[{idx}] time={section_time_str}: "
                        f"inline {m.group(1)} is {section_time - ts:.0f}s before section start"
                    ),
                ))
    return issues
