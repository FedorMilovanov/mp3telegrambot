#!/usr/bin/env python3
"""Дамп опубликованных Telegraph-страниц в docs/page_dumps/*.md.

Зачем: среда Claude Code не имеет сетевого доступа к telegra.ph, а GitHub
доступен. Запустите локально, закоммитьте docs/page_dumps и запушьте —
и страницы можно вычитывать прямо из репозитория.

Использование:
    python tools/dump_telegraph_pages.py <telegraph-url> [<url> ...]
    python tools/dump_telegraph_pages.py --last N   # N последних видео из архива

Токен НЕ нужен: getPage — публичный метод.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "page_dumps"


def _node_to_md(node, depth: int = 0) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag", "")
    inner = "".join(_node_to_md(c, depth + 1) for c in node.get("children", []) or [])
    if tag in ("h3", "h4"):
        return f"\n\n{'#' * (3 if tag == 'h3' else 4)} {inner}\n"
    if tag == "p":
        return f"\n{inner}\n"
    if tag == "blockquote":
        return f"\n> {inner}\n"
    if tag in ("b", "strong"):
        return f"**{inner}**"
    if tag in ("i", "em"):
        return f"*{inner}*"
    if tag == "a":
        href = (node.get("attrs") or {}).get("href", "")
        return f"[{inner}]({href})"
    if tag == "li":
        return f"\n- {inner}"
    if tag == "hr":
        return "\n\n---\n"
    if tag == "br":
        return "\n"
    return inner


def dump_url(url: str) -> Path | None:
    path = url.split("telegra.ph/")[-1].split("?")[0].strip("/")
    if not path:
        print(f"skip (не telegraph url): {url}")
        return None
    r = requests.get(
        f"https://api.telegra.ph/getPage/{path}",
        params={"return_content": "true"},
        timeout=30,
    )
    data = r.json()
    if not data.get("ok"):
        print(f"FAIL {path}: {data.get('error')}")
        return None
    page = data["result"]
    md = f"# {page.get('title', '')}\n\nURL: https://telegra.ph/{path}\n"
    md += "".join(_node_to_md(n) for n in page.get("content", []) or [])
    md = re.sub(r"\n{3,}", "\n\n", md)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{path}.md"
    out.write_text(md, encoding="utf-8")
    print(f"OK  {out.relative_to(ROOT)} ({len(md)} chars)")
    return out


def _urls_from_archive(last_n: int) -> list[str]:
    from core.generated_pages import query_generated_pages

    urls: list[str] = []
    for rec in query_generated_pages(limit=last_n):
        for key in ("synopsis_url", "study_url", "reflection_url", "terms_url", "questions_url"):
            u = str(rec.get(key) or "").strip()
            if u:
                urls.append(u)
    return urls


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "--last":
        urls = _urls_from_archive(int(argv[1]) if len(argv) > 1 else 3)
    else:
        urls = argv
    ok = 0
    for u in urls:
        if dump_url(u):
            ok += 1
    print(f"\nГотово: {ok}/{len(urls)}. Теперь: git add docs/page_dumps && "
          f"git commit -m 'page dumps' && git push")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
