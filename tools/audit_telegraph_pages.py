#!/usr/bin/env python3
"""Audit published Telegraph pages and append a Markdown quality-history entry.

Default input is docs/generated_pages_archive.md. Fetching prefers Playwright
when available (real browser rendering) and falls back to requests for CI/dev
machines without browsers.

Usage:
  python tools/audit_telegraph_pages.py --limit 20
  python tools/audit_telegraph_pages.py --url https://telegra.ph/...
  python tools/audit_telegraph_pages.py --url-file docs/telegraph_repair_targets_2026-06-16.md
  python tools/audit_telegraph_pages.py --sample-html sample.html
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from core.telegraph_dom_audit import TelegraphDomIssue, audit_telegraph_html, summarize_dom_issues


def write_repair_targets(path: Path, results: list[tuple[str, list[TelegraphDomIssue]]]) -> int:
    """Write a Markdown target list for pages that still have audit issues."""
    failing = [(url, issues) for url, issues in results if issues]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Telegraph repair targets",
        "",
        "Generated from DOM/page audit. These are technical pages worth deterministic repair.",
        "",
    ]
    if not failing:
        lines.append("No repair targets: audit found no issues.")
    for url, issues in failing:
        codes = ", ".join(sorted({i.code for i in issues}))
        lines.append(f"- {url}  <!-- {codes} -->")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return len(failing)


TELEGRAPH_URL_RE = re.compile(r"https://telegra\.ph/[^\s)\]]+")


def telegraph_path_from_url(url: str) -> str:
    m = re.match(r"^https?://telegra\.ph/(?P<path>[^\s?#]+)", str(url or "").strip(), re.I)
    return m.group("path") if m else ""


def _extract_next_part_urls_from_nodes(nodes: object, *, base: str = "https://telegra.ph") -> list[str]:
    out: list[str] = []

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("tag") == "a":
            text = str(node.get("children", ""))
            href = str((node.get("attrs") or {}).get("href") or "").strip()
            if href and "➡" in text:
                if href.startswith("/"):
                    href = base.rstrip("/") + href
                if telegraph_path_from_url(href) and href not in out:
                    out.append(href)
        for child in node.get("children", []) or []:
            walk(child)

    for item in nodes or []:
        walk(item)
    return out


def expand_telegraph_url_chain_sync(url: str, *, max_pages: int = 12, timeout: int = 30) -> list[str]:
    start = str(url or "").strip()
    if not telegraph_path_from_url(start):
        return []
    seen: set[str] = set()
    queue = [start]
    out: list[str] = []
    while queue and len(out) < max(1, min(int(max_pages or 12), 50)):
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
        try:
            path = telegraph_path_from_url(current)
            resp = requests.get(f"https://api.telegra.ph/getPage/{path}?return_content=true", timeout=timeout)
            data = resp.json()
            if not data.get("ok"):
                continue
            for nxt in _extract_next_part_urls_from_nodes((data.get("result") or {}).get("content") or []):
                if nxt not in seen and nxt not in queue:
                    queue.append(nxt)
        except Exception:
            continue
    return out


def expand_input_urls(urls: list[str], *, expand_chains: bool = True, timeout: int = 30) -> list[str]:
    if not expand_chains:
        return urls
    out: list[str] = []
    for url in urls:
        chain = expand_telegraph_url_chain_sync(url, timeout=timeout)
        for item in (chain or [url]):
            if item not in out:
                out.append(item)
    return out


def extract_telegraph_urls(markdown_path: Path) -> list[str]:
    if not markdown_path.exists():
        return []
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    seen: set[str] = set()
    urls: list[str] = []
    for raw in TELEGRAPH_URL_RE.findall(text):
        url = raw.rstrip(".,;:»)>")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


async def _fetch_with_playwright(url: str, timeout_ms: int) -> str | None:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return await page.content()
        finally:
            await browser.close()


def _fetch_with_requests(url: str, timeout: int) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "mp3telegrambot-audit/1.0"})
    resp.raise_for_status()
    return resp.text


async def fetch_page_html(url: str, *, prefer_playwright: bool = True, timeout: int = 30) -> tuple[str, str]:
    if prefer_playwright:
        try:
            html = await _fetch_with_playwright(url, timeout * 1000)
            if html:
                return html, "playwright"
        except Exception:
            # Browser unavailable or page load failed; requests fallback still gives
            # deterministic Telegraph HTML for the text-level audit.
            pass
    return _fetch_with_requests(url, timeout), "requests"


def _issues_to_dict(issues: list[TelegraphDomIssue]) -> list[dict]:
    return [
        {"code": i.code, "severity": i.severity, "message": i.message, "snippet": i.snippet}
        for i in issues
    ]


def append_history(history_path: Path, *, results: list[tuple[str, list[TelegraphDomIssue]]], fetcher: str, source: str) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    summary = summarize_dom_issues(results)
    lines = [
        "",
        f"## {stamp} — Telegraph DOM audit",
        "",
        f"Source: `{source}`",
        f"Fetcher: `{fetcher}`",
        "",
        "```text",
        summary,
        "```",
        "",
    ]
    with history_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


async def run_audit(
    urls: Iterable[str],
    *,
    prefer_playwright: bool = True,
    timeout: int = 30,
) -> tuple[list[tuple[str, list[TelegraphDomIssue]]], str]:
    results: list[tuple[str, list[TelegraphDomIssue]]] = []
    fetchers: set[str] = set()
    for url in urls:
        try:
            html, fetcher = await fetch_page_html(url, prefer_playwright=prefer_playwright, timeout=timeout)
            fetchers.add(fetcher)
            results.append((url, audit_telegraph_html(html, url=url)))
        except Exception as exc:
            results.append((url, [TelegraphDomIssue("fetch_failed", str(exc)[:300], url, "error")]))
    return results, "+".join(sorted(fetchers)) if fetchers else "none"


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit published Telegraph pages with Playwright/requests")
    ap.add_argument("--archive", type=Path, default=Path("docs/generated_pages_archive.md"))
    ap.add_argument("--history", type=Path, default=Path("docs/quality_audit_history.md"))
    ap.add_argument("--json-out", type=Path, default=Path("docs/telegraph_dom_audit.json"))
    ap.add_argument("--url", action="append", default=[], help="Telegraph URL to audit; may be repeated")
    ap.add_argument("--url-file", type=Path, default=None, help="Text/Markdown file containing Telegraph URLs")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--requests-only", action="store_true", help="Disable Playwright even if installed")
    ap.add_argument("--no-expand-chains", action="store_true", help="Do not follow Telegraph ➡ Дальше multipart links")
    ap.add_argument("--sample-html", type=Path, default=None, help="Audit one local HTML file without network")
    ap.add_argument("--repair-targets-out", type=Path, default=None, help="Write Markdown URL list for pages with issues")
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args()

    if args.sample_html:
        html = args.sample_html.read_text(encoding="utf-8", errors="replace")
        results = [(str(args.sample_html), audit_telegraph_html(html, url=str(args.sample_html)))]
        fetcher = "local-html"
        source = str(args.sample_html)
    else:
        urls = list(args.url)
        source = "--url" if urls else str(args.archive)
        if args.url_file:
            urls.extend(extract_telegraph_urls(args.url_file))
            source = str(args.url_file)
        if not urls:
            urls = extract_telegraph_urls(args.archive)
        if args.limit:
            urls = urls[: max(1, args.limit)]
        urls = expand_input_urls(urls, expand_chains=not args.no_expand_chains, timeout=args.timeout)
        results, fetcher = asyncio.run(run_audit(
            urls,
            prefer_playwright=not args.requests_only,
            timeout=args.timeout,
        ))

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(
            [{"url": url, "issues": _issues_to_dict(issues)} for url, issues in results],
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    if args.repair_targets_out:
        count = write_repair_targets(args.repair_targets_out, results)
    else:
        count = 0
    if not args.no_history:
        append_history(args.history, results=results, fetcher=fetcher, source=source)
    print(summarize_dom_issues(results))
    print(f"json={args.json_out}")
    if args.repair_targets_out:
        print(f"repair_targets={args.repair_targets_out} count={count}")
    if not args.no_history:
        print(f"history={args.history}")
    return 1 if any(issues for _, issues in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
