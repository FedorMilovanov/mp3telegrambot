#!/usr/bin/env python3
"""Section-level deterministic audit between Gemini JSON and Telegraph rendering.

This is the professional layer above node-level Telegraph postprocess:

    Gemini JSON -> parse -> SECTION AUDIT -> markdown/nodes -> node audit -> publish

It mutates only narrow, deterministic classes of defects observed in live runs
and reports warnings for classes that should be inspected but not guessed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from core.text_utils import (
    find_mixed_greek_cyrillic_tokens,
    normalize_common_typos,
    normalize_source_map_text,
    scrub_third_person_phrases,
)


@dataclass(frozen=True)
class ContentAuditIssue:
    code: str
    location: str
    message: str
    before: str = ""
    after: str = ""


_THIRD_PERSON_RE = re.compile(
    r"\b(?:(?:Джон\s+)?МакАртур|автор|проповедник|спикер|лектор)\s+"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает)\b",
    re.IGNORECASE,
)

_SOURCE_MAP_HEADING_RE = re.compile(r"карта\s+источников", re.IGNORECASE)
_TRANSLATION_FORKS_HEADING_RE = re.compile(r"переводческ\w*\s+развил", re.IGNORECASE)
_BULLET_LINE_RE = re.compile(r"^\s*[•\-]\s+(.+?)\s*$")


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " / ").strip()
    return text[:limit]


def _audit_text(value: str, *, location: str, source_map: bool = False) -> tuple[str, list[ContentAuditIssue]]:
    """Normalize one title/content string and return issues."""
    original = str(value or "")
    issues: list[ContentAuditIssue] = []

    text = normalize_common_typos(original)
    if source_map:
        text = "\n".join(normalize_source_map_text(line) for line in text.splitlines())
    else:
        # Safe no-op for non-source lines; still catches inline source-card strings.
        text = "\n".join(normalize_source_map_text(line) for line in text.splitlines())

    after_typo_source = text
    if after_typo_source != original:
        issues.append(ContentAuditIssue(
            code="normalized_text",
            location=location,
            message="common typo/source-map normalization applied",
            before=_short(original),
            after=_short(after_typo_source),
        ))

    third_before = text
    text = scrub_third_person_phrases(text)
    if text != third_before:
        issues.append(ContentAuditIssue(
            code="third_person_fixed",
            location=location,
            message="third-person analytic wrapper removed",
            before=_short(third_before),
            after=_short(text),
        ))
    elif _THIRD_PERSON_RE.search(text):
        issues.append(ContentAuditIssue(
            code="third_person_warning",
            location=location,
            message="third-person wrapper still present after conservative scrub",
            before=_short(text),
        ))

    mixed = find_mixed_greek_cyrillic_tokens(text)
    if mixed:
        issues.append(ContentAuditIssue(
            code="mixed_greek_cyrillic_warning",
            location=location,
            message="mixed Greek/Cyrillic token(s): " + ", ".join(mixed[:5]),
            before=_short(text),
        ))

    return text, issues


def _audit_translation_forks(content: str, *, location: str) -> list[ContentAuditIssue]:
    """Warn about bare translation-fork bullet lists without analysis."""
    if not content or not _TRANSLATION_FORKS_HEADING_RE.search(location):
        return []
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    bullet_idxs = [i for i, ln in enumerate(lines) if _BULLET_LINE_RE.match(ln)]
    if len(bullet_idxs) < 2:
        return []
    # If two or more bullet lines are short and adjacent/near-adjacent, likely bare headings.
    short_bullets = [i for i in bullet_idxs if len(lines[i]) < 130]
    if len(short_bullets) >= 2:
        # Require at least one substantial non-bullet analysis paragraph.
        has_analysis = any((not _BULLET_LINE_RE.match(ln)) and len(ln) >= 160 for ln in lines)
        if not has_analysis:
            return [ContentAuditIssue(
                code="bare_translation_forks_warning",
                location=location,
                message="translation-forks section may contain bare bullet headings without analysis",
                before=_short(" / ".join(lines[:5])),
            )]
    return []


def audit_expanded_sections(
    sections: list[dict],
    outline: list[dict] | None = None,
    *,
    label: str = "",
) -> tuple[list[dict], list[dict], list[ContentAuditIssue]]:
    """Audit and normalize parsed Gemini ``sections``/``outline``.

    The function is intentionally schema-light: it preserves unknown keys and
    only normalizes ``title`` and ``content`` string fields. It returns copied
    lists so callers do not mutate parser-owned structures by accident.
    """
    issues: list[ContentAuditIssue] = []
    new_sections: list[dict] = []

    for idx, raw in enumerate(sections or []):
        if not isinstance(raw, dict):
            continue
        sec = dict(raw)
        base_loc = f"{label or 'expanded'}.sections[{idx}]"

        title = str(sec.get("title") or "")
        new_title, got = _audit_text(title, location=f"{base_loc}.title")
        issues.extend(got)
        sec["title"] = new_title

        content = str(sec.get("content") or "")
        source_map = bool(_SOURCE_MAP_HEADING_RE.search(new_title))
        new_content, got = _audit_text(content, location=f"{base_loc}.content", source_map=source_map)
        issues.extend(got)
        issues.extend(_audit_translation_forks(new_content, location=f"{base_loc}:{new_title}"))
        sec["content"] = new_content
        new_sections.append(sec)

    new_outline: list[dict] = []
    for idx, raw in enumerate(outline or []):
        if not isinstance(raw, dict):
            continue
        oi = dict(raw)
        title = str(oi.get("title") or "")
        new_title, got = _audit_text(title, location=f"{label or 'expanded'}.outline[{idx}].title")
        issues.extend(got)
        oi["title"] = new_title
        new_outline.append(oi)

    return new_sections, new_outline, issues


def format_content_audit_issues(issues: list[ContentAuditIssue], limit: int = 6) -> str:
    if not issues:
        return ""
    rendered: list[str] = []
    for issue in issues[:limit]:
        detail = issue.message
        if issue.before:
            detail += f" | before={issue.before}"
        if issue.after:
            detail += f" | after={issue.after}"
        rendered.append(f"{issue.code}@{issue.location}: {detail}")
    if len(issues) > limit:
        rendered.append(f"... и ещё {len(issues) - limit}")
    return " || ".join(rendered)


_CRITICAL_CONTENT_CODES = {
    "mixed_greek_cyrillic_warning",
    "bare_translation_forks_warning",
}


def get_content_audit_mode() -> str:
    """Return CONTENT_AUDIT_MODE: off | warn | strict. Default: warn."""
    mode = (os.getenv("CONTENT_AUDIT_MODE", "warn") or "warn").strip().lower()
    return mode if mode in {"off", "warn", "strict"} else "warn"


def should_abort_for_content_audit(issues: list[ContentAuditIssue]) -> bool:
    """True only in strict mode and only for critical issue classes."""
    if get_content_audit_mode() != "strict":
        return False
    return any(i.code in _CRITICAL_CONTENT_CODES for i in issues or [])
