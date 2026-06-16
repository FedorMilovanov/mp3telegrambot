#!/usr/bin/env python3
"""Repair existing Telegraph pages using current deterministic postprocess.

No Gemini calls. This is for old pages: fetch content from Telegraph, run current
node postprocess/audit, editPage, and optionally refresh archive timestamps later.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Any

import requests

from converters.md_telegraph import _edit_telegraph_page, _postprocess_telegraph_nodes
from core.page_audit import audit_telegraph_page, format_audit_issues

logger = logging.getLogger(__name__)

_TELEGRAPH_URL_RE = re.compile(r"^https?://telegra\.ph/(?P<path>[^\s?#]+)", re.IGNORECASE)
_PAGE_URL_KEYS = (
    "synopsis_url", "study_url", "reflection_url", "terms_url", "questions_url",
)


@dataclass(frozen=True)
class TelegraphRepairResult:
    url: str
    ok: bool
    changed: bool = False
    title: str = ""
    error: str = ""
    audit_summary: str = ""


def telegraph_path_from_url(url: str) -> str:
    m = _TELEGRAPH_URL_RE.match(str(url or "").strip())
    return m.group("path") if m else ""


def collect_record_page_urls(record: dict[str, Any] | None) -> list[str]:
    if not isinstance(record, dict):
        return []
    urls: list[str] = []
    for key in _PAGE_URL_KEYS:
        url = str(record.get(key) or "").strip()
        if url and telegraph_path_from_url(url) and url not in urls:
            urls.append(url)
    return urls


def _nodes_signature(nodes: Any) -> str:
    return repr(nodes)


async def repair_telegraph_page_url(url: str) -> TelegraphRepairResult:
    """Repair one Telegraph page by URL. Returns result; never raises for API errors."""
    path = telegraph_path_from_url(url)
    if not path:
        return TelegraphRepairResult(url=url, ok=False, error="not_telegra_ph_url")
    loop = asyncio.get_running_loop()
    try:
        resp = await loop.run_in_executor(None, lambda: requests.get(
            f"https://api.telegra.ph/getPage/{path}?return_content=true",
            timeout=30,
        ))
        data = resp.json()
        if not data.get("ok"):
            return TelegraphRepairResult(url=url, ok=False, error=str(data.get("error") or "getPage_failed")[:300])
        page = data.get("result") or {}
        title = page.get("title") or ""
        author = page.get("author_name") or ""
        nodes = page.get("content") or []
        before = _nodes_signature(nodes)
        fixed_nodes = _postprocess_telegraph_nodes(nodes)
        after = _nodes_signature(fixed_nodes)
        changed = before != after
        issues = audit_telegraph_page(title, fixed_nodes, page_type="repair")
        audit_summary = format_audit_issues(issues, limit=4)
        if not changed:
            logger.info("Telegraph repair: no deterministic changes for %s", url)
            return TelegraphRepairResult(url=url, ok=True, changed=False, title=title, audit_summary=audit_summary)
        ok = await _edit_telegraph_page(url, title, author, fixed_nodes, loop)
        if not ok:
            return TelegraphRepairResult(url=url, ok=False, changed=True, title=title, error="editPage_failed", audit_summary=audit_summary)
        logger.info("Telegraph repair OK: %s changed=True", url)
        return TelegraphRepairResult(url=url, ok=True, changed=True, title=title, audit_summary=audit_summary)
    except Exception as exc:
        logger.warning("Telegraph repair failed for %s: %s", url, exc)
        return TelegraphRepairResult(url=url, ok=False, error=f"{type(exc).__name__}: {str(exc)[:240]}")


async def repair_generated_page_record(record: dict[str, Any]) -> list[TelegraphRepairResult]:
    urls = collect_record_page_urls(record)
    if not urls:
        return []
    results: list[TelegraphRepairResult] = []
    for url in urls:
        results.append(await repair_telegraph_page_url(url))
        await asyncio.sleep(1.0)  # gentle Telegraph API pacing
    return results


def format_repair_results(results: list[TelegraphRepairResult]) -> str:
    if not results:
        return "Нечего чинить: Telegraph-ссылки не найдены."
    ok_count = sum(1 for r in results if r.ok)
    changed_count = sum(1 for r in results if r.changed)
    lines = [f"🛠 Repair: {ok_count}/{len(results)} OK, changed={changed_count}", ""]
    for i, r in enumerate(results, 1):
        mark = "✅" if r.ok else "❌"
        changed = " · changed" if r.changed else ""
        lines.append(f"{i}. {mark} {r.url}{changed}")
        if r.error:
            lines.append(f"   error: {r.error}")
        if r.audit_summary:
            lines.append(f"   audit: {r.audit_summary[:220]}")
    return "\n".join(lines)


@dataclass(frozen=True)
class TelegraphRepairBatchItem:
    video_id: str
    title: str
    results: list[TelegraphRepairResult]


async def repair_generated_page_records(records: list[dict[str, Any]], *, limit: int = 10) -> list[TelegraphRepairBatchItem]:
    """Repair Telegraph pages for several archive records, gently and sequentially."""
    out: list[TelegraphRepairBatchItem] = []
    for record in (records or [])[: max(1, min(int(limit or 10), 20))]:
        results = await repair_generated_page_record(record)
        out.append(TelegraphRepairBatchItem(
            video_id=str(record.get("video_id") or ""),
            title=str(record.get("title") or "Без названия"),
            results=results,
        ))
        await asyncio.sleep(1.0)
    return out


def format_batch_repair_results(items: list[TelegraphRepairBatchItem]) -> str:
    if not items:
        return "Нечего чинить: записи архива не найдены."
    total_pages = sum(len(i.results) for i in items)
    ok_pages = sum(1 for i in items for r in i.results if r.ok)
    changed_pages = sum(1 for i in items for r in i.results if r.changed)
    lines = [f"🛠 Batch repair: records={len(items)}, pages={ok_pages}/{total_pages} OK, changed={changed_pages}", ""]
    for idx, item in enumerate(items, 1):
        item_ok = sum(1 for r in item.results if r.ok)
        item_changed = sum(1 for r in item.results if r.changed)
        lines.append(f"{idx}. {item.title} [{item.video_id}] — {item_ok}/{len(item.results)} OK, changed={item_changed}")
        for r in item.results[:5]:
            mark = "✅" if r.ok else "❌"
            suffix = " changed" if r.changed else ""
            lines.append(f"   {mark} {r.url}{suffix}")
            if r.error:
                lines.append(f"      {r.error}")
        if len(item.results) > 5:
            lines.append(f"   …ещё {len(item.results) - 5} страниц")
    return "\n".join(lines)
