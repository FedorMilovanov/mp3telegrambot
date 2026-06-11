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
import logging
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


def _safe_text(value: Any, limit: int = 4000) -> str:
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




def collect_quality_warnings(ai_data: dict | None) -> list[str]:
    """Collect non-fatal quality warnings that should survive outside logs."""
    ai = ai_data or {}
    out: list[str] = []
    if ai.get("timestamp_coverage_warning"):
        out.append("timestamp: " + _safe_text(ai.get("timestamp_coverage_warning"), 400))
    if ai.get("title_topic_warning"):
        out.append("title-topic: " + _safe_text(ai.get("title_topic_warning"), 400))
    for raw in _json_list(ai.get("quality_warnings") or []):
        if raw not in out:
            out.append(_safe_text(raw, 400))
    if ai.get("segments_status") == "partial":
        msg = "segments: partial timestamp coverage"
        if msg not in out:
            out.append(msg)
    return out


def timestamp_coverage_archive_fields(ai_data: dict | None) -> tuple[float, str]:
    """Return (ratio, segments_status) from enriched ai_data for archive/export."""
    ai = ai_data or {}
    try:
        ratio = float(ai.get("timestamp_coverage_ratio") or 0.0)
    except (TypeError, ValueError):
        ratio = 0.0
    status = str(ai.get("segments_status") or "complete").strip() or "complete"
    return ratio, status

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
    quality_warnings: Any = None,
    timestamp_coverage_ratio: float = 0.0,
    segments_status: str = "complete",
    caption_trim_stage: str = "",
    caption_timestamps_total: int = 0,
    caption_timestamps_shown: int = 0,
    model: str = "",
    prompt_version: str = "",
    prompt_variant: str = "",
    created_at: int | None = None,
) -> dict[str, Any]:
    ts = int(created_at or _now_ts())
    return {
        "video_id": _safe_text(video_id, 160),
        "source_url": _safe_text(source_url, 4096),
        "title": _safe_text(title, 400),
        "author": _safe_text(author, 240),
        "event": _safe_text(event, 240),
        "format": _safe_text(format_name, 80),
        "duration": int(duration or 0),
        "youtube_url": _safe_text(youtube_url or source_url, 4096),
        "rutube_url": _safe_text(rutube_url, 4096),
        "vk_url": _safe_text(vk_url, 4096),
        "synopsis_url": _safe_text(synopsis_url, 4096),
        "study_url": _safe_text(study_url, 4096),
        "reflection_url": _safe_text(reflection_url, 4096),
        "terms_url": _safe_text(terms_url, 4096),
        "questions_url": _safe_text(questions_url, 4096),
        "hashtags": _json_list(hashtags),
        "key_categories": _json_list(key_categories),
        "scripture_refs": _json_list(scripture_refs),
        "publication_status": _safe_text(publication_status or "unknown", 40),
        "publication_missing": _json_list(publication_missing),
        "publication_warning": _safe_text(publication_warning, 2000),
        "quality_warnings": _json_list(quality_warnings),
        "timestamp_coverage_ratio": float(timestamp_coverage_ratio or 0.0),
        "segments_status": _safe_text(segments_status or "complete", 40),
        "caption_trim_stage": _safe_text(caption_trim_stage, 80),
        "caption_timestamps_total": int(caption_timestamps_total or 0),
        "caption_timestamps_shown": int(caption_timestamps_shown or 0),
        "model": _safe_text(model, 120),
        "prompt_version": _safe_text(prompt_version, 120),
        "prompt_variant": _safe_text(prompt_variant or os.getenv("PROMPT_EXPERIMENT_TAG", ""), 120),
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
            quality_warnings TEXT DEFAULT '[]',
            timestamp_coverage_ratio REAL DEFAULT 0,
            segments_status TEXT DEFAULT 'complete',
            caption_trim_stage TEXT DEFAULT '',
            caption_timestamps_total INTEGER DEFAULT 0,
            caption_timestamps_shown INTEGER DEFAULT 0,
            model TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            prompt_variant TEXT DEFAULT '',
            last_repaired_at INTEGER DEFAULT 0,
            repair_count INTEGER DEFAULT 0,
            last_repair_changed_pages INTEGER DEFAULT 0,
            last_repair_errors TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    """)
    def _trusted_ident(name: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"unsafe column name: {name!r}")
        return name

    for col, ddl in [
        ("quality_warnings", "TEXT DEFAULT '[]'"),
        ("timestamp_coverage_ratio", "REAL DEFAULT 0"),
        ("segments_status", "TEXT DEFAULT 'complete'"),
        ("caption_trim_stage", "TEXT DEFAULT ''"),
        ("caption_timestamps_total", "INTEGER DEFAULT 0"),
        ("caption_timestamps_shown", "INTEGER DEFAULT 0"),
        ("prompt_variant", "TEXT DEFAULT ''"),
        ("last_repaired_at", "INTEGER DEFAULT 0"),
        ("repair_count", "INTEGER DEFAULT 0"),
        ("last_repair_changed_pages", "INTEGER DEFAULT 0"),
        ("last_repair_errors", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE generated_pages ADD COLUMN {_trusted_ident(col)} {ddl}")
        except sqlite3.OperationalError:
            pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_updated ON generated_pages(updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_author ON generated_pages(author)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_status ON generated_pages(publication_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generated_pages_repaired ON generated_pages(last_repaired_at)")


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
        record.get("publication_warning", ""), _json_dumps(record.get("quality_warnings", [])),
        float(record.get("timestamp_coverage_ratio") or 0.0), record.get("segments_status", "complete"),
        record.get("caption_trim_stage", ""), int(record.get("caption_timestamps_total") or 0),
        int(record.get("caption_timestamps_shown") or 0),
        record.get("model", ""), record.get("prompt_version", ""), record.get("prompt_variant", ""),
        int(record.get("created_at") or _now_ts()), int(record.get("updated_at") or _now_ts()),
    )


def _load_recent(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT video_id, source_url, title, author, event, format, duration,
               youtube_url, rutube_url, vk_url, synopsis_url, study_url, reflection_url,
               terms_url, questions_url, hashtags, key_categories, scripture_refs,
               publication_status, publication_missing, publication_warning,
               quality_warnings, timestamp_coverage_ratio, segments_status,
               caption_trim_stage, caption_timestamps_total, caption_timestamps_shown,
               model, prompt_version, prompt_variant, last_repaired_at, repair_count,
               last_repair_changed_pages, last_repair_errors, created_at, updated_at
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
        "quality_warnings", "timestamp_coverage_ratio", "segments_status",
        "caption_trim_stage", "caption_timestamps_total", "caption_timestamps_shown",
        "model", "prompt_version", "prompt_variant", "last_repaired_at", "repair_count",
        "last_repair_changed_pages", "last_repair_errors", "created_at", "updated_at",
    ]
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(keys, row))
        for k in ("hashtags", "key_categories", "scripture_refs", "publication_missing", "quality_warnings"):
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
    if record.get("prompt_variant"):
        lines.append(f"- Prompt variant: `{record['prompt_variant']}`")
    lines.append(f"- Status: `{status}`")
    if record.get("publication_warning"):
        lines.append(f"- Warning: {record['publication_warning']}")
    if record.get("quality_warnings"):
        lines.append("- Quality warnings: " + "; ".join(_json_list(record.get("quality_warnings"))[:8]))
    if record.get("segments_status") and record.get("segments_status") != "complete":
        ratio = float(record.get("timestamp_coverage_ratio") or 0.0)
        lines.append(f"- Segments: `{record.get('segments_status')}` (timestamp coverage {ratio:.0%})")
    if int(record.get("caption_timestamps_total") or 0) > int(record.get("caption_timestamps_shown") or 0) >= 0:
        lines.append(
            f"- Caption timestamps: {int(record.get('caption_timestamps_shown') or 0)}/"
            f"{int(record.get('caption_timestamps_total') or 0)}"
            + (f" ({record.get('caption_trim_stage')})" if record.get("caption_trim_stage") else "")
        )
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
    if int(record.get("last_repaired_at") or 0):
        lines.append(
            f"- Repaired: {_fmt_ts(record.get('last_repaired_at') or 0)} "
            f"(count={int(record.get('repair_count') or 0)}, "
            f"changed={int(record.get('last_repair_changed_pages') or 0)})"
        )
        if record.get("last_repair_errors"):
            lines.append(f"- Repair errors: {record['last_repair_errors']}")
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
                 quality_warnings, timestamp_coverage_ratio, segments_status,
                 caption_trim_stage, caption_timestamps_total, caption_timestamps_shown,
                 model, prompt_version, prompt_variant, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                quality_warnings=excluded.quality_warnings,
                timestamp_coverage_ratio=excluded.timestamp_coverage_ratio,
                segments_status=excluded.segments_status,
                caption_trim_stage=excluded.caption_trim_stage,
                caption_timestamps_total=excluded.caption_timestamps_total,
                caption_timestamps_shown=excluded.caption_timestamps_shown,
                model=excluded.model,
                prompt_version=excluded.prompt_version,
                prompt_variant=excluded.prompt_variant,
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


def query_generated_pages(
    *,
    limit: int = 10,
    query: str = "",
    author: str = "",
    scripture: str = "",
    status: str = "",
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Query generated pages archive for bot commands.

    SQLite ``LIKE`` is not reliably case-insensitive for Cyrillic, so we load a
    bounded recent window and filter in Python with ``casefold()``. The archive
    command is an admin/readout path, not a high-volume public search engine.
    """
    base = Path(base_dir or ARCHIVE_DIR)
    db_path = base / ARCHIVE_DB
    if not db_path.exists():
        return []
    limit = max(1, min(int(limit or 10), 50))
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        candidates = _load_recent(conn, limit=500)

    q = str(query or "").strip().casefold()
    a = str(author or "").strip().casefold()
    sr = str(scripture or "").strip().casefold()
    st = str(status or "").strip().casefold()

    def haystack(r: dict[str, Any]) -> str:
        parts = [
            r.get("title", ""), r.get("author", ""), r.get("event", ""),
            r.get("format", ""), r.get("publication_status", ""),
            " ".join(r.get("quality_warnings") or []), r.get("segments_status", ""),
            r.get("prompt_variant", ""),
            " ".join(r.get("hashtags") or []),
            " ".join(r.get("key_categories") or []),
            " ".join(r.get("scripture_refs") or []),
        ]
        return " ".join(str(x) for x in parts).casefold()

    out: list[dict[str, Any]] = []
    for r in candidates:
        h = haystack(r)
        if q and q not in h:
            continue
        if a and a not in str(r.get("author", "")).casefold():
            continue
        if sr and sr not in (" ".join(r.get("scripture_refs") or []) + " " + str(r.get("title", ""))).casefold():
            continue
        if st and st != str(r.get("publication_status", "")).casefold():
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out

async def aquery_generated_pages(**kwargs) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: query_generated_pages(**kwargs))


def get_related_materials(
    *,
    author: str = "",
    scripture: str = "",
    exclude_video_id: str = "",
    limit: int = 5,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch related materials from the archive for 'Read Next' blocks."""
    records = query_generated_pages(limit=limit + 1, author=author, scripture=scripture, base_dir=base_dir)
    out = []
    for r in records:
        if r.get("video_id") == exclude_video_id:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


async def aget_related_materials(**kwargs) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_related_materials(**kwargs))


def update_generated_page_repair_status(
    video_id: str,
    *,
    changed_pages: int = 0,
    errors: list[str] | None = None,
    base_dir: Path | None = None,
) -> None:
    """Persist repair metadata and refresh Markdown exports."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return
    base = Path(base_dir or ARCHIVE_DIR)
    db_path = base / ARCHIVE_DB
    if not db_path.exists():
        return
    now = _now_ts()
    err_text = "; ".join(str(e)[:180] for e in (errors or []) if str(e).strip())[:1000]
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE generated_pages
            SET last_repaired_at = ?,
                repair_count = COALESCE(repair_count, 0) + 1,
                last_repair_changed_pages = ?,
                last_repair_errors = ?,
                updated_at = ?
            WHERE video_id = ?
            """,
            (now, int(changed_pages or 0), err_text, now, video_id),
        )
        conn.commit()
        recent = _load_recent(conn, limit=200)
    _write_markdown_exports(base, recent)


async def aupdate_generated_page_repair_status(video_id: str, **kwargs) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: update_generated_page_repair_status(video_id, **kwargs))


def get_generated_page_record(video_id: str, base_dir: Path | None = None) -> dict[str, Any] | None:
    """Return one archived generated_pages record by exact video_id."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return None
    base = Path(base_dir or ARCHIVE_DIR)
    db_path = base / ARCHIVE_DB
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        rows = _load_recent(conn, limit=500)
    for row in rows:
        if str(row.get("video_id") or "") == video_id:
            return row
    return None


async def aget_generated_page_record(video_id: str, base_dir: Path | None = None) -> dict[str, Any] | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_generated_page_record(video_id, base_dir=base_dir))


def save_segment_plan_export(
    *,
    video_id: str,
    title: str,
    author: str = "",
    timestamps: Any = "",
    duration: int | float = 0,
    format_name: str = "",
    segments_status: str = "complete",
    timestamp_coverage_ratio: float = 0.0,
    base_dir: Path | None = None,
) -> dict[str, str]:
    """Write human/machine segment plan export for a processed video."""
    from core.segment_planner import build_segments_from_timestamps, format_segments_text

    video_id = _safe_text(video_id, 160) or _slug(title or str(_now_ts()))
    segments = build_segments_from_timestamps(timestamps, duration, format_name=format_name)
    base = Path(base_dir or ARCHIVE_DIR) / "segments"
    base.mkdir(parents=True, exist_ok=True)
    stem = _slug(video_id, 120)
    json_path = base / f"{stem}_segments.json"
    md_path = base / f"{stem}_segments.md"
    payload = {
        "video_id": video_id,
        "title": _safe_text(title, 400),
        "author": _safe_text(author, 240),
        "format": _safe_text(format_name, 80),
        "duration": int(duration or 0),
        "segments_status": _safe_text(segments_status or "complete", 40),
        "timestamp_coverage_ratio": float(timestamp_coverage_ratio or 0.0),
        "segments": [
            {"index": s.index, "start": s.start, "end": s.end, "duration": s.duration, "title": s.title, "kind": s.kind}
            for s in segments
        ],
        "updated_at": _now_ts(),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# Segments — {title or video_id}", ""]
    if author:
        md.append(f"Author: {author}")
    md.append(f"Video ID: `{video_id}`")
    if segments_status and segments_status != "complete":
        md.append(f"⚠️ Сегменты построены по неполной сетке таймкодов (coverage {float(timestamp_coverage_ratio or 0.0):.0%}).")
    md.append("")
    md.append(format_segments_text(segments) if segments else "Сегменты не найдены.")
    md.append("")
    md.append("Render examples:")
    md.append(f"- `/segments {video_id}`")
    md.append(f"- `/cutseg {video_id} 1`")
    md.append(f"- `/cutseg {video_id} 1,3,5`")
    md_path.write_text("\n".join(md).strip() + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path), "count": str(len(segments))}


async def asave_segment_plan_export(**kwargs) -> dict[str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: save_segment_plan_export(**kwargs))


def get_segment_export_paths(video_id: str, base_dir: Path | None = None) -> dict[str, str]:
    """Return expected segment export paths for a video_id."""
    video_id = _safe_text(video_id, 160)
    if not video_id:
        return {}
    base = Path(base_dir or ARCHIVE_DIR) / "segments"
    stem = _slug(video_id, 120)
    return {
        "md": str(base / f"{stem}_segments.md"),
        "json": str(base / f"{stem}_segments.json"),
    }


def get_archive_export_path(kind: str, base_dir: Path | None = None) -> Path | None:
    """Return a known archive export path by user-facing kind."""
    base = Path(base_dir or ARCHIVE_DIR)
    kind = (kind or "latest").strip().lower()
    mapping = {
        "latest": base / "latest.md",
        "last": base / "latest.md",
        "all": base / "all_links.md",
        "links": base / "all_links.md",
        "jsonl": base / ARCHIVE_JSONL,
        "readme": base / "README.md",
        "db": base / ARCHIVE_DB,
        "sqlite": base / ARCHIVE_DB,
    }
    return mapping.get(kind)


def cleanup_old_segment_files(max_age_days: int = 90) -> int:
    """Delete segment JSON/MD files older than max_age_days.

    Called from periodic_maintenance in main.py.
    Only deletes from SEGMENTS_DIR; archive DB records are untouched.
    """
    import time as _time
    cutoff = _time.time() - max_age_days * 86400
    deleted = 0
    segments_dir = ARCHIVE_DIR / "segments"
    if not segments_dir.exists():
        return 0
    for f in segments_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix not in (".json", ".md"):
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                deleted += 1
        except OSError:
            pass
    if deleted:
        logging.getLogger(__name__).info(
            "cleanup_old_segment_files: deleted %d files older than %d days", deleted, max_age_days
        )
    return deleted
