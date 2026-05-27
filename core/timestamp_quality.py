#!/usr/bin/env python3
"""Timestamp coverage diagnostics.

No hallucinated timestamp labels are generated here. We only measure coverage and
log a warning when primary audio analysis covers too little of a long material.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.core_utils import time_to_seconds


_TS_LINE_RE = re.compile(r"^\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\b")


@dataclass(frozen=True)
class TimestampCoverageIssue:
    code: str
    message: str
    last_seconds: int
    duration_seconds: int
    coverage_ratio: float


def extract_timestamp_seconds(timestamps: str) -> list[int]:
    values: list[int] = []
    for line in str(timestamps or "").splitlines():
        m = _TS_LINE_RE.match(line)
        if not m:
            continue
        sec = time_to_seconds(m.group("time"))
        if sec is not None:
            values.append(sec)
    return values


def timestamp_coverage_ratio(timestamps: str, duration_seconds: int | float = 0) -> float:
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur <= 0:
        return 0.0
    seconds = extract_timestamp_seconds(timestamps)
    if not seconds:
        return 0.0
    return max(seconds) / dur


def audit_timestamp_coverage(
    timestamps: str,
    duration_seconds: int | float = 0,
    *,
    min_ratio: float = 0.72,
    min_duration: int = 20 * 60,
) -> TimestampCoverageIssue | None:
    """Return warning when timestamp list stops too early in a long material."""
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur < min_duration:
        return None
    seconds = extract_timestamp_seconds(timestamps)
    if not seconds:
        return TimestampCoverageIssue(
            code="timestamp_coverage_empty",
            message="timestamps are empty for a long material",
            last_seconds=0,
            duration_seconds=dur,
            coverage_ratio=0.0,
        )
    last = max(seconds)
    ratio = last / dur if dur else 0.0
    if ratio < min_ratio:
        return TimestampCoverageIssue(
            code="timestamp_coverage_low",
            message=f"timestamps cover only {ratio:.0%} of material duration",
            last_seconds=last,
            duration_seconds=dur,
            coverage_ratio=ratio,
        )
    return None
