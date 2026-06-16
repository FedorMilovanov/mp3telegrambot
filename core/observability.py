#!/usr/bin/env python3
"""Observability helpers for V3 Gemini calls.

This module is intentionally small and dependency-light: it can be tested without
real Gemini clients and can be called from sync or async pipeline stages.
"""

from __future__ import annotations

from core.database import _db_conn
import asyncio
import json
import logging
import sqlite3
import time
from typing import Any

from core.globals import DB_PATH
from core.candidate_schema import parse_validation_summary

logger = logging.getLogger(__name__)
_TABLE_ENSURED_DB_PATHS: set[str] = set()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any, max_len: int = 1000) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _bool_to_db(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def extract_usage_metadata(response: Any) -> dict[str, int]:
    """Extract token counters from google-genai response-like objects.

    Supports the field names currently seen in Gemini SDK responses and returns
    zeros when metadata is absent. This keeps observability calls safe in tests,
    fallbacks and mocked responses.
    """
    meta = getattr(response, "usage_metadata", None) if response is not None else None
    if meta is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }

    input_tokens = _safe_int(getattr(meta, "prompt_token_count", 0))
    output_tokens = _safe_int(getattr(meta, "candidates_token_count", 0))
    cached_tokens = _safe_int(getattr(meta, "cached_content_token_count", 0))
    thinking_tokens = _safe_int(
        getattr(meta, "thoughts_token_count", getattr(meta, "thinking_token_count", 0))
    )
    total_tokens = _safe_int(getattr(meta, "total_token_count", 0))
    if not total_tokens:
        total_tokens = input_tokens + output_tokens + thinking_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "thinking_tokens": thinking_tokens,
        "total_tokens": total_tokens,
    }


def extract_finish_reason(response: Any) -> str:
    """Best-effort finish_reason extraction from a Gemini response."""
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        reason = getattr(candidates[0], "finish_reason", "")
        return _safe_text(reason, 120)
    except Exception:
        return ""


def _ensure_gemini_runs_table(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gemini_runs (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                 REAL NOT NULL,
            video_id           TEXT DEFAULT '',
            task               TEXT NOT NULL,
            model              TEXT DEFAULT '',
            thinking_level     TEXT DEFAULT '',
            input_tokens       INTEGER DEFAULT 0,
            output_tokens      INTEGER DEFAULT 0,
            cached_tokens      INTEGER DEFAULT 0,
            thinking_tokens    INTEGER DEFAULT 0,
            total_tokens       INTEGER DEFAULT 0,
            duration_ms        INTEGER DEFAULT 0,
            retry_num          INTEGER DEFAULT 0,
            is_fallback        INTEGER DEFAULT 0,
            json_valid         INTEGER,
            postprocess_fixes  INTEGER DEFAULT 0,
            validation_summary TEXT DEFAULT '',
            finish_reason      TEXT DEFAULT '',
            error              TEXT DEFAULT '',
            prompt_version     TEXT DEFAULT '',
            cache_key          TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_ts ON gemini_runs(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_task_ts ON gemini_runs(task, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_video_id ON gemini_runs(video_id)")
    try:
        conn.execute("ALTER TABLE gemini_runs ADD COLUMN validation_summary TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass


def log_gemini_run(
    *,
    task: str,
    video_id: str = "",
    model: str = "",
    thinking_level: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    thinking_tokens: int = 0,
    total_tokens: int = 0,
    duration_ms: int = 0,
    retry_num: int = 0,
    is_fallback: bool = False,
    json_valid: bool | None = None,
    postprocess_fixes: int = 0,
    validation_summary: str | dict | None = "",
    finish_reason: str = "",
    error: str | None = None,
    prompt_version: str = "",
    cache_key: str = "",
    ts: float | None = None,
) -> int | None:
    """Persist one Gemini run metric row. Returns row id or None on failure."""
    task = _safe_text(task, 80) or "unknown"
    if isinstance(validation_summary, dict):
        validation_summary = json.dumps(validation_summary, ensure_ascii=False, sort_keys=True)
    row = (
        float(ts if ts is not None else time.time()),
        _safe_text(video_id, 200),
        task,
        _safe_text(model, 120),
        _safe_text(thinking_level, 40),
        _safe_int(input_tokens),
        _safe_int(output_tokens),
        _safe_int(cached_tokens),
        _safe_int(thinking_tokens),
        _safe_int(total_tokens),
        _safe_int(duration_ms),
        _safe_int(retry_num),
        1 if is_fallback else 0,
        _bool_to_db(json_valid),
        _safe_int(postprocess_fixes),
        _safe_text(validation_summary, 1000),
        _safe_text(finish_reason, 120),
        _safe_text(error, 1000),
        _safe_text(prompt_version, 120),
        _safe_text(cache_key, 200),
    )

    try:
        with _db_conn(DB_PATH) as conn:
            db_key = str(DB_PATH)
            if db_key not in _TABLE_ENSURED_DB_PATHS:
                _ensure_gemini_runs_table(conn)
                _TABLE_ENSURED_DB_PATHS.add(db_key)
            cur = conn.execute(
                """
                INSERT INTO gemini_runs (
                    ts, video_id, task, model, thinking_level,
                    input_tokens, output_tokens, cached_tokens, thinking_tokens,
                    total_tokens, duration_ms, retry_num, is_fallback, json_valid,
                    postprocess_fixes, validation_summary, finish_reason, error, prompt_version, cache_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as exc:
        logger.warning("log_gemini_run failed: %s", exc, exc_info=True)
        return None


async def alog_gemini_run(**kwargs: Any) -> int | None:
    """Async wrapper for log_gemini_run that does not block the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: log_gemini_run(**kwargs))


def log_gemini_response(
    *,
    response: Any,
    task: str,
    video_id: str = "",
    model: str = "",
    thinking_level: str | None = None,
    duration_ms: int = 0,
    retry_num: int = 0,
    is_fallback: bool = False,
    json_valid: bool | None = None,
    postprocess_fixes: int = 0,
    validation_summary: str | dict | None = "",
    error: str | None = None,
    prompt_version: str = "",
    cache_key: str = "",
) -> int | None:
    """Convenience helper: extract usage/finish_reason and persist one row."""
    usage = extract_usage_metadata(response)
    return log_gemini_run(
        task=task,
        video_id=video_id,
        model=model,
        thinking_level=thinking_level,
        duration_ms=duration_ms,
        retry_num=retry_num,
        is_fallback=is_fallback,
        json_valid=json_valid,
        postprocess_fixes=postprocess_fixes,
        validation_summary=validation_summary,
        finish_reason=extract_finish_reason(response),
        error=error,
        prompt_version=prompt_version,
        cache_key=cache_key,
        **usage,
    )


async def alog_gemini_response(**kwargs: Any) -> int | None:
    """Async wrapper for log_gemini_response."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: log_gemini_response(**kwargs))


def fetch_recent_gemini_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Return recent Gemini run rows as dictionaries for admin readouts."""
    limit = max(1, min(_safe_int(limit, 10), 50))
    try:
        with _db_conn(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_gemini_runs_table(conn)
            rows = conn.execute(
                """
                SELECT ts, video_id, task, model, thinking_level,
                       input_tokens, output_tokens, cached_tokens, thinking_tokens,
                       total_tokens, duration_ms, retry_num, is_fallback, json_valid,
                       postprocess_fixes, validation_summary, finish_reason, error
                FROM gemini_runs
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning("fetch_recent_gemini_runs failed: %s", exc, exc_info=True)
        return []


def summarize_gemini_runs(hours: int = 24) -> dict[str, Any]:
    """Aggregate Gemini run metrics for the last N hours."""
    hours = max(1, min(_safe_int(hours, 24), 24 * 30))
    since = time.time() - hours * 3600
    empty = {
        "hours": hours,
        "total_runs": 0,
        "errors": 0,
        "json_invalid": 0,
        "fallbacks": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cached_tokens": 0,
        "avg_duration_ms": 0,
        "by_task": [],
        "by_finish_reason": [],
        "candidate_rejections": 0,
        "candidate_rejection_reasons": [],
        "recent_errors": [],
    }
    try:
        with _db_conn(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_gemini_runs_table(conn)
            totals = conn.execute(
                """
                SELECT COUNT(*) AS total_runs,
                       SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) AS errors,
                       SUM(CASE WHEN json_valid = 0 THEN 1 ELSE 0 END) AS json_invalid,
                       SUM(CASE WHEN is_fallback = 1 THEN 1 ELSE 0 END) AS fallbacks,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(thinking_tokens), 0) AS thinking_tokens,
                       COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                       COALESCE(AVG(duration_ms), 0) AS avg_duration_ms
                FROM gemini_runs
                WHERE ts >= ?
                """,
                (since,),
            ).fetchone()
            if not totals or not totals["total_runs"]:
                return empty

            by_task = conn.execute(
                """
                SELECT task,
                       COUNT(*) AS runs,
                       SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) AS errors,
                       SUM(CASE WHEN json_valid = 0 THEN 1 ELSE 0 END) AS json_invalid,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(AVG(duration_ms), 0) AS avg_duration_ms
                FROM gemini_runs
                WHERE ts >= ?
                GROUP BY task
                ORDER BY runs DESC, task ASC
                LIMIT 12
                """,
                (since,),
            ).fetchall()
            by_finish = conn.execute(
                """
                SELECT COALESCE(NULLIF(finish_reason, ''), 'unknown') AS finish_reason,
                       COUNT(*) AS runs
                FROM gemini_runs
                WHERE ts >= ?
                GROUP BY COALESCE(NULLIF(finish_reason, ''), 'unknown')
                ORDER BY runs DESC
                LIMIT 8
                """,
                (since,),
            ).fetchall()
            recent_errors = conn.execute(
                """
                SELECT ts, task, model, error
                FROM gemini_runs
                WHERE ts >= ? AND error != ''
                ORDER BY ts DESC
                LIMIT 5
                """,
                (since,),
            ).fetchall()
            validation_rows = conn.execute(
                """
                SELECT validation_summary
                FROM gemini_runs
                WHERE ts >= ? AND validation_summary != ''
                """,
                (since,),
            ).fetchall()

        rejection_reasons: dict[str, int] = {}
        candidate_rejections = 0
        for row in validation_rows:
            parsed = parse_validation_summary(row["validation_summary"])
            candidate_rejections += _safe_int(parsed.get("rejected"))
            for reason, count in (parsed.get("reasons") or {}).items():
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + _safe_int(count)
        candidate_rejection_reasons = [
            {"reason": reason, "count": count}
            for reason, count in sorted(rejection_reasons.items(), key=lambda item: (-item[1], item[0]))
        ][:10]

        return {
            "hours": hours,
            "total_runs": _safe_int(totals["total_runs"]),
            "errors": _safe_int(totals["errors"]),
            "json_invalid": _safe_int(totals["json_invalid"]),
            "fallbacks": _safe_int(totals["fallbacks"]),
            "total_tokens": _safe_int(totals["total_tokens"]),
            "input_tokens": _safe_int(totals["input_tokens"]),
            "output_tokens": _safe_int(totals["output_tokens"]),
            "thinking_tokens": _safe_int(totals["thinking_tokens"]),
            "cached_tokens": _safe_int(totals["cached_tokens"]),
            "avg_duration_ms": _safe_int(totals["avg_duration_ms"]),
            "by_task": [dict(row) for row in by_task],
            "by_finish_reason": [dict(row) for row in by_finish],
            "candidate_rejections": candidate_rejections,
            "candidate_rejection_reasons": candidate_rejection_reasons,
            "recent_errors": [dict(row) for row in recent_errors],
        }
    except Exception as exc:
        logger.warning("summarize_gemini_runs failed: %s", exc, exc_info=True)
        return empty


def _fmt_int(value: Any) -> str:
    return f"{_safe_int(value):,}".replace(",", " ")


def format_gemini_metrics_report(hours: int = 24, recent_limit: int = 5) -> str:
    """Build an HTML-safe admin report for Telegram."""
    summary = summarize_gemini_runs(hours)
    recent = fetch_recent_gemini_runs(recent_limit)
    hours = summary["hours"]

    lines = [
        "📊 <b>Gemini metrics</b>",
        f"Период: <b>{hours}ч</b>",
        "",
        f"Всего вызовов: <b>{_fmt_int(summary['total_runs'])}</b>",
        f"Ошибки: <b>{_fmt_int(summary['errors'])}</b>",
        f"JSON invalid: <b>{_fmt_int(summary['json_invalid'])}</b>",
        f"Fallback: <b>{_fmt_int(summary['fallbacks'])}</b>",
        f"Avg latency: <b>{_safe_int(summary['avg_duration_ms'])}ms</b>",
        "",
        "<b>Tokens</b>",
        f"total={_fmt_int(summary['total_tokens'])} | in={_fmt_int(summary['input_tokens'])} | "
        f"out={_fmt_int(summary['output_tokens'])} | think={_fmt_int(summary['thinking_tokens'])} | "
        f"cached={_fmt_int(summary['cached_tokens'])}",
    ]

    if summary["by_task"]:
        lines.append("")
        lines.append("<b>By task</b>")
        for row in summary["by_task"][:8]:
            lines.append(
                f"• <code>{_safe_text(row.get('task'), 40)}</code>: "
                f"runs={_fmt_int(row.get('runs'))}, err={_fmt_int(row.get('errors'))}, "
                f"bad_json={_fmt_int(row.get('json_invalid'))}, "
                f"tok={_fmt_int(row.get('total_tokens'))}, avg={_safe_int(row.get('avg_duration_ms'))}ms"
            )

    if summary["by_finish_reason"]:
        lines.append("")
        lines.append("<b>Finish reasons</b>")
        lines.append(
            ", ".join(
                f"<code>{_safe_text(row.get('finish_reason'), 30)}</code>={_fmt_int(row.get('runs'))}"
                for row in summary["by_finish_reason"][:6]
            )
        )

    if summary["candidate_rejection_reasons"]:
        lines.append("")
        lines.append("<b>Candidate rejection reasons</b>")
        lines.append(f"Total rejected: <b>{_fmt_int(summary['candidate_rejections'])}</b>")
        lines.append(
            ", ".join(
                f"<code>{_safe_text(row.get('reason'), 32)}</code>={_fmt_int(row.get('count'))}"
                for row in summary["candidate_rejection_reasons"][:8]
            )
        )

    if summary["recent_errors"]:
        lines.append("")
        lines.append("<b>Recent errors</b>")
        for row in summary["recent_errors"][:5]:
            lines.append(
                f"• <code>{_safe_text(row.get('task'), 32)}</code> "
                f"{_safe_text(row.get('model'), 32)}: {_safe_text(row.get('error'), 120)}"
            )

    if recent:
        lines.append("")
        lines.append("<b>Recent runs</b>")
        for row in recent[:recent_limit]:
            status = "❌" if row.get("error") else ("⚠️" if row.get("json_valid") == 0 else "✅")
            lines.append(
                f"{status} <code>{_safe_text(row.get('task'), 30)}</code> "
                f"{_safe_text(row.get('model'), 28)} "
                f"{_safe_int(row.get('duration_ms'))}ms "
                f"tok={_fmt_int(row.get('total_tokens'))}"
            )

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3820].rstrip() + "\n\n<i>⚠️ Отчёт обрезан. Используйте меньший период.</i>"
    return text
