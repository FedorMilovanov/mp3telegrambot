#!/usr/bin/env python3
"""Deterministic pre-publication audit for Telegraph pages.

This layer does not call Gemini. It catches the class of live-run defects that
prompts can only *request* the model to avoid: mixed Greek/Cyrillic tokens,
third-person analytic wrappers, source-map title hallucinations, bare headings
and recurring typo remnants.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from core.text_utils import find_mixed_greek_cyrillic_tokens, normalize_source_map_text


@dataclass(frozen=True)
class PageAuditIssue:
    code: str
    message: str
    snippet: str = ""


_TYPO_RE = re.compile(
    r"доктлин|боголог|Божьего\s+Слово|Слово\s+Божьего|"
    r"душевпопечение|Стину\s+Лоусону|Стин\s+Лоусон|МакАртора|"
    r"Странный\s+огонь|странный\s+огонь|епифанит",
    re.IGNORECASE,
)

_THIRD_PERSON_RE = re.compile(
    r"\b(?:(?:Джон\s+)?МакАртур|(?:Алексей\s+)?Коломийцев|(?:Лигон\s+)?Данкан|МакАртур|Данкан|Молер|Девер|Лоусон|Бики|Пеннингтон|Бокам|Риккарди|автор|проповедник|спикер|лектор)\s+"
    r"(?:подч[её]ркивает|показывает|объясняет|отмечает|говорит|указывает|считает|вскрывает)\b",
    re.IGNORECASE,
)

_POMPOUS_RE = re.compile(
    r"богословские\s+гиганты|ведущие\s+богословы\s+современности|"
    r"выдающиеся\s+пасторы|крупнейшие\s+мыслители",
    re.IGNORECASE,
)

_SOURCE_RU_ORIGINAL_RE = re.compile(
    r"^\s*[•\-]\s*[А-ЯЁ][^\n]{2,120}\(\s*[A-Z][A-Za-z .'-]{2,80},\s*[A-Za-z][^)]{2,160}\)",
)

_BARE_BULLET_RE = re.compile(
    r"(?:^|\n)\s*[•\-]\s*(?:\*\*)?[^\n]{3,95}(?:\*\*)?\s*"
    r"(?=\n\s*[•\-]\s*(?:\*\*)?[^\n]{3,95}(?:\*\*)?\s*(?:\n|$))",
)


def flatten_telegraph_nodes(nodes: Any) -> str:
    """Flatten Telegraph node tree to readable text for audit."""
    if isinstance(nodes, str):
        return nodes
    if isinstance(nodes, dict):
        children = nodes.get("children", [])
        return "".join(flatten_telegraph_nodes(c) for c in children)
    if isinstance(nodes, list):
        parts: list[str] = []
        for n in nodes:
            txt = flatten_telegraph_nodes(n)
            if txt:
                parts.append(txt)
        return "\n".join(parts)
    return ""


def _snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)].replace("\n", " / ")


def audit_telegraph_page(title: str, nodes: list, page_type: str = "") -> list[PageAuditIssue]:
    """Return deterministic warnings for a page about to be published."""
    text = (title or "") + "\n" + flatten_telegraph_nodes(nodes)
    issues: list[PageAuditIssue] = []

    for m in _TYPO_RE.finditer(text):
        issues.append(PageAuditIssue("typo", f"possible unnormalized typo: {m.group(0)}", _snippet(text, m.start(), m.end())))

    mixed = find_mixed_greek_cyrillic_tokens(text)
    for token in mixed:
        pos = text.find(token)
        issues.append(PageAuditIssue("mixed_greek_cyrillic", f"mixed Greek/Cyrillic token: {token}", _snippet(text, pos, pos + len(token))))

    for m in _THIRD_PERSON_RE.finditer(text):
        issues.append(PageAuditIssue("third_person", f"third-person wrapper: {m.group(0)}", _snippet(text, m.start(), m.end())))

    for m in _POMPOUS_RE.finditer(text):
        issues.append(PageAuditIssue("pompous_label", f"pompous label: {m.group(0)}", _snippet(text, m.start(), m.end())))

    if page_type.lower() in {"study", "studyanalysis", "reflection", "synopsis", ""}:
        for m in _BARE_BULLET_RE.finditer(text):
            issues.append(PageAuditIssue("possible_bare_bullet", "possible bare bullet heading", _snippet(text, m.start(), m.end())))

    # Source-title hallucination audit must be source-card scoped. A global
    # regex produced false positives on translation-fork analysis like
    # «укрепляет душу» vs “restoring the soul”. Only inspect bullet-like source
    # cards after/inside «Карта источников» context.
    in_source_map = False
    for line in text.splitlines():
        low = line.lower()
        if "карта источников" in low:
            in_source_map = True
            continue
        if in_source_map and line.strip() and not line.lstrip().startswith(("•", "-")):
            # Keep context through short description paragraphs, but do not run
            # source-card regex on prose.
            continue
        if in_source_map:
            # Doctrinal concept bullets can legitimately contain parenthetical
            # Latin/English terms followed by an explanation dash, e.g.
            # "Sola Gratia (...) — principle...". They are not bibliography
            # source cards and should not be flagged as invented source titles.
            if re.search(r"\)\s*[—-]\s+", line):
                continue
            m = _SOURCE_RU_ORIGINAL_RE.search(line)
            if m:
                # If the source normalizer keeps the Russian title as the
                # canonical bold title, it is known/official rather than a
                # hallucinated title. Example: Calvin's Institutes.
                raw_title = re.sub(r"^\s*[•\-]\s*", "", line).split(",", 1)[0].strip().strip("*")
                normalized = normalize_source_map_text(line)
                if raw_title and f"**{raw_title}**" in normalized:
                    continue
                issues.append(PageAuditIssue(
                    "source_map_original_title",
                    "source card may contain invented Russian title before original title",
                    line[:240],
                ))

    return issues


def format_audit_issues(issues: list[PageAuditIssue], limit: int = 5) -> str:
    if not issues:
        return ""
    head = issues[:limit]
    rendered = [f"{i.code}: {i.message} — {i.snippet[:160]}" for i in head]
    if len(issues) > limit:
        rendered.append(f"... и ещё {len(issues) - limit}")
    return " | ".join(rendered)


_PAGE_WARNING_CODES = {
    "typo",
    "mixed_greek_cyrillic",
    "third_person",
    "pompous_label",
    "possible_bare_bullet",
    "source_map_original_title",
}
_PAGE_CRITICAL_CODES = {
    "mixed_greek_cyrillic",
}


def get_page_audit_mode() -> str:
    """Return PAGE_AUDIT_MODE/CONTENT_AUDIT_MODE: off | warn | strict.

    PAGE_AUDIT_MODE overrides CONTENT_AUDIT_MODE. This lets operators run
    strict DOM/page checks only for surgical review sessions without changing
    section-level content audit defaults.
    """
    raw = os.getenv("PAGE_AUDIT_MODE", os.getenv("CONTENT_AUDIT_MODE", "warn"))
    mode = (raw or "warn").strip().lower()
    return mode if mode in {"off", "warn", "strict"} else "warn"


def _strict_page_codes() -> set[str]:
    raw = (os.getenv("PAGE_AUDIT_STRICT_CODES", "") or "").strip()
    if not raw:
        return set(_PAGE_CRITICAL_CODES)
    if raw.lower() in {"all", "warnings", "warning"}:
        return set(_PAGE_WARNING_CODES) | set(_PAGE_CRITICAL_CODES)
    codes = {x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()}
    return codes | set(_PAGE_CRITICAL_CODES)


def page_audit_block_publication_enabled() -> bool:
    """Explicit opt-in for blocking Telegraph create/edit on page-audit findings."""
    raw = (os.getenv("PAGE_AUDIT_BLOCK_PUBLICATION", "0") or "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    # A single global override for rare CI/manual release gates.
    raw_global = (os.getenv("AUDIT_BLOCK_PUBLICATION", "0") or "0").strip().lower()
    return raw_global in {"1", "true", "yes", "on"}


def should_abort_for_page_audit(issues: list[PageAuditIssue]) -> bool:
    """True when strict mode and explicit block-publication flag are both enabled."""
    if get_page_audit_mode() != "strict" or not page_audit_block_publication_enabled():
        return False
    strict_codes = _strict_page_codes()
    return any(i.code in strict_codes for i in issues or [])
