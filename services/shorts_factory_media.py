#!/usr/bin/env python3
"""Pure media/timing helpers for Factory and legacy LiveDub cut modes.

This module owns no runtime installation, ContextVar state or post-import
rebinding. Callers explicitly probe translated masters and align their own
candidate lists before rendering.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

from services.livedub_mix import get_mix_params
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(value, 30.0))


def livedub_downstream_envelope() -> tuple[float, float]:
    """Return ``(pre_roll, full_tail)`` for downstream translated cuts."""
    params = get_mix_params()
    required_tail = max(0.0, float(params.get("tail_pad_ms", 0)) / 1000.0)
    return (
        _env_float("LIVEDUB_DOWNSTREAM_PREROLL_SEC", 0.25),
        required_tail
        + _env_float("LIVEDUB_DOWNSTREAM_TAIL_EXTRA_SEC", 0.15),
    )


def align_livedub_interval(
    start_seconds: float,
    end_seconds: float,
    *,
    source_duration: float,
    public_max_seconds: float = 0.0,
) -> tuple[float, float] | None:
    """Build a context-safe interval without cutting the delayed Russian tail."""
    try:
        semantic_start = max(0.0, float(start_seconds))
        semantic_end = max(0.0, float(end_seconds))
        source_limit = max(0.0, float(source_duration))
        public_max = max(0.0, float(public_max_seconds))
    except (TypeError, ValueError, OverflowError):
        return None
    if semantic_end <= semantic_start or source_limit <= 0:
        return None

    pre_roll, desired_tail = livedub_downstream_envelope()
    required_tail = max(
        0.0,
        float(get_mix_params().get("tail_pad_ms", 0)) / 1000.0,
    )
    semantic_duration = semantic_end - semantic_start
    if public_max > 0 and semantic_duration + required_tail > public_max + 1e-6:
        return None

    available_extra = (
        max(0.0, public_max - semantic_duration)
        if public_max > 0
        else pre_roll + desired_tail
    )
    actual_pre = min(
        pre_roll,
        semantic_start,
        max(0.0, available_extra - required_tail),
    )
    actual_tail = min(desired_tail, max(0.0, available_extra - actual_pre))
    render_start = max(0.0, semantic_start - actual_pre)
    render_end = min(source_limit, semantic_end + actual_tail)

    if render_end - semantic_end + 1e-6 < required_tail:
        return None
    if render_end <= render_start:
        return None
    if public_max > 0 and render_end - render_start > public_max + 1e-6:
        return None
    return render_start, render_end


def align_livedub_candidate(
    candidate: dict[str, Any],
    *,
    source_duration: float,
    public_max_seconds: float,
) -> dict[str, Any] | None:
    """Copy one candidate and replace only its render-facing numeric interval."""
    if not isinstance(candidate, dict):
        return None
    interval = align_livedub_interval(
        candidate.get("start_seconds", 0),
        candidate.get("end_seconds", 0),
        source_duration=source_duration,
        public_max_seconds=public_max_seconds,
    )
    if interval is None:
        return None

    render_start, render_end = interval
    item = copy.deepcopy(candidate)
    item["livedub_semantic_start_seconds"] = float(candidate.get("start_seconds", 0))
    item["livedub_semantic_end_seconds"] = float(candidate.get("end_seconds", 0))
    item["start_seconds"] = render_start
    item["end_seconds"] = render_end
    item["duration_seconds"] = render_end - render_start
    return item


def align_livedub_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: float,
    public_max_seconds: float,
) -> list[dict[str, Any]]:
    aligned: list[dict[str, Any]] = []
    rejected: list[str] = []
    for candidate in candidates or []:
        item = align_livedub_candidate(
            candidate,
            source_duration=source_duration,
            public_max_seconds=public_max_seconds,
        )
        if item is None:
            rejected.append(str((candidate or {}).get("title") or "без названия"))
        else:
            aligned.append(item)
    if rejected:
        logger.warning(
            "LiveDub downstream rejected %d/%d cuts without a complete Russian tail: %s",
            len(rejected),
            len(candidates or []),
            ", ".join(rejected[:8]),
        )
    return aligned


def align_livedub_montage_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: float,
) -> list[dict[str, Any]]:
    """Expand every montage fragment and keep only fully safe candidates."""
    output: list[dict[str, Any]] = []
    for candidate in copy.deepcopy(candidates or []):
        fragments = candidate.get("fragments") or []
        aligned = align_livedub_candidates(
            fragments,
            source_duration=source_duration,
            public_max_seconds=0.0,
        )
        if not fragments or len(aligned) != len(fragments):
            continue
        candidate["fragments"] = aligned
        candidate["total_dur"] = sum(
            float(item["end_seconds"]) - float(item["start_seconds"])
            for item in aligned
        )
        output.append(candidate)
    return output


async def probe_livedub_source_duration(
    source_path: Path,
    *,
    fallback_duration: float = 0.0,
) -> float:
    """Return exact translated duration; fail closed by default on probe failure."""
    probe = await probe_media_async(Path(source_path))
    if media_probe_is_deliverable(probe):
        assert probe is not None
        return float(probe.duration)

    if _env_bool("LIVEDUB_DOWNSTREAM_REQUIRE_PROBE", True):
        raise RuntimeError(
            "LiveDub-файл не прошёл обязательный media probe: нужны "
            "доказанные video+audio и точная длительность"
        )

    logger.warning(
        "LiveDub source probe failed; explicit degraded fallback duration=%.3f",
        float(fallback_duration or 0.0),
    )
    return max(0.0, float(fallback_duration or 0.0))


async def validated_factory_source_duration(
    source_path: Path,
    expected_duration: int,
) -> float:
    """Return exact ffprobe duration and reject truncated video/audio sources."""
    probe = await probe_media_async(source_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("Общий Factory-источник не прошёл media probe (нужны video+audio)")
    assert probe is not None
    exact_duration = float(probe.duration)
    if exact_duration + 3.0 < float(expected_duration):
        raise RuntimeError(
            "Общий Factory-источник обрезан: "
            f"ожидалось около {expected_duration:.0f}с, получено {exact_duration:.3f}с"
        )
    return exact_duration


__all__ = [
    "align_livedub_candidate",
    "align_livedub_candidates",
    "align_livedub_interval",
    "align_livedub_montage_candidates",
    "livedub_downstream_envelope",
    "probe_livedub_source_duration",
    "validated_factory_source_duration",
]
