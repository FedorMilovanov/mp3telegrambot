#!/usr/bin/env python3
"""Low-overhead request latency aggregation for production diagnosis.

The trace is intentionally log-only and bound to the explicit asyncio Task that
owns one dispatched video request. It does not change timeouts, retries, model
selection, prompts, media quality or persistence, and it deliberately avoids
ContextVar/ambient request state.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _LatencyTrace:
    trace_id: str
    mode: str
    started: float
    totals_ms: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _TraceHandle:
    task: asyncio.Task
    trace: _LatencyTrace
    previous: _LatencyTrace | None


_TRACE_BY_TASK: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_TRACE_LOCK = threading.RLock()


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _current_trace() -> _LatencyTrace | None:
    task = _current_task()
    if task is None:
        return None
    with _TRACE_LOCK:
        return _TRACE_BY_TASK.get(task)


def begin_latency_trace(mode: str) -> _TraceHandle | None:
    """Bind one request trace to the current asyncio Task."""
    task = _current_task()
    if task is None:
        return None
    trace = _LatencyTrace(
        trace_id=uuid.uuid4().hex[:10],
        mode=str(mode or "unknown").strip() or "unknown",
        started=time.perf_counter(),
    )
    with _TRACE_LOCK:
        previous = _TRACE_BY_TASK.get(task)
        _TRACE_BY_TASK[task] = trace
    logger.info("[LATENCY] trace=%s mode=%s start", trace.trace_id, trace.mode)
    return _TraceHandle(task=task, trace=trace, previous=previous)


def record_latency(stage: str, elapsed_seconds: float, *, count: int = 1) -> None:
    """Aggregate one measured elapsed interval into the current task trace."""
    trace = _current_trace()
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
    trace = _current_trace()
    if trace is None:
        return
    name = str(stage or "unknown").strip().replace(" ", "_")[:80] or "unknown"
    trace.counts[name] = trace.counts.get(name, 0) + max(1, int(count or 1))
    trace.totals_ms.setdefault(name, 0)


def current_latency_trace_id() -> str:
    trace = _current_trace()
    return trace.trace_id if trace is not None else ""


def finish_latency_trace(handle: _TraceHandle | None, *, outcome: str) -> str:
    """Log one compact aggregate line and restore any enclosing task trace."""
    if handle is None:
        return ""
    trace = handle.trace
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

    with _TRACE_LOCK:
        current = _TRACE_BY_TASK.get(handle.task)
        if current is trace:
            if handle.previous is None:
                _TRACE_BY_TASK.pop(handle.task, None)
            else:
                _TRACE_BY_TASK[handle.task] = handle.previous
    return summary


__all__ = [
    "begin_latency_trace",
    "current_latency_trace_id",
    "finish_latency_trace",
    "note_latency_event",
    "record_latency",
]
