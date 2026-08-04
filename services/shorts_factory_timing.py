#!/usr/bin/env python3
"""Exact render-envelope policy for Yandex LiveDub Factory cuts."""
from __future__ import annotations

import copy
import logging
import os
from typing import Any

from services.livedub_mix import get_mix_params

logger = logging.getLogger(__name__)

PUBLIC_SHORT_MAX_SEC = 180.0
PUBLIC_LONG_MAX_SEC = 900.0


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(value, 30.0))


def _candidate_seconds(item: dict[str, Any]) -> tuple[float, float]:
    try:
        start = max(0.0, float(item.get("start_seconds", 0)))
        end = max(0.0, float(item.get("end_seconds", 0)))
    except (TypeError, ValueError):
        return 0.0, 0.0
    return start, end


def _format_seconds(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _public_max_for_candidates(candidates: list[dict[str, Any]]) -> float:
    """Shorts and long candidates have disjoint duration ranges."""
    for item in candidates:
        start, end = _candidate_seconds(item)
        if end - start >= 300.0:
            return PUBLIC_LONG_MAX_SEC
    return PUBLIC_SHORT_MAX_SEC


def align_factory_livedub_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
) -> list[dict[str, Any]]:
    """Preserve the semantic start and append the complete Yandex audio tail."""
    if not candidates:
        return []

    params = get_mix_params()
    required_tail = max(0.0, float(params.get("tail_pad_ms", 0)) / 1000.0)
    desired_tail = required_tail + _env_float(
        "SHORTS_FACTORY_LIVEDUB_TAIL_EXTRA_SEC",
        0.15,
    )
    desired_pre_roll = _env_float(
        "SHORTS_FACTORY_LIVEDUB_PREROLL_SEC",
        0.25,
    )
    public_max = _public_max_for_candidates(candidates)
    source_limit = max(0.0, float(source_duration))

    aligned: list[dict[str, Any]] = []
    rejected: list[str] = []
    for item in copy.deepcopy(candidates):
        start, end = _candidate_seconds(item)
        semantic_duration = end - start
        if semantic_duration <= 0:
            rejected.append(str(item.get("title") or "invalid"))
            continue

        available_envelope = public_max - semantic_duration
        if available_envelope + 1e-6 < required_tail:
            rejected.append(str(item.get("title") or "too-long"))
            continue

        pre_roll = min(
            desired_pre_roll,
            start,
            max(0.0, available_envelope - required_tail),
        )
        tail = min(desired_tail, max(0.0, available_envelope - pre_roll))
        render_start = max(0.0, start - pre_roll)
        render_end = min(source_limit, end + tail)
        actual_tail = render_end - end

        if actual_tail + 1e-6 < required_tail:
            rejected.append(str(item.get("title") or "tail-truncated"))
            continue
        if render_end <= render_start or render_end - render_start > public_max + 1e-6:
            rejected.append(str(item.get("title") or "invalid-envelope"))
            continue

        item["start_seconds"] = render_start
        item["end_seconds"] = render_end
        item["duration_seconds"] = render_end - render_start
        item["start"] = _format_seconds(render_start)
        item["end"] = _format_seconds(render_end)
        item["livedub_semantic_start_seconds"] = start
        item["livedub_semantic_end_seconds"] = end
        item["livedub_preroll_seconds"] = pre_roll
        item["livedub_tail_seconds"] = actual_tail
        aligned.append(item)

    if rejected:
        logger.warning(
            "Shorts Factory LiveDub envelope rejected %d/%d candidates: %s",
            len(rejected),
            len(candidates),
            ", ".join(rejected[:8]),
        )
    if candidates and not aligned:
        raise RuntimeError(
            "Ни один кандидат не помещается в точный хвост Яндекс LiveDub "
            f"при лимите {int(public_max)} секунд"
        )
    return aligned


__all__ = [
    "PUBLIC_LONG_MAX_SEC",
    "PUBLIC_SHORT_MAX_SEC",
    "align_factory_livedub_candidates",
]
