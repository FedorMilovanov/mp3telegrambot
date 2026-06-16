#!/usr/bin/env python3
"""Dry-run/apply deterministic repairs for Telegraph pages.

This is the companion to audit_telegraph_pages.py. It does not call Gemini.
It fetches Telegraph content, runs the current deterministic postprocess, audits
before/after, and optionally calls editPage when --apply is passed and
TELEGRAPH_TOKEN is configured.

Default is dry-run so technical pages can be inspected safely first.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from converters.md_telegraph import _edit_telegraph_page, _postprocess_telegraph_nodes
from core.page_audit import audit_telegraph_page, format_audit_issues
from services.telegraph_repair import telegraph_path_from_url
from tools.audit_telegraph_pages import extract_telegraph_urls


URL_RE = re.compile(r"https://telegra\.ph/[^\s)\]]+")


def _nodes_signature(nodes: Any) -> str:
    return json.dumps(nodes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_urls(args) -> list[str]:
    urls: list[str] = []
    for raw in args.url or []:
        urls.append(raw.strip())
    if args.url_file:
        text = args.url_file.read_text(encoding="utf-8", errors="replace")
        urls.extend(u.rstrip(".,;:»)> ") for u in URL_RE.findall(text))
    if not urls and args.archive:
        urls.extend(extract_telegraph_urls(args.archive))
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if telegraph_path_from_url(url) and url not in seen:
            seen.add(url)
            out.append(url)
    return out[: max(1, int(args.limit or 1000))]


def _fetch_page(url: str) -> dict[str, Any]:
    path = telegraph_path_from_url(url)
    resp = requests.get(f"https://api.telegra.ph/getPage/{path}?return_content=true", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "getPage_failed"))
    return data.get("result") or {}


async def _repair_one(url: str, *, apply: bool) -> dict[str, Any]:
    try:
        page = _fetch_page(url)
        title = page.get("title") or ""
        author = page.get("author_name") or ""
        nodes = page.get("content") or []
        before_sig = _nodes_signature(nodes)
        before_issues = audit_telegraph_page(title, nodes, page_type="repair")
        fixed_nodes = _postprocess_telegraph_nodes(nodes)
        after_sig = _nodes_signature(fixed_nodes)
        after_issues = audit_telegraph_page(title, fixed_nodes, page_type="repair")
        changed = before_sig != after_sig
        edit_ok = None
        if apply and changed:
            loop = asyncio.get_running_loop()
            edit_ok = await _edit_telegraph_page(url, title, author, fixed_nodes, loop)
        return {
            "url": url,
            "ok": True,
            "changed": changed,
            "applied": bool(edit_ok) if edit_ok is not None else False,
            "title": title,
            "before_issues": [i.__dict__ for i in before_issues],
            "after_issues": [i.__dict__ for i in after_issues],
            "before_summary": format_audit_issues(before_issues, limit=6),
            "after_summary": format_audit_issues(after_issues, limit=6),
        }
    except Exception as exc:
        return {
            "url": url,
            "ok": False,
            "changed": False,
            "applied": False,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


async def run(urls: list[str], *, apply: bool, pause: float = 1.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for url in urls:
        out.append(await _repair_one(url, apply=apply))
        if apply and pause > 0:
            await asyncio.sleep(pause)
    return out


def _summary(results: list[dict[str, Any]], *, apply: bool) -> str:
    changed = [r for r in results if r.get("changed")]
    unresolved = [r for r in results if r.get("after_issues")]
    errors = [r for r in results if not r.get("ok")]
    applied = [r for r in results if r.get("applied")]
    lines = [
        f"pages={len(results)} changed={len(changed)} unresolved_after={len(unresolved)} errors={len(errors)} applied={len(applied)} mode={'apply' if apply else 'dry-run'}",
    ]
    for r in changed[:30]:
        lines.append(f"- changed: {r['url']}")
        if r.get("before_summary"):
            lines.append(f"  before: {r['before_summary'][:260]}")
        if r.get("after_summary"):
            lines.append(f"  after: {r['after_summary'][:260]}")
    for r in errors[:20]:
        lines.append(f"- error: {r['url']} — {r.get('error')}")
    return "\n".join(lines)


def _append_history(path: Path, summary: str, *, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    with path.open("a", encoding="utf-8") as f:
        f.write(
            "\n\n"
            f"## {stamp} — Telegraph deterministic repair run\n\n"
            f"Source: `{source}`\n\n"
            "```text\n"
            f"{summary}\n"
            "```\n"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run/apply deterministic Telegraph repairs")
    ap.add_argument("--url", action="append", default=[], help="Telegraph URL; may be repeated")
    ap.add_argument("--url-file", type=Path, default=None, help="Text/Markdown file containing Telegraph URLs")
    ap.add_argument("--archive", type=Path, default=None, help="Archive markdown to extract Telegraph URLs from")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--apply", action="store_true", help="Actually call editPage. Requires TELEGRAPH_TOKEN.")
    ap.add_argument("--pause", type=float, default=1.0, help="Pause between editPage calls in apply mode")
    ap.add_argument("--json-out", type=Path, default=Path("docs/telegraph_repair_run.json"))
    ap.add_argument("--history", type=Path, default=Path("docs/quality_audit_history.md"))
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args()

    urls = _load_urls(args)
    if not urls:
        raise SystemExit("No Telegraph URLs found. Pass --url, --url-file, or --archive.")
    results = asyncio.run(run(urls, apply=args.apply, pause=args.pause))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = _summary(results, apply=args.apply)
    print(summary)
    print(f"json={args.json_out}")
    if not args.no_history:
        source = str(args.url_file or args.archive or "--url")
        _append_history(args.history, summary, source=source)
        print(f"history={args.history}")
    return 0 if not any(not r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
