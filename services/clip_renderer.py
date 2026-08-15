#!/usr/bin/env python3
"""Owned long-clip renderer with explicit silence-snap bounds."""
from __future__ import annotations

import asyncio
import logging
import math
import shutil
import subprocess
from pathlib import Path

from services.async_process import run_cancellable_process
from services.async_worker import await_owned_coroutine
from services.ffmpeg import _find_silence_end, _get_video_encoder

logger = logging.getLogger(__name__)


def clip_snap_ceiling(
    start_seconds: float,
    public_max_seconds: float | None,
    source_duration: float | int = 0.0,
) -> float | None:
    """Return the absolute source timestamp a long clip may never cross."""
    if public_max_seconds is None:
        return None
    try:
        start = float(start_seconds)
        maximum = float(public_max_seconds)
        source = float(source_duration or 0.0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(maximum) or maximum <= 0.0:
        return None
    ceiling = start + maximum
    if math.isfinite(source) and source > 0.0:
        ceiling = min(ceiling, source)
    return ceiling


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("render_clip cleanup failed for %s: %s", path, exc)


async def render_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    silence_snap_max_end: float | None = None,
) -> bool:
    """Render a long clip while enforcing an optional absolute snap ceiling."""
    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    _unlink(output_path)
    try:
        start = float(start_seconds)
        end = float(end_seconds)
        ceiling = (
            float(silence_snap_max_end)
            if silence_snap_max_end is not None
            else None
        )
    except (TypeError, ValueError, OverflowError):
        logger.warning("render_clip: invalid boundaries")
        return False
    if not math.isfinite(start) or not math.isfinite(end):
        return False
    if ceiling is not None and not math.isfinite(ceiling):
        return False
    if ceiling is not None and end > ceiling + 1e-9:
        logger.warning(
            "render_clip: requested end %.3fs exceeds hard ceiling %.3fs",
            end,
            ceiling,
        )
        return False

    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("render_clip: ffmpeg not found")
            return False
        if not source_video_path.exists():
            logger.warning("render_clip: source file not found: %s", source_video_path)
            return False
        if end <= start:
            logger.warning("render_clip: invalid range %.3f..%.3f", start, end)
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        adjusted = await _find_silence_end(
            source_video_path,
            end,
            search_window=8.0,
        )
        try:
            adjusted = float(adjusted)
        except (TypeError, ValueError, OverflowError):
            adjusted = end
        if not math.isfinite(adjusted):
            adjusted = end

        min_end = start + max(10.0, (end - start) * 0.5)
        max_end = end + 12.0
        if ceiling is not None:
            adjusted = min(adjusted, ceiling)
            max_end = min(max_end, ceiling)
        if min_end < adjusted <= max_end and abs(adjusted - end) > 0.1:
            snapped = float(round(adjusted))
            if ceiling is not None:
                snapped = min(snapped, ceiling)
            if snapped > start:
                logger.info(
                    "Clip end adjusted: %.3fs -> %.3fs (silence snap, ceiling=%s)",
                    end,
                    snapped,
                    f"{ceiling:.3f}" if ceiling is not None else "none",
                )
                end = snapped
        if ceiling is not None:
            end = min(end, ceiling)

        duration = end - start
        if duration <= 0.0:
            return False

        encoder, _, preset = await await_owned_coroutine(
            asyncio.to_thread(_get_video_encoder)
        )
        quality = (
            ["-rc", "vbr", "-cq", "22"]
            if encoder == "h264_nvenc"
            else ["-crf", "22"]
        )
        command = [
            ffmpeg,
            "-ss",
            str(start),
            "-i",
            str(source_video_path),
            "-t",
            str(duration),
            "-c:v",
            encoder,
            *preset,
            *quality,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]

        from core.resource_scheduler import scheduler

        async with scheduler.gpu_render:
            process = await run_cancellable_process(command, timeout=900, text=True)
        if process.returncode != 0:
            stderr_tail = (process.stderr or "")[-800:]
            file_ok = output_path.exists() and output_path.stat().st_size > 0
            if not (file_ok and "received signal 2" in stderr_tail):
                logger.warning("render_clip ffmpeg error: %s", stderr_tail)
                _unlink(output_path)
                return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            _unlink(output_path)
            return False

        logger.info(
            "Clip rendered: %s (%.3fs..%.3fs, %.3fs, %.1fMB)",
            output_path.name,
            start,
            end,
            duration,
            output_path.stat().st_size / (1024 * 1024),
        )
        return True
    except asyncio.CancelledError:
        _unlink(output_path)
        raise
    except subprocess.TimeoutExpired:
        logger.warning("render_clip: ffmpeg timeout")
    except Exception as exc:
        logger.warning("render_clip error: %s: %s", type(exc).__name__, exc)
    _unlink(output_path)
    return False


__all__ = ["clip_snap_ceiling", "render_clip"]
