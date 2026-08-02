#!/usr/bin/env python3
"""Deterministic reconciliation of Synopsis section and inline timestamps.

Gemini returns two representations of the same timeline: ``sections[*].time``
and inline anchors inside each section.  Publication must have one source of
truth.  This module moves a section start earlier only when its own earliest
anchor proves that the section already began, preserves section ordering, and
then rebuilds the public outline from the reconciled section times.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from core.core_utils import time_to_seconds


_INLINE_TS_RE = re.compile(
    r"[⏱📌]\s*\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}"
)
_EXACT_TS_RE = re.compile(
    r"^\s*\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}\s*$"
)
_BLOCK_TEXT_FIELDS = (
    "text",
    "quote",
    "why_relevant",
    "role_in_argument",
    "challenge",
    "concrete_step",
)
_BLOCK_TIME_FIELDS = ("timestamp", "anchor_timestamp")


@dataclass(frozen=True)
class SynopsisTimestampIssue:
    code: str
    section_index: int
    message: str
    before: str = ""
    after: str = ""


def _format_seconds(value: int) -> str:
    value = max(0, int(value))
    hours, rem = divmod(value, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _times_from_text(value: Any) -> list[int]:
    found: list[int] = []
    for match in _INLINE_TS_RE.finditer(str(value or "")):
        seconds = time_to_seconds(match.group(1))
        if seconds is not None:
            found.append(seconds)
    return found


def section_anchor_seconds(section: dict[str, Any] | None) -> tuple[int, ...]:
    """Return every explicit video anchor owned by one section.

    Scripture references are not treated as video timestamps: prose anchors
    require ``⏱`` or ``📌``; structured timestamp fields must be an exact time.
    """
    if not isinstance(section, dict):
        return ()

    anchors = _times_from_text(section.get("content"))
    for field in _BLOCK_TIME_FIELDS:
        exact = _EXACT_TS_RE.match(str(section.get(field) or ""))
        if exact:
            seconds = time_to_seconds(exact.group(1))
            if seconds is not None:
                anchors.append(seconds)

    blocks = section.get("blocks") or []
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for field in _BLOCK_TEXT_FIELDS:
                anchors.extend(_times_from_text(block.get(field)))
            for step in block.get("steps") or []:
                anchors.extend(_times_from_text(step))
            for field in _BLOCK_TIME_FIELDS:
                exact = _EXACT_TS_RE.match(str(block.get(field) or ""))
                if exact:
                    seconds = time_to_seconds(exact.group(1))
                    if seconds is not None:
                        anchors.append(seconds)

    return tuple(sorted(set(anchors)))


def reconcile_synopsis_timestamps(
    sections: list[dict] | None,
    outline: list[dict] | None = None,
    *,
    tolerance_seconds: int = 5,
) -> tuple[list[dict], list[dict], list[SynopsisTimestampIssue]]:
    """Return reconciled section copies, authoritative outline and diagnostics.

    A start is moved only to the earliest explicit anchor in that same section,
    and only when doing so does not place it before the previous section.  An
    ambiguous cross-boundary anchor is reported rather than guessed.  Running
    the function repeatedly is idempotent.
    """
    clean_sections = [dict(item) for item in (sections or []) if isinstance(item, dict)]
    issues: list[SynopsisTimestampIssue] = []
    previous_start: int | None = None
    tolerance = max(0, int(tolerance_seconds))

    for index, section in enumerate(clean_sections):
        raw_start = str(section.get("time") or "").strip()
        current_start = time_to_seconds(raw_start) if raw_start else None
        anchors = section_anchor_seconds(section)
        earliest = anchors[0] if anchors else None

        if current_start is not None and previous_start is not None and current_start < previous_start:
            issues.append(SynopsisTimestampIssue(
                code="section_start_non_monotonic",
                section_index=index,
                message=(
                    f"section start {_format_seconds(current_start)} precedes previous "
                    f"section start {_format_seconds(previous_start)}"
                ),
                before=raw_start,
            ))

        target: int | None = None
        if earliest is not None:
            if current_start is None:
                target = earliest
            elif earliest < current_start - tolerance:
                target = earliest

        if target is not None:
            if previous_start is not None and target < previous_start:
                issues.append(SynopsisTimestampIssue(
                    code="section_time_reconcile_blocked",
                    section_index=index,
                    message=(
                        f"earliest own anchor {_format_seconds(target)} is before previous "
                        f"section start {_format_seconds(previous_start)}; boundary needs review"
                    ),
                    before=raw_start,
                ))
            else:
                new_start = _format_seconds(target)
                section["time"] = new_start
                current_start = target
                issues.append(SynopsisTimestampIssue(
                    code="section_time_reconciled",
                    section_index=index,
                    message=f"section start aligned to earliest own anchor {new_start}",
                    before=raw_start,
                    after=new_start,
                ))

        if current_start is not None:
            previous_start = current_start

    # The rendered sections are the source of truth.  An outline supplied by
    # Gemini may contribute a time only when the matching section has none.
    source_outline = [dict(item) for item in (outline or []) if isinstance(item, dict)]
    final_outline: list[dict] = []
    same_length = len(source_outline) == len(clean_sections)
    for index, section in enumerate(clean_sections):
        source_item = source_outline[index] if same_length else {}
        section_time = str(section.get("time") or "").strip()
        fallback_time = str(source_item.get("time") or "").strip()
        final_outline.append({
            "title": str(section.get("title") or ""),
            "time": section_time or fallback_time,
        })

    return clean_sections, final_outline, issues


def unresolved_timestamp_issues(
    sections: list[dict] | None,
    *,
    tolerance_seconds: int = 5,
) -> list[SynopsisTimestampIssue]:
    """Audit residual inline-before-section defects without mutating input."""
    issues: list[SynopsisTimestampIssue] = []
    tolerance = max(0, int(tolerance_seconds))
    for index, section in enumerate(sections or []):
        if not isinstance(section, dict):
            continue
        raw_start = str(section.get("time") or "").strip()
        start = time_to_seconds(raw_start) if raw_start else None
        if start is None:
            continue
        for anchor in section_anchor_seconds(section):
            if anchor < start - tolerance:
                issues.append(SynopsisTimestampIssue(
                    code="inline_timestamp_before_section",
                    section_index=index,
                    message=(
                        f"inline {_format_seconds(anchor)} is {start - anchor}s before "
                        f"section start {raw_start}"
                    ),
                    before=raw_start,
                ))
    return issues


def format_timestamp_issues(
    issues: list[SynopsisTimestampIssue] | None,
    *,
    limit: int = 12,
) -> str:
    rendered = [
        f"{item.code}@sections[{item.section_index}]: {item.message}"
        for item in (issues or [])[: max(0, int(limit))]
    ]
    if issues and len(issues) > limit:
        rendered.append(f"... и ещё {len(issues) - limit}")
    return " | ".join(rendered)
