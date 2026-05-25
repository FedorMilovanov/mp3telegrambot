#!/usr/bin/env python3
"""Lightweight V3 validation helpers for Gemini video candidates.

This is not yet full Structured Output. It is a safe intermediate schema layer:
Gemini may still return plain JSON, but every candidate goes through one
canonical timing validator and produces a small validation report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Callable


@dataclass
class CandidateValidationReport:
    """Counts accepted/rejected generated candidates and rejection reasons."""

    accepted: int = 0
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def accept(self) -> None:
        self.accepted += 1

    def reject(self, reason: str) -> None:
        reason = reason or "unknown"
        self.rejected += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    def summary(self) -> str:
        if not self.reasons:
            return f"accepted={self.accepted} rejected={self.rejected}"
        top = ", ".join(f"{k}:{v}" for k, v in sorted(self.reasons.items())[:8])
        return f"accepted={self.accepted} rejected={self.rejected} reasons={top}"

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "total": self.total,
            "reasons": dict(self.reasons),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def parse_validation_summary(value: str | None) -> dict:
    """Parse validation_summary JSON stored in gemini_runs."""
    if not value:
        return {"accepted": 0, "rejected": 0, "total": 0, "reasons": {}}
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"accepted": 0, "rejected": 0, "total": 0, "reasons": {}}
    reasons = data.get("reasons") if isinstance(data, dict) else {}
    if not isinstance(reasons, dict):
        reasons = {}
    return {
        "accepted": int(data.get("accepted") or 0) if isinstance(data, dict) else 0,
        "rejected": int(data.get("rejected") or 0) if isinstance(data, dict) else 0,
        "total": int(data.get("total") or 0) if isinstance(data, dict) else 0,
        "reasons": {str(k): int(v or 0) for k, v in reasons.items()},
    }


def validate_candidate_times(
    start_raw: str,
    end_raw: str,
    *,
    duration: int | float = 0,
    min_sec: int | float = 0,
    max_sec: int | float = 10**9,
    time_to_seconds: Callable[[str], int | None],
) -> tuple[bool, str, int, int, int]:
    """Validate and normalize start/end fields for Shorts/Clips/Extras.

    Returns: (ok, reason, start_seconds, end_seconds, duration_seconds)
    """
    start_raw = str(start_raw or "").strip()
    end_raw = str(end_raw or "").strip()
    if not start_raw or not end_raw:
        return False, "missing_time", 0, 0, 0

    start_s = time_to_seconds(start_raw)
    end_s = time_to_seconds(end_raw)
    if start_s is None or end_s is None:
        return False, "bad_time_format", 0, 0, 0
    if start_s < 0:
        return False, "negative_start", start_s, end_s, 0
    if end_s <= start_s:
        return False, "end_before_start", start_s, end_s, 0
    if duration and end_s > duration:
        return False, "end_after_duration", start_s, end_s, end_s - start_s

    clip_len = end_s - start_s
    if clip_len < min_sec:
        return False, "too_short", start_s, end_s, clip_len
    if clip_len > max_sec:
        return False, "too_long", start_s, end_s, clip_len
    return True, "ok", start_s, end_s, clip_len


def _string_schema() -> dict:
    return {"type": "string"}


def _string_array_schema() -> dict:
    return {"type": "array", "items": _string_schema()}


def shorts_response_schema() -> dict:
    """Gemini structured-output schema for Shorts candidates."""
    return {
        "type": "object",
        "properties": {
            "shorts_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": _string_schema(),
                        "end": _string_schema(),
                        "title": _string_schema(),
                        "hook": _string_schema(),
                        "reason": _string_schema(),
                        "kind": _string_schema(),
                        "hashtags": _string_array_schema(),
                    },
                    "required": ["start", "end", "title", "reason", "kind", "hashtags"],
                },
            }
        },
        "required": ["shorts_candidates"],
    }


def clips_response_schema() -> dict:
    """Gemini structured-output schema for long clip candidates."""
    return {
        "type": "object",
        "properties": {
            "clip_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": _string_schema(),
                        "end": _string_schema(),
                        "title": _string_schema(),
                        "reason": _string_schema(),
                        "kind": _string_schema(),
                        "hashtags": _string_array_schema(),
                    },
                    "required": ["start", "end", "title", "reason", "kind", "hashtags"],
                },
            }
        },
        "required": ["clip_candidates"],
    }


def extras_response_schema() -> dict:
    """Gemini structured-output schema for Montage + Highlights candidates."""
    fragment_summary = {
        "type": "object",
        "properties": {
            "start": _string_schema(),
            "end": _string_schema(),
            "summary": _string_schema(),
        },
        "required": ["start", "end"],
    }
    fragment_hook = {
        "type": "object",
        "properties": {
            "start": _string_schema(),
            "end": _string_schema(),
            "hook": _string_schema(),
        },
        "required": ["start", "end"],
    }
    return {
        "type": "object",
        "properties": {
            "montage_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme": _string_schema(),
                        "title": _string_schema(),
                        "hashtags": _string_array_schema(),
                        "fragments": {"type": "array", "items": fragment_summary},
                    },
                    "required": ["theme", "title", "hashtags", "fragments"],
                },
            },
            "highlights": {
                "type": "object",
                "properties": {
                    "title": _string_schema(),
                    "hashtags": _string_array_schema(),
                    "fragments": {"type": "array", "items": fragment_hook},
                },
                "required": ["title", "hashtags", "fragments"],
            },
            "highlights_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": _string_schema(),
                        "hashtags": _string_array_schema(),
                        "fragments": {"type": "array", "items": fragment_hook},
                    },
                    "required": ["title", "hashtags", "fragments"],
                },
            },
        },
        "required": ["montage_candidates"],
    }


def structured_json_config_kwargs(schema: dict | None) -> dict:
    """Config kwargs for Gemini structured JSON output, or empty dict."""
    if not schema:
        return {}
    return {"response_mime_type": "application/json", "response_schema": schema}
