#!/usr/bin/env python3
"""Low-overhead request latency aggregation for production diagnosis.

The trace is intentionally log-only and request-scoped. It does not change
timeouts, retries, model selection, prompts, media quality or persistence.
Async child tasks inherit the ContextVar, so shared yt-dlp/FFmpeg and Gemini
owners can attribute elapsed time to the active Telegram request without
threading trace IDs through every pipeline signature.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _LatencyTrace:
    trace_id: str
    mode: str
    started: float
    totals_ms: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


_CURRENT_TRACE: ContextVar[_LatencyTrace | None] = ContextVar(
    "mp3bot_latency_trace",
    default=None,
)


def begin_latency_trace(mode: str):
    """Start one request trace and return the ContextVar reset token."""
    trace = _LatencyTrace(
        trace_id=uuid.uuid4().hex[:10],
        mode=str(mode or "unknown").strip() or "unknown",
        started=time.perf_counter(),
    )
    token = _CURRENT_TRACE.set(trace)
    logger.info("[LATENCY] trace=%s mode=%s start", trace.trace_id, trace.mode)
    return token


def record_latency(stage: str, elapsed_seconds: float, *, count: int = 1) -> None:
    """Aggregate one measured elapsed interval into the active request trace."""
    trace = _CURRENT_TRACE.get()
    if trace is None:
        return
    name = str(stage or "unknown").strip().replace(" ", "_")[:80] or "unknown"
    try:
        seconds = float(elapsed_seconds)
    except (TypeError, ValueError, OverflowError):
        return
    if not math.isfinite(seconds) or seconds < 0:
        return
    elapsed_ms = max(0, int(round(seconds * 1000.0)))
    trace.totals_ms[name] = trace.totals_ms.get(name, 0) + elapsed_ms
    trace.counts[name] = trace.counts.get(name, 0) + max(1, int(count or 1))


def note_latency_event(stage: str, *, count: int = 1) -> None:
    """Count a diagnostic event without pretending it consumed elapsed time."""
    trace = _CURRENT_TRACE.get()
    if trace is None:
        return
    name = str(stage or "unknown").strip().replace(" ", "_")[:80] or "unknown"
    trace.counts[name] = trace.counts.get(name, 0) + max(1, int(count or 1))
    trace.totals_ms.setdefault(name, 0)


def current_latency_trace_id() -> str:
    trace = _CURRENT_TRACE.get()
    return trace.trace_id if trace is not None else ""


def finish_latency_trace(token, *, outcome: str) -> str:
    """Log one compact aggregate line and restore the previous trace context."""
    trace = _CURRENT_TRACE.get()
    summary = ""
    try:
        if trace is None:
            return ""
        total_ms = max(0, int(round((time.perf_counter() - trace.started) * 1000.0)))
        stage_parts: list[str] = []
        for stage in sorted(
            trace.totals_ms,
            key=lambda name: (-trace.totals_ms[name], name),
        ):
            elapsed_ms = trace.totals_ms[stage]
            calls = trace.counts.get(stage, 0)
            if elapsed_ms:
                stage_parts.append(f"{stage}={elapsed_ms / 1000.0:.2f}s/{calls}")
            else:
                stage_parts.append(f"{stage}=count:{calls}")
        stages = ",".join(stage_parts) if stage_parts else "none"
        summary = (
            f"[LATENCY] trace={trace.trace_id} mode={trace.mode} "
            f"outcome={str(outcome or 'unknown')[:80]} total={total_ms / 1000.0:.2f}s "
            f"stages={stages}"
        )
        logger.info(summary)
        return summary
    finally:
        _CURRENT_TRACE.reset(token)


__all__ = [
    "begin_latency_trace",
    "current_latency_trace_id",
    "finish_latency_trace",
    "note_latency_event",
    "record_latency",
]
