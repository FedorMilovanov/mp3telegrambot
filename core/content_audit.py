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


_FIRST_PERSON_AUTHOR_RE = re.compile(
    r"\b(?P<prefix>Я|Для меня),\s+(?P<name>[А-ЯЁ][А-ЯЁа-яё]+(?:\s+[А-ЯЁ][А-ЯЁа-яё]+){0,2}),\s*"
)


_DOUBLE_SLASH_RE = re.compile(r"(?<!:)\s+/\s*/\s+")
_GLUE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"([а-яё])([A-Z])"), r"\1 \2"),
    (re.compile(r"([а-яё])«"), r"\1 «"),
    (re.compile(r"([.!?…])([А-ЯЁA-Z])"), r"\1 \2"),
    (re.compile(r"(^|\n)-(?=[А-ЯЁA-Z])"), r"\1- "),
)


def normalize_generated_markdown_separators(text: str) -> str:
    """Preserve layout semantics before Telegraph node rendering.

    Gemini sometimes emits pseudo-separators like ``/ /`` between logical
    paragraphs.  Treat them as paragraph breaks instead of deleting them.  The
    glue fixes are deliberately narrow and do not touch URLs (``://`` is guarded)
    or markdown links.
    """
    out = str(text or "")
    out = _DOUBLE_SLASH_RE.sub("\n\n", out)
    for pattern, repl in _GLUE_FIXES:
        out = pattern.sub(repl, out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def _scrub_mismatched_first_person_author(text: str, expected_author: str = "") -> tuple[str, list[ContentAuditIssue]]:
    """Remove hallucinated first-person name appositions when they mismatch expected author."""
    if not text or not expected_author:
        return text, []
    expected_norm = re.sub(r"\s+", " ", expected_author).strip().lower()
    issues: list[ContentAuditIssue] = []

    def repl(m: re.Match) -> str:
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        if name.lower() in expected_norm or expected_norm in name.lower():
            return m.group(0)
        issues.append(ContentAuditIssue(
            code="first_person_author_fixed",
            location="",
            message=f"removed mismatched first-person author name: {name}",
            before=m.group(0),
            after=m.group("prefix") + " ",
        ))
        return m.group("prefix") + " "

    return _FIRST_PERSON_AUTHOR_RE.sub(repl, text), issues


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " / ").strip()
    return text[:limit]


def _audit_text(value: str, *, location: str, source_map: bool = False, expected_author: str = "") -> tuple[str, list[ContentAuditIssue]]:
    """Normalize one title/content string and return issues."""
    original = str(value or "")
    issues: list[ContentAuditIssue] = []

    text = normalize_common_typos(original)
    if text != original:
        _looks_source_fix = bool(re.search(r"[A-Za-z].*,|\([^)]*[A-Za-z]{3,}[^)]*\)", original))
        issues.append(ContentAuditIssue(
            code="source_card_fixed" if _looks_source_fix else "typo_fixed",
            location=location,
            message="source-card normalization applied" if _looks_source_fix else "common typo normalization applied",
            before=_short(original),
            after=_short(text),
        ))
        issues.append(ContentAuditIssue(
            code="normalized_text",
            location=location,
            message="legacy compatibility: typo/source-map normalization applied",
            before=_short(original),
            after=_short(text),
        ))

    sep_before = text
    text = normalize_generated_markdown_separators(text)
    if text != sep_before:
        issues.append(ContentAuditIssue(
            code="separator_fixed",
            location=location,
            message=(
                f"removed_double_slash={len(_DOUBLE_SLASH_RE.findall(sep_before))}; "
                f"paragraphs_before={sep_before.count(chr(10)+chr(10))}; paragraphs_after={text.count(chr(10)+chr(10))}"
            ),
            before=_short(sep_before),
            after=_short(text),
        ))

    source_before = text
    if source_map:
        text = "\n".join(normalize_source_map_text(line) for line in text.splitlines())
    else:
        # Safe no-op for non-source lines; still catches inline source-card strings.
        text = "\n".join(normalize_source_map_text(line) for line in text.splitlines())
    if text != source_before:
        issues.append(ContentAuditIssue(
            code="source_card_fixed",
            location=location,
            message="source-card normalization applied",
            before=_short(source_before),
            after=_short(text),
        ))
        if not any(i.code == "normalized_text" and i.location == location for i in issues):
            issues.append(ContentAuditIssue(
                code="normalized_text",
                location=location,
                message="legacy compatibility: typo/source-map normalization applied",
                before=_short(source_before),
                after=_short(text),
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

    text, author_issues = _scrub_mismatched_first_person_author(text, expected_author)
    for issue in author_issues:
        issues.append(ContentAuditIssue(
            code=issue.code,
            location=location,
            message=issue.message,
            before=issue.before,
            after=issue.after,
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


def _audit_translation_semantics(content: str, *, location: str) -> list[ContentAuditIssue]:
    """Small semantic sanity checks for common translation-fork drift."""
    low = (content or "").lower()
    issues: list[ContentAuditIssue] = []
    if "evil" in low and "лож" in low and "зл" not in low:
        issues.append(ContentAuditIssue(
            code="translation_semantic_warning",
            location=location,
            message="English 'evil' appears to be rendered as 'ложь' without 'зло'",
            before=_short(content),
        ))
    return issues


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
    expected_author: str = "",
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
        new_title, got = _audit_text(title, location=f"{base_loc}.title", expected_author=expected_author)
        issues.extend(got)
        sec["title"] = new_title

        content = str(sec.get("content") or "")
        source_map = bool(_SOURCE_MAP_HEADING_RE.search(new_title))
        new_content, got = _audit_text(content, location=f"{base_loc}.content", source_map=source_map, expected_author=expected_author)
        issues.extend(got)
        issues.extend(_audit_translation_forks(new_content, location=f"{base_loc}:{new_title}"))
        issues.extend(_audit_translation_semantics(new_content, location=f"{base_loc}:{new_title}"))
        sec["content"] = new_content

        blocks = sec.get("blocks")
        if isinstance(blocks, list):
            new_blocks: list[dict] = []
            for bidx, raw_block in enumerate(blocks):
                if not isinstance(raw_block, dict):
                    continue
                block = dict(raw_block)
                for field in ("text", "quote", "why_relevant", "role_in_argument"):
                    if field in block and isinstance(block.get(field), str):
                        block_text, got_block = _audit_text(
                            block.get(field, ""),
                            location=f"{base_loc}.blocks[{bidx}].{field}",
                            expected_author=expected_author,
                        )
                        issues.extend(got_block)
                        block[field] = block_text
                for field in ("author", "title_original", "lemma", "ref", "timestamp", "type"):
                    if field in block and isinstance(block.get(field), str):
                        block[field] = str(block.get(field) or "").strip()
                new_blocks.append(block)
            sec["blocks"] = new_blocks

        new_sections.append(sec)

    new_outline: list[dict] = []
    for idx, raw in enumerate(outline or []):
        if not isinstance(raw, dict):
            continue
        oi = dict(raw)
        title = str(oi.get("title") or "")
        new_title, got = _audit_text(title, location=f"{label or 'expanded'}.outline[{idx}].title", expected_author=expected_author)
        issues.extend(got)
        oi["title"] = new_title
        new_outline.append(oi)

    return new_sections, new_outline, issues


_WARNING_CODES = {
    "mixed_greek_cyrillic_warning",
    "bare_translation_forks_warning",
    "translation_semantic_warning",
    "third_person_warning",
}


def has_content_audit_warnings(issues: list[ContentAuditIssue]) -> bool:
    """True when issues include unresolved warning-level problems, not just fixes."""
    return any(i.code in _WARNING_CODES for i in issues or [])


def format_content_audit_issues(issues: list[ContentAuditIssue], limit: int = 6) -> str:
    if not issues:
        return ""
    rendered: list[str] = []
    specific_locations = {
        i.location for i in issues or []
        if i.code in {"typo_fixed", "source_card_fixed", "separator_fixed", "whitespace_fixed"}
    }
    display_issues = [
        i for i in (issues or [])
        if not (i.code == "normalized_text" and i.location in specific_locations)
    ]
    for issue in display_issues[:limit]:
        detail = issue.message
        if issue.before:
            detail += f" | before={issue.before}"
        if issue.after:
            detail += f" | after={issue.after}"
        rendered.append(f"{issue.code}@{issue.location}: {detail}")
    if len(display_issues) > limit:
        rendered.append(f"... и ещё {len(display_issues) - limit}")
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
