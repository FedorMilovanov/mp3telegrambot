#!/usr/bin/env python3
"""Deterministic DOM/text audit for published Telegraph pages.

This module is intentionally network-free. Tools can fetch a Telegraph page with
Playwright/requests, then pass the HTML here. The checks focus on production
regressions observed in generated pages: raw markdown artifacts, third-person
wrappers, source-card title drift, empty paragraphs, and broken-looking links.
"""
from __future__ import annotations

from dataclasses import dataclass
import html as html_mod
import re
from typing import Iterable

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional at import time
    BeautifulSoup = None


@dataclass(frozen=True)
class TelegraphDomIssue:
    code: str
    message: str
    snippet: str = ""
    severity: str = "warning"


def _short(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _text_from_html(html: str) -> tuple[str, list[str], list[str]]:
    if BeautifulSoup is None:
        text = re.sub(r"<script\b.*?</script>", " ", html or "", flags=re.I | re.S)
        text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
        hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", html or "", flags=re.I)
        paragraphs = [html_mod.unescape(x) for x in re.findall(r"<p\b[^>]*>(.*?)</p>", html or "", flags=re.I | re.S)]
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", text))
        return text, hrefs, [re.sub(r"<[^>]+>", " ", p).strip() for p in paragraphs]
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    hrefs = [str(a.get("href") or "") for a in soup.find_all("a") if a.has_attr("href")]
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = soup.get_text("\n", strip=True)
    return text, hrefs, paragraphs


_THIRD_PERSON_PATTERNS = (
    re.compile(r"\b(?:лектор|проповедник|спикер|автор)\s+(?:показывает|подчеркивает|объясняет|говорит|указывает|раскрывает|разбирает)\b", re.I),
    re.compile(r"\b(?:МакАртур|Молер|Девер|Данкан|Лоусон|Бики|Пеннингтон|Бокам|Коломийцев|Риккарди)\s+(?:показывает|подчеркивает|объясняет|говорит|указывает|раскрывает|разбирает)\b", re.I),
)
_MARKDOWN_ARTIFACT_RE = re.compile(r"(?:\*\*|__|\*\s+\*|\s/\s/\s|-\*\s+\*)")
_SOURCE_INVENTED_RU_TITLE_RE = re.compile(
    r"^[•\-]\s+[А-ЯЁ][^,()]{3,80},\s+[А-ЯЁ][^()]{2,80}\([^)]*[A-Za-z]{3,}[^)]*\)",
    re.M,
)
_BAD_LINK_RE = re.compile(r"^(?:#|javascript:|data:|file:|)$", re.I)


def audit_telegraph_html(html: str, *, url: str = "") -> list[TelegraphDomIssue]:
    text, hrefs, paragraphs = _text_from_html(html)
    issues: list[TelegraphDomIssue] = []

    if not _short(text, 20):
        issues.append(TelegraphDomIssue("empty_page", "page text is empty", url, "error"))
        return issues

    for pattern in _THIRD_PERSON_PATTERNS:
        m = pattern.search(text)
        if m:
            issues.append(TelegraphDomIssue(
                "third_person_wrapper",
                "third-person analytic wrapper is visible on published page",
                _short(text[max(0, m.start() - 80): m.end() + 120]),
            ))
            break

    m = _MARKDOWN_ARTIFACT_RE.search(text)
    if m:
        issues.append(TelegraphDomIssue(
            "markdown_artifact",
            "raw markdown or glue artifact is visible",
            _short(text[max(0, m.start() - 80): m.end() + 120]),
        ))

    m = _SOURCE_INVENTED_RU_TITLE_RE.search(text)
    if m and not re.search(r"\)\s*[—-]\s+", m.group(0)):
        issues.append(TelegraphDomIssue(
            "source_map_original_title",
            "source card may put invented Russian title before original title",
            _short(m.group(0)),
        ))

    empty_paragraphs = [p for p in paragraphs if not p.strip()]
    if empty_paragraphs:
        issues.append(TelegraphDomIssue(
            "empty_paragraphs",
            f"published page contains empty paragraph nodes: {len(empty_paragraphs)}",
            "",
        ))

    bad_links = [h for h in hrefs if _BAD_LINK_RE.match(str(h or "").strip())]
    if bad_links:
        issues.append(TelegraphDomIssue(
            "bad_links",
            f"published page contains broken-looking hrefs: {len(bad_links)}",
            ", ".join(bad_links[:5]),
        ))

    nav_mentions = len(re.findall(r"\b(?:Часть\s+\d+|Навигация|Разбор|Размышление|Конспект)\b", text, re.I))
    if "telegra.ph" in url and len(text) > 3500 and nav_mentions == 0:
        issues.append(TelegraphDomIssue(
            "navigation_missing_warning",
            "long Telegraph page has no visible navigation/cross-link markers",
            "",
        ))

    return issues


def summarize_dom_issues(results: Iterable[tuple[str, list[TelegraphDomIssue]]]) -> str:
    rows = list(results)
    total = len(rows)
    failed = [(url, issues) for url, issues in rows if issues]
    lines = [
        f"pages_checked={total}",
        f"pages_with_issues={len(failed)}",
    ]
    for url, issues in failed[:20]:
        issue_text = "; ".join(f"{i.code}: {i.snippet or i.message}" for i in issues[:4])
        lines.append(f"- {url}: {issue_text}")
    if len(failed) > 20:
        lines.append(f"- ... and {len(failed) - 20} more pages with issues")
    return "\n".join(lines)
