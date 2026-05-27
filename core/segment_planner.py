#!/usr/bin/env python3
"""Deterministic segment planner from AI timestamps.

Used for selectable Q&A/theme cuts. No Gemini call: segments are derived from the
existing timestamp grid. For Q&A this naturally maps one question to one answer;
for sermons/lectures it gives thematic chunks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.core_utils import time_to_seconds


@dataclass(frozen=True)
class PlannedSegment:
    index: int
    start: int
    end: int
    title: str
    kind: str = "topic"

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)


def seconds_to_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _strip_markup(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", str(text or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_timestamp_lines(timestamps: Any) -> list[tuple[int, str]]:
    """Parse timestamps from either parser string or raw Gemini list."""
    out: list[tuple[int, str]] = []
    if isinstance(timestamps, list):
        for item in timestamps:
            if not isinstance(item, dict):
                continue
            sec = time_to_seconds(str(item.get("time") or ""))
            title = _strip_markup(item.get("topic") or "")
            if sec is not None and title:
                out.append((sec, title))
    else:
        for line in str(timestamps or "").splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$", line)
            if not m:
                continue
            sec = time_to_seconds(m.group(1))
            title = _strip_markup(m.group(2))
            if sec is not None and title:
                out.append((sec, title))
    # sort + dedupe by start second
    seen: set[int] = set()
    result: list[tuple[int, str]] = []
    for sec, title in sorted(out, key=lambda x: x[0]):
        if sec in seen:
            continue
        seen.add(sec)
        result.append((sec, title))
    return result


def build_segments_from_timestamps(
    timestamps: Any,
    duration: int | float = 0,
    *,
    format_name: str = "",
    end_padding: int = 8,
    min_segment: int = 45,
    max_segment: int = 18 * 60,
) -> list[PlannedSegment]:
    """Build selectable segments from timestamp boundaries.

    Segment end = next timestamp - small padding, or video duration for the last
    segment. Long topic chunks are split conservatively only by duration cap? No:
    we keep semantic timestamp boundaries and merely skip/mark too-short chunks.
    """
    try:
        total = int(duration or 0)
    except (TypeError, ValueError):
        total = 0
    points = parse_timestamp_lines(timestamps)
    if not points:
        return []
    kind = "qa" if (format_name or "").lower() == "qa" else "topic"
    segments: list[PlannedSegment] = []
    for idx, (start, title) in enumerate(points, 1):
        is_last = idx >= len(points)
        next_start = points[idx][0] if idx < len(points) else (total or start + max_segment)
        end = next_start if is_last else next_start - max(0, int(end_padding or 0))
        if total:
            end = min(end, total)
        if end <= start:
            end = next_start if next_start > start else start + min_segment
            if total:
                end = min(end, total)
        if end - start < min_segment:
            continue
        if end - start > max_segment:
            end = start + max_segment
        segments.append(PlannedSegment(index=len(segments) + 1, start=start, end=end, title=title, kind=kind))
    return segments


def format_segments_text(segments: list[PlannedSegment], *, title: str = "") -> str:
    if not segments:
        return "Сегменты не найдены: нужны таймкоды из AI-анализа."
    lines = []
    if title:
        lines.append(f"🎬 {title}")
        lines.append("")
    for s in segments[:40]:
        label = "Q&A" if s.kind == "qa" else "Тема"
        lines.append(
            f"{s.index}. {seconds_to_timestamp(s.start)}–{seconds_to_timestamp(s.end)} "
            f"({seconds_to_timestamp(s.duration)}) · {label}: {s.title}"
        )
    if len(segments) > 40:
        lines.append(f"…и ещё {len(segments) - 40}")
    return "\n".join(lines)


def get_segment_by_index(segments: list[PlannedSegment], index: int) -> PlannedSegment | None:
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    for segment in segments:
        if segment.index == idx:
            return segment
    return None


def parse_segment_selection(selection: str, total: int, *, max_count: int = 5) -> list[int]:
    """Parse `1`, `1,3,5`, `1-3` or `all` into safe 1-based indexes."""
    selection = str(selection or "").strip().lower()
    total = max(0, int(total or 0))
    max_count = max(1, int(max_count or 5))
    if not selection or total <= 0:
        return []
    values: list[int] = []
    if selection == "all":
        values = list(range(1, total + 1))
    else:
        for part in re.split(r"[,;\s]+", selection):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit():
                    start, end = int(a), int(b)
                    if start > end:
                        start, end = end, start
                    values.extend(range(start, end + 1))
            elif part.isdigit():
                values.append(int(part))
    out: list[int] = []
    seen: set[int] = set()
    for v in values:
        if 1 <= v <= total and v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= max_count:
            break
    return out
