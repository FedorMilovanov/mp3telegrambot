#!/usr/bin/env python3
"""Export a sanitized public archive of generated pages.

The bot runtime cache contains private/operational data: settings, rate limits,
Gemini metrics, full AI JSON, temporary IDs. None of that belongs in git.

This tool extracts only a small allow-list of public metadata and public links
(YouTube/Rutube/VK/Telegraph) into a Markdown file that is safe to commit.
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

PUBLIC_URL_COLUMNS = (
    "url",
    "telegraph_url",
    "quotes_tg_url",
    "study_tg_url",
    "reflection_tg_url",
    "terms_tg_url",
    "questions_tg_url",
    "rutube_url",
    "vk_url",
)

LINK_LABELS = {
    "url": "Источник",
    "telegraph_url": "Конспект",
    "quotes_tg_url": "Разбор",
    "study_tg_url": "Разбор",
    "reflection_tg_url": "Размышление",
    "terms_tg_url": "Термины",
    "questions_tg_url": "Вопросы",
    "rutube_url": "Rutube",
    "vk_url": "VK Видео",
}


def _safe_str(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", "").replace("\r", " ").strip()
    text = " ".join(text.split())
    return text[:limit]


def _load_ai(ai_data: str) -> dict[str, Any]:
    try:
        parsed = json.loads(ai_data or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _url(value: Any) -> str:
    text = _safe_str(value, 1000)
    if not (text.startswith("https://") or text.startswith("http://")):
        return ""
    # Keep the export intentionally boring: no markdown/html injection in URLs.
    if any(ch in text for ch in "<>'\"`\\"):
        return ""
    return text


def _row_get(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except Exception:
        return default


def extract_records(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        table_names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "video_cache" not in table_names:
            return []
        columns = {r[1] for r in conn.execute("PRAGMA table_info(video_cache)")}
        selected = ["video_id", "created_at", "ai_data"] + [c for c in PUBLIC_URL_COLUMNS if c in columns]
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM video_cache ORDER BY COALESCE(created_at, 0) DESC, video_id ASC"
        ).fetchall()

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in rows:
        ai = _load_ai(_row_get(row, "ai_data"))
        links: dict[str, str] = {}
        seen_urls: set[str] = set()
        for col in PUBLIC_URL_COLUMNS:
            if col not in row.keys():
                continue
            url = _url(_row_get(row, col))
            if url and url not in seen_urls:
                # Deduplicate same Telegraph URL under legacy aliases
                # (e.g. reflection_tg_url == questions_tg_url in older cache rows).
                label = LINK_LABELS.get(col, col)
                # FIX AUDIT R4: quotes_tg_url и study_tg_url делят ярлык
                # «Разбор» — при РАЗНЫХ URL вторая страница молча выпадала
                # из экспорта (setdefault терял url, но seen_urls его гасил).
                if label in links:
                    _n = 2
                    while f"{label} {_n}" in links:
                        _n += 1
                    label = f"{label} {_n}"
                links[label] = url
                seen_urls.add(url)
        if not links:
            continue
        video_id = _safe_str(_row_get(row, "video_id"), 80)
        title = _safe_str(ai.get("real_title") or "", 180) or "Без названия"
        author = _safe_str(ai.get("real_author") or "", 120)
        created_at = int(_row_get(row, "created_at", 0) or 0)
        key = video_id or next(iter(links.values()))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        records.append(
            {
                "video_id": video_id,
                "title": title,
                "author": author,
                "created_at": created_at,
                "links": links,
            }
        )
    return records


def render_markdown(records: list[dict[str, Any]], *, source_db: Path) -> str:
    generated = time.strftime("%Y-%m-%d %H:%M:%S %z")
    lines = [
        "# Архив опубликованных конспектов",
        "",
        "Санитизированный публичный индекс: только названия, авторы и публичные ссылки.",
        "Полный runtime cache, настройки, метрики Gemini, rate-limit и тексты AI JSON в git не хранятся.",
        "",
        f"_Сгенерировано: {generated} из `{source_db.name}`._",
        "",
    ]
    if not records:
        lines.append("Пока нет опубликованных ссылок.")
        return "\n".join(lines).rstrip() + "\n"

    by_day: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        day = time.strftime("%Y-%m-%d", time.localtime(int(rec.get("created_at") or 0))) if rec.get("created_at") else "unknown-date"
        by_day.setdefault(day, []).append(rec)

    for day in sorted(by_day.keys(), reverse=True):
        lines.extend([f"## {day}", ""])
        for rec in by_day[day]:
            title = html.escape(str(rec["title"]), quote=False)
            author = html.escape(str(rec.get("author") or "Автор не указан"), quote=False)
            vid = html.escape(str(rec.get("video_id") or ""), quote=False)
            suffix = f" · `{vid}`" if vid else ""
            lines.append(f"### {title} — {author}{suffix}")
            for label, url in rec["links"].items():
                lines.append(f"- {label}: {url}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def find_default_db() -> Path | None:
    candidates = []
    candidates.extend(Path(".").glob("bot_cache.db.bak.*"))
    candidates.append(Path("bot_cache.db"))
    existing = [p for p in candidates if p.exists() and p.is_file()]
    if not existing:
        return None
    return sorted(existing, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Export sanitized public generated-page links from bot cache DB")
    ap.add_argument("--db", type=Path, default=None, help="Path to bot_cache.db or bot_cache.db.bak.YYYYMMDD")
    ap.add_argument("--out", type=Path, default=Path("docs/generated_pages_archive.md"), help="Markdown output path")
    args = ap.parse_args()

    db_path = args.db or find_default_db()
    if not db_path:
        raise SystemExit("No bot_cache.db or bot_cache.db.bak.* found; pass --db explicitly")
    records = extract_records(db_path)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(records, source_db=db_path), encoding="utf-8")
    print(f"Exported {len(records)} public records to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
