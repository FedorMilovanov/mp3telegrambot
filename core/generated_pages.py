#!/usr/bin/env python3
"""Persistent archive of generated Telegraph pages and source links.

This is intentionally separate from the operational cache. The cache may expire;
the archive is append/upsert + human-readable Markdown exports so the owner can
open a folder, copy links, and share them without using the bot UI.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


ARCHIVE_DIR = Path(os.getenv("GENERATED_PAGES_DIR", "data/generated_pages"))
ARCHIVE_DB = "generated_pages.sqlite"
ARCHIVE_JSONL = "generated_pages.jsonl"


def _now_ts() -> int:
    return int(time.time())


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "").replace("\r", " ").strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text[:limit]


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [x.strip() for x in re.split(r"[,;\n]", value) if x.strip()]
    return [str(value).strip()]


def _slug(value: str, limit: int = 80) -> str:
    value = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "_", value or "").strip("_")
    return (value or "archive")[:limit]


def extract_scripture_refs(ai_data: dict | None) -> list[str]:
    """Best-effort scripture refs extraction from parsed Gemini terms_data."""
    refs: list[str] = []
    td = (ai_data or {}).get("terms_data") or {}
    for raw in td.get("scripture", []) or []:
        ref = str(raw).split("||", 1)[0].strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def build_generated_page_record(
    *,
    video_id: str,
    source_url: str,
    title: str,
    author: str = "",
    event: str = "",
    format_name: str = "",
    duration: int = 0,
    youtube_url: str = "",
    rutube_url: str = "",
    vk_url: str = "",
    synopsis_url: str = "",
    study_url: str = "",
    reflection_url: str = "",
    terms_url: str = "",
    questions_url: str = "",
    hashtags: Any = None,
    key_categories: Any = None,
    scripture_refs: Any = None,
    publication_status: str = "unknown",
    publication_missing: Any = None,
    publication_warning: str = "",
    model: str = "",
    prompt_version: str = "",
    created_at: int | None = None,
) -> dict[str, Any]:
    ts = int(created_at or _now_ts())
    return {
        "video_id": _safe_text(video_id, 160),
        "source_url": _safe_text(source_url, 800),
        "title": _safe_text(title, 400),
        "author": _safe_text(author, 240),
        "event": _safe_text(event, 240),
        "format": _safe_text(format_name, 80),
        "duration": int(duration or 0),
        "youtube_url": _safe_text(youtube_url or source_url, 800),
        "rutube_url": _safe_text(rutube_url, 800),
        "vk_url": _safe_text(vk_url, 800),
        "synopsis_url": _safe_text(synopsis_url, 800),
        "study_url": _safe_text(study_url, 800),
        "reflection_url": _safe_text(reflection_url, 800),
        "terms_url": _safe_text(terms_url, 800),
        "questions_url": _safe_text(questions_url, 800),
        "hashtags": _json_list(hashtags),
        "key_categories": _json_list(key_categories),
        "scripture_refs": _json_list(scripture_refs),
        "publication_status": _safe_text(publication_status or "unknown", 40),
        "publication_missing": _json_list(publication_missing),
        "publication_warning": _safe_text(publication_warning, 800),
        "model": _safe_text(model, 120),
        "prompt_version": _safe_text(prompt_version, 120),
        "created_at": ts,
        "updated_at": ts,
    }


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generated_pages (
            video_id TEXT PRIMARY KEY,
            source_url TEXT DEFAULT '',
            title TEXT DEFAULT '',
            author TEXT DEFAULT '',
            event TEXT DEFAULT '',
            format TEXT DEFAULT '',
            duration INTEGER DEFAULT 0,
            youtube_url TEXT DEFAULT '',
            rutube_url TEXT DEFAULT '',
            vk_url TEXT DEFAULT '',
            synopsis_url TEXT DEFAULT '',
            study_url TEXT DEFAULT '',
            reflection_url TEXT DEFAULT '',
            terms_url TEXT DEFAULT '',
            questions_url TEXT DEFAULT '',
            hashtags TEXT DEFAULT '[]',
            key_categories TEXT DEFAULT '[]',
            scripture_refs TEXT DEFAULT '[]',
            publication_status TEXT DEFAULT 'unknown',
            publication_missing TEXT DEFAULT '[]',
            publication_warning TEXT DEFAULT '',
            model TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_updated ON generated_pages(updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_author ON generated_pages(author)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_status ON generated_pages(publication_status)")


def _record_values(record: dict[str, Any]) -> tuple:
    return (
        record.get("video_id", ""), record.get("source_url", ""),
        record.get("title", ""), record.get("author", ""), record.get("event", ""),
        record.get("format", ""), int(record.get("duration") or 0),
        record.get("youtube_url", ""), record.get("rutube_url", ""), record.get("vk_url", ""),
        record.get("synopsis_url", ""), record.get("study_url", ""), record.get("reflection_url", ""),
        record.get("terms_url", ""), record.get("questions_url", ""),
        _json_dumps(record.get("hashtags", [])), _json_dumps(record.get("key_categories", [])),
        _json_dumps(record.get("scripture_refs", [])),
        record.get("publication_status", "unknown"), _json_dumps(record.get("publication_missing", [])),
        record.get("publication_warning", ""), record.get("model", ""), record.get("prompt_version", ""),
        int(record.get("created_at") or _now_ts()), int(record.get("updated_at") or _now_ts()),
    )


def _load_recent(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT video_id, source_url, title, author, event, format, duration,
               youtube_url, rutube_url, vk_url, synopsis_url, study_url, reflection_url,
               terms_url, questions_url, hashtags, key_categories, scripture_refs,
               publication_status, publication_missing, publication_warning,
               model, prompt_version, created_at, updated_at
        FROM generated_pages
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    keys = [
        "video_id", "source_url", "title", "author", "event", "format", "duration",
        "youtube_url", "rutube_url", "vk_url", "synopsis_url", "study_url", "reflection_url",
        "terms_url", "questions_url", "hashtags", "key_categories", "scripture_refs",
        "publication_status", "publication_missing", "publication_warning",
        "model", "prompt_version", "created_at", "updated_at",
    ]
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(keys, row))
        for k in ("hashtags", "key_categories", "scripture_refs", "publication_missing"):
            try:
                item[k] = json.loads(item.get(k) or "[]")
            except Exception:
                item[k] = []
        out.append(item)
    return out


def _fmt_ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts or 0)))


def _md_record(record: dict[str, Any]) -> str:
    title = record.get("title") or "Без названия"
    author = record.get("author") or "Автор не указан"
    status = record.get("publication_status") or "unknown"
    lines = [f"### {title} — {author}", ""]
    if record.get("event"):
        lines.append(f"- Event: {record['event']}")
    lines.append(f"- Status: `{status}`")
    if record.get("publication_warning"):
        lines.append(f"- Warning: {record['publication_warning']}")
    if record.get("source_url"):
        lines.append(f"- Source: {record['source_url']}")
    for label, key in [
        ("YouTube", "youtube_url"), ("RuTube", "rutube_url"), ("VK", "vk_url"),
        ("Конспект", "synopsis_url"), ("Разбор", "study_url"),
        ("Размышление", "reflection_url"), ("Термины", "terms_url"),
        ("Вопросы", "questions_url"),
    ]:
        if record.get(key):
            lines.append(f"- {label}: {record[key]}")
    if record.get("hashtags"):
        lines.append("- Tags: " + " ".join("#" + str(x).lstrip("#") for x in record["hashtags"]))
    if record.get("scripture_refs"):
        lines.append("- Scripture: " + "; ".join(record["scripture_refs"][:12]))
    lines.append(f"- Updated: {_fmt_ts(record.get('updated_at') or record.get('created_at') or 0)}")
    lines.append("")
    return "\n".join(lines)


def _write_markdown_exports(base_dir: Path, records: list[dict[str, Any]]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "README.md").write_text(
        "# generated_pages archive\n\n"
        "Эта папка создаётся ботом автоматически. Здесь лежат все опубликованные ссылки "
        "в удобном для копирования виде.\n\n"
        "- `latest.md` — последние публикации\n"
        "- `all_links.md` — компактный список ссылок\n"
        "- `by_date/YYYY-MM-DD.md` — публикации по датам\n"
        "- `generated_pages.jsonl` — машинный экспорт\n"
        "- `generated_pages.sqlite` — локальная SQLite-база архива\n",
        encoding="utf-8",
    )
    latest = ["# Generated pages — latest", ""]
    all_links = ["# Generated pages — all links", ""]
    by_date: dict[str, list[str]] = {}
    for r in records:
        day = time.strftime("%Y-%m-%d", time.localtime(int(r.get("updated_at") or 0)))
        block = _md_record(r)
        latest.append(block)
        compact = [f"## {r.get('title') or 'Без названия'} — {r.get('author') or 'Автор не указан'}", ""]
        for key in ("source_url", "synopsis_url", "study_url", "reflection_url", "terms_url", "questions_url"):
            if r.get(key):
                compact.append(f"- {key}: {r[key]}")
        compact.append("")
        all_links.extend(compact)
        by_date.setdefault(day, [f"# Generated pages — {day}", ""]).append(block)
    (base_dir / "latest.md").write_text("\n".join(latest).strip() + "\n", encoding="utf-8")
    (base_dir / "all_links.md").write_text("\n".join(all_links).strip() + "\n", encoding="utf-8")
    by_date_dir = base_dir / "by_date"
    by_date_dir.mkdir(exist_ok=True)
    for day, chunks in by_date.items():
        (by_date_dir / f"{_slug(day)}.md").write_text("\n".join(chunks).strip() + "\n", encoding="utf-8")


def save_generated_page_record(record: dict[str, Any], base_dir: Path | None = None) -> None:
    """Upsert a generated page record and refresh human-readable exports."""
    base = Path(base_dir or ARCHIVE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / ARCHIVE_DB
    now = _now_ts()
    record = {**record, "updated_at": now, "created_at": int(record.get("created_at") or now)}
    if not record.get("video_id"):
        # Stable fallback key for non-YouTube/manual inputs.
        record["video_id"] = _slug(record.get("source_url") or record.get("title") or str(now), 120)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO generated_pages
                (video_id, source_url, title, author, event, format, duration,
                 youtube_url, rutube_url, vk_url, synopsis_url, study_url, reflection_url,
                 terms_url, questions_url, hashtags, key_categories, scripture_refs,
                 publication_status, publication_missing, publication_warning,
                 model, prompt_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                source_url=excluded.source_url,
                title=excluded.title,
                author=excluded.author,
                event=excluded.event,
                format=excluded.format,
                duration=excluded.duration,
                youtube_url=excluded.youtube_url,
                rutube_url=excluded.rutube_url,
                vk_url=excluded.vk_url,
                synopsis_url=excluded.synopsis_url,
                study_url=excluded.study_url,
                reflection_url=excluded.reflection_url,
                terms_url=excluded.terms_url,
                questions_url=excluded.questions_url,
                hashtags=excluded.hashtags,
                key_categories=excluded.key_categories,
                scripture_refs=excluded.scripture_refs,
                publication_status=excluded.publication_status,
                publication_missing=excluded.publication_missing,
                publication_warning=excluded.publication_warning,
                model=excluded.model,
                prompt_version=excluded.prompt_version,
                updated_at=excluded.updated_at
            """,
            _record_values(record),
        )
        conn.commit()
        recent = _load_recent(conn, limit=200)
    with (base / ARCHIVE_JSONL).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    _write_markdown_exports(base, recent)


async def asave_generated_page_record(record: dict[str, Any], base_dir: Path | None = None) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: save_generated_page_record(record, base_dir=base_dir))
