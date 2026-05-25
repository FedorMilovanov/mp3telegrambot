#!/usr/bin/env python3
"""Observability helpers for V3 Gemini calls.

This module is intentionally small and dependency-light: it can be tested without
real Gemini clients and can be called from sync or async pipeline stages.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any

from core.globals import DB_PATH

logger = logging.getLogger(__name__)


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
            finish_reason      TEXT DEFAULT '',
            error              TEXT DEFAULT '',
            prompt_version     TEXT DEFAULT '',
            cache_key          TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_ts ON gemini_runs(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_task_ts ON gemini_runs(task, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_video_id ON gemini_runs(video_id)")


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
    finish_reason: str = "",
    error: str | None = None,
    prompt_version: str = "",
    cache_key: str = "",
    ts: float | None = None,
) -> int | None:
    """Persist one Gemini run metric row. Returns row id or None on failure."""
    task = _safe_text(task, 80) or "unknown"
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
        _safe_text(finish_reason, 120),
        _safe_text(error, 1000),
        _safe_text(prompt_version, 120),
        _safe_text(cache_key, 200),
    )

    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            _ensure_gemini_runs_table(conn)
            cur = conn.execute(
                """
                INSERT INTO gemini_runs (
                    ts, video_id, task, model, thinking_level,
                    input_tokens, output_tokens, cached_tokens, thinking_tokens,
                    total_tokens, duration_ms, retry_num, is_fallback, json_valid,
                    postprocess_fixes, finish_reason, error, prompt_version, cache_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
