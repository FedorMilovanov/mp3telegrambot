#!/usr/bin/env python3
"""Adaptive generation policy from Gemini observability.

This module turns observability into a gentle feedback loop. It does not change
prompts or block generation; it adjusts safe generation knobs when recent runs
show a clear pattern:

- many JSON-invalid responses -> lower temperature;
- MAX_TOKENS finish/errors -> raise max_tokens for the next run.

The defaults are conservative and require enough recent samples to avoid
reacting to noise.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from core.database import _db_conn
import time
from typing import Any

from core.globals import DB_PATH


@dataclass(frozen=True)
class GeminiTaskHealth:
    task: str
    total: int = 0
    json_invalid: int = 0
    max_tokens: int = 0
    errors: int = 0

    @property
    def json_invalid_rate(self) -> float:
        return self.json_invalid / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total else 0.0


@dataclass(frozen=True)
class AdaptiveGenerationParams:
    temperature: float
    max_tokens: int
    reason: str = "baseline"


def get_gemini_task_health(task: str, *, hours: int = 24, min_samples: int = 3) -> GeminiTaskHealth:
    """Read recent task health from gemini_runs.

    Returns zero health if DB/table is missing. This keeps runtime safe during
    first run and in tests.
    """
    task = str(task or "").strip()
    if not task:
        return GeminiTaskHealth(task="")
    since = time.time() - max(1, min(int(hours or 24), 24 * 30)) * 3600
    try:
        with _db_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN json_valid = 0 THEN 1 ELSE 0 END) AS json_invalid,
                       SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) AS errors,
                       SUM(CASE WHEN finish_reason LIKE '%MAX_TOKENS%' OR error = 'max_tokens' THEN 1 ELSE 0 END) AS max_tokens
                FROM gemini_runs
                WHERE ts >= ? AND task = ?
                """,
                (since, task),
            ).fetchone()
    except Exception:
        return GeminiTaskHealth(task=task)
    total = int((row[0] if row else 0) or 0)
    if total < min_samples:
        return GeminiTaskHealth(task=task, total=total)
    return GeminiTaskHealth(
        task=task,
        total=total,
        json_invalid=int((row[1] if row else 0) or 0),
        errors=int((row[2] if row else 0) or 0),
        max_tokens=int((row[3] if row else 0) or 0),
    )


def adapt_text_generation_params(
    *,
    task: str,
    base_temperature: float,
    base_max_tokens: int,
    health: GeminiTaskHealth | None = None,
    json_invalid_threshold: float = 0.30,
    max_token_multiplier: float = 1.5,
    token_cap: int = 65000,
) -> AdaptiveGenerationParams:
    """Return safe adaptive params for one Gemini text task."""
    h = health if health is not None else get_gemini_task_health(task)
    temperature = float(base_temperature)
    max_tokens = int(base_max_tokens)
    reasons: list[str] = []

    if h.total >= 3 and h.json_invalid_rate >= json_invalid_threshold:
        temperature = min(temperature, 0.2)
        reasons.append(f"json_invalid_rate={h.json_invalid_rate:.0%}")

    if h.total >= 1 and h.max_tokens > 0:
        max_tokens = min(int(max_tokens * max_token_multiplier), int(token_cap))
        reasons.append(f"max_tokens_hits={h.max_tokens}")

    return AdaptiveGenerationParams(
        temperature=temperature,
        max_tokens=max_tokens,
        reason="; ".join(reasons) if reasons else "baseline",
    )


def get_adaptive_text_generation_params(task: str, base_temperature: float, base_max_tokens: int) -> AdaptiveGenerationParams:
    """Convenience wrapper used by async pipelines."""
    health = get_gemini_task_health(task)
    return adapt_text_generation_params(
        task=task,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
        health=health,
    )
