#!/usr/bin/env python3
"""Duration-aware Synopsis density and coverage helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.core_utils import time_to_seconds


@dataclass(frozen=True)
class SynopsisDensityProfile:
    name: str
    sections: str
    section_len: str
    total_chars: str
    max_tokens: int
    min_sections: int
    min_total_chars: int


@dataclass(frozen=True)
class SynopsisQualityIssue:
    code: str
    message: str


def get_synopsis_density_profile(duration_seconds: int | float = 0) -> SynopsisDensityProfile:
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur and dur < 20 * 60:
        return SynopsisDensityProfile("short", "3-5", "250-800", "1800", 24000, 3, 1200)
    if dur and dur < 50 * 60:
        return SynopsisDensityProfile("medium", "5-9", "450-1300", "3500", 32000, 5, 2500)
    if dur and dur < 90 * 60:
        return SynopsisDensityProfile("long", "10-16", "700-1900", "9000", 48000, 9, 6000)
    return SynopsisDensityProfile("very_long", "12-20", "800-2200", "13000", 56000, 11, 8500)


def _section_time_seconds(section: dict[str, Any]) -> int | None:
    t = str((section or {}).get("time") or "").strip()
    return time_to_seconds(t) if t else None



def _section_text_chars(section: dict[str, Any]) -> int:
    blocks = section.get("blocks") if isinstance(section, dict) else None
    if isinstance(blocks, list) and blocks:
        return sum(
            len(str(b.get("text") or b.get("quote") or b.get("why_relevant") or b.get("role_in_argument") or ""))
            for b in blocks if isinstance(b, dict)
        )
    return len(str((section or {}).get("content") or "").strip())

def audit_synopsis_density(sections: list[dict], duration_seconds: int | float = 0) -> list[SynopsisQualityIssue]:
    """Return warnings when Synopsis looks too thin for material duration."""
    profile = get_synopsis_density_profile(duration_seconds)
    issues: list[SynopsisQualityIssue] = []
    valid = [s for s in sections or [] if isinstance(s, dict)]
    total_chars = sum(_section_text_chars(s) for s in valid)
    if len(valid) < profile.min_sections:
        issues.append(SynopsisQualityIssue(
            "synopsis_too_few_sections",
            f"sections={len(valid)} below profile minimum {profile.min_sections} for {profile.name}",
        ))
    if total_chars < profile.min_total_chars:
        issues.append(SynopsisQualityIssue(
            "synopsis_too_few_chars",
            f"content_chars={total_chars} below profile minimum {profile.min_total_chars} for {profile.name}",
        ))
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur >= 30 * 60:
        times = [_section_time_seconds(s) for s in valid]
        times = [t for t in times if t is not None]
        if times and max(times) / dur < 0.70:
            issues.append(SynopsisQualityIssue(
                "synopsis_time_coverage_low",
                f"last_section_time={max(times)} covers only {max(times)/dur:.0%} of duration",
            ))
    return issues


def format_synopsis_quality_issues(issues: list[SynopsisQualityIssue]) -> str:
    return " | ".join(f"{i.code}: {i.message}" for i in issues or [])


def synopsis_density_score(sections: list[dict], duration_seconds: int | float = 0) -> int:
    """Simple monotonic score for comparing original vs retry Synopsis."""
    valid = [s for s in sections or [] if isinstance(s, dict)]
    total_chars = sum(_section_text_chars(s) for s in valid)
    times = [_section_time_seconds(s) for s in valid]
    times = [t for t in times if t is not None]
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    # Coverage is a tie-breaker, not a substitute for real content.
    # Old bonus=2000 could make a thin retry beat a substantially deeper original.
    coverage_bonus = min(250, int((max(times) / dur) * 250)) if times and dur else 0
    return total_chars + len(valid) * 350 + coverage_bonus


def should_retry_synopsis_density(issues: list[SynopsisQualityIssue], duration_seconds: int | float = 0) -> bool:
    """Retry only for substantial long-material density problems."""
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur < 45 * 60:
        return False
    critical = {"synopsis_too_few_sections", "synopsis_too_few_chars"}
    return any(i.code in critical for i in issues or [])
