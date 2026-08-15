#!/usr/bin/env python3
"""Pure disk-capacity policy for SHORTS FACTORY MAX."""
from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_GIB = 1024**3
_AUDIO_UNKNOWN_FLOOR_BYTES = 4 * _GIB
_VIDEO_UNKNOWN_FLOOR_BYTES = 6 * _GIB


def _finite_positive(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _selected_format_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = payload.get("requested_downloads")
    if not isinstance(raw_rows, list) or not raw_rows:
        raw_rows = payload.get("requested_formats")
    if not isinstance(raw_rows, list) or not raw_rows:
        raw_rows = [payload]

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        nested = raw.get("requested_formats")
        if isinstance(nested, list) and nested:
            rows.extend(item for item in nested if isinstance(item, dict))
        else:
            rows.append(raw)
    return rows


def estimate_factory_selection_payload(
    payload: dict[str, Any],
) -> tuple[int, float]:
    """Return selected download bytes and duration from an existing yt-dlp JSON."""
    duration = _finite_positive(payload.get("duration"))
    total = 0.0
    for row in _selected_format_rows(payload):
        row_duration = _finite_positive(row.get("duration")) or duration
        size = (
            _finite_positive(row.get("filesize"))
            or _finite_positive(row.get("filesize_approx"))
        )
        if not size:
            total_bitrate_kbps = _finite_positive(row.get("tbr"))
            if total_bitrate_kbps and row_duration:
                size = total_bitrate_kbps * 1000.0 * row_duration / 8.0
        total += size
        duration = max(duration, row_duration)
    return int(math.ceil(total)), duration


def required_factory_free_bytes(
    kind: str,
    estimated_download_bytes: int,
    duration_seconds: float,
) -> int:
    """Model peak disk usage, not only the final media file size."""
    estimate = max(0, int(estimated_download_bytes or 0))
    duration = max(0.0, float(duration_seconds or 0.0))
    if kind == "audio":
        # Native compressed source + temporary decode/encode working set + cache headroom.
        pcm_bound = duration * 48000.0 * 2.0 * 2.0
        modeled = estimate + pcm_bound * 1.10 + 512 * 1024**2
        floor = _AUDIO_UNKNOWN_FLOOR_BYTES if not estimate else 2 * _GIB
        return int(math.ceil(max(modeled, floor)))
    if kind == "video":
        if not estimate:
            # Unknown format size: assume a conservative 12 Mbps source.
            estimate = int(duration * 12_000_000.0 / 8.0)
        modeled = estimate * 2.20 + 1024**3
        return int(math.ceil(max(modeled, _VIDEO_UNKNOWN_FLOOR_BYTES)))
    raise ValueError(f"Unsupported Factory disk estimate kind: {kind}")


def ensure_factory_free_space(
    paths: Iterable[Path],
    *,
    required_bytes: int,
    label: str,
) -> None:
    """Require the modeled peak on every filesystem that may receive a copy."""
    checked: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        path.mkdir(parents=True, exist_ok=True)
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in checked:
            continue
        checked.add(key)
        free = int(shutil.disk_usage(path).free)
        if free < required_bytes:
            raise RuntimeError(
                f"SHORTS FACTORY: недостаточно места для {label}: "
                f"нужно около {required_bytes / _GIB:.1f} ГБ, "
                f"свободно {free / _GIB:.1f} ГБ в {path}"
            )
        logger.info(
            "Factory disk guard: %s path=%s required=%.2fGB free=%.2fGB",
            label,
            path,
            required_bytes / _GIB,
            free / _GIB,
        )


def ensure_factory_audio_space(
    paths: Iterable[Path],
    *,
    duration_seconds: float,
    estimated_download_bytes: int = 0,
) -> None:
    ensure_factory_free_space(
        paths,
        required_bytes=required_factory_free_bytes(
            "audio",
            estimated_download_bytes,
            duration_seconds,
        ),
        label="максимального analysis-аудио и его временной обработки",
    )


def ensure_factory_video_space(
    paths: Iterable[Path],
    *,
    duration_seconds: float,
    estimated_download_bytes: int = 0,
) -> None:
    ensure_factory_free_space(
        paths,
        required_bytes=required_factory_free_bytes(
            "video",
            estimated_download_bytes,
            duration_seconds,
        ),
        label="максимального видео, отдельных потоков и merge",
    )


def factory_delivery_sort_args(base_args: Iterable[str]) -> list[str]:
    """Prefer SDR, then maximize resolution/FPS for the delivery-compatible source."""
    result = list(base_args)
    result.extend(["--format-sort", "hdr:0,res,fps"])
    return result


__all__ = [
    "ensure_factory_audio_space",
    "ensure_factory_free_space",
    "ensure_factory_video_space",
    "estimate_factory_selection_payload",
    "factory_delivery_sort_args",
    "required_factory_free_bytes",
]
