#!/usr/bin/env python3
"""Estimate selected yt-dlp formats and fail before unsafe Factory downloads."""
from __future__ import annotations

import functools
import json
import logging
import math
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_GIB = 1024**3
_AUDIO_UNKNOWN_FLOOR_BYTES = 4 * _GIB
_VIDEO_UNKNOWN_FLOOR_BYTES = 6 * _GIB
_ESTIMATE_TIMEOUT_SEC = 300
_INSTALLED = False


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
    """Return selected download bytes and duration from yt-dlp JSON."""
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
        # Worst supported preparation path: keep the native compressed stream
        # while decoding it to stereo 48kHz/16-bit lossless FLAC. FLAC cannot be
        # larger than a small overhead over this PCM bound in normal operation.
        pcm_bound = duration * 48000.0 * 2.0 * 2.0
        modeled = estimate + pcm_bound * 1.10 + 512 * 1024**2
        floor = _AUDIO_UNKNOWN_FLOOR_BYTES if not estimate else 2 * _GIB
        return int(math.ceil(max(modeled, floor)))
    if kind == "video":
        # yt-dlp may hold video-only + audio-only + merged output at once.
        if not estimate:
            # Conservative unknown-size model: 12 Mbps average source, then
            # separate-stream/merge peak. Known filesize/tbr normally wins.
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


async def estimate_factory_selection(
    url: str,
    format_selector: str,
) -> tuple[int, float]:
    """Ask yt-dlp which exact formats it would download, without media bytes."""
    import services.shorts_factory_source as source

    command = (
        list(source.YTDLP_BASE_ARGS)
        + source._factory_quality_sort_reset()
        + [
            "--format",
            format_selector,
            "--no-playlist",
            "--simulate",
            "--dump-single-json",
            url,
        ]
    )
    process = await source.run_cancellable_process(
        command,
        timeout=_ESTIMATE_TIMEOUT_SEC,
        text=True,
    )
    if process.returncode != 0:
        logger.warning(
            "Factory disk estimate failed for %s: %s",
            format_selector,
            source._stderr_tail(process),
        )
        return 0, 0.0

    stdout = str(getattr(process, "stdout", "") or "")
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return estimate_factory_selection_payload(payload)
    logger.warning(
        "Factory disk estimate returned no JSON for %s; using conservative floor",
        format_selector,
    )
    return 0, 0.0


def install_factory_disk_guard() -> bool:
    """Wrap maximum-quality source downloads with selected-format disk proof."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.shorts_factory as factory_pipeline
    import services.shorts_factory_source as source

    original_audio = source.download_factory_audio_source
    original_video = source.download_factory_video_source

    @functools.wraps(original_audio)
    async def guarded_audio(url: str, media_id: str) -> Path:
        estimated, duration = await estimate_factory_selection(
            url,
            "bestaudio/best",
        )
        required = required_factory_free_bytes("audio", estimated, duration)
        ensure_factory_free_space(
            [source.DOWNLOAD_DIR],
            required_bytes=required,
            label="максимального аудио и lossless FLAC",
        )
        return await original_audio(url, media_id)

    @functools.wraps(original_video)
    async def guarded_video(
        url: str,
        media_id: str,
        workdir: Path | None = None,
    ) -> Path:
        estimated, duration = await estimate_factory_selection(
            url,
            "bestvideo+bestaudio/best",
        )
        required = required_factory_free_bytes("video", estimated, duration)
        target_dir = Path(workdir) if workdir is not None else source.DOWNLOAD_DIR
        ensure_factory_free_space(
            [target_dir, source.DOWNLOAD_DIR],
            required_bytes=required,
            label="максимального видео, отдельных потоков и merge",
        )
        return await original_video(url, media_id, workdir=workdir)

    source.download_factory_audio_source = guarded_audio
    source.download_factory_video_source = guarded_video
    factory_pipeline._download_factory_audio = guarded_audio
    factory_pipeline.download_video_for_shorts = guarded_video

    eager_factory = sys.modules.get("pipelines.shorts_factory")
    if eager_factory is not None:
        eager_factory._download_factory_audio = guarded_audio
        eager_factory.download_video_for_shorts = guarded_video

    _INSTALLED = True
    logger.info(
        "Shorts Factory disk guard installed: selected-format filesize/tbr "
        "estimate, PCM/FLAC bound, separate-stream merge peak and all target "
        "filesystems"
    )
    return True


__all__ = [
    "ensure_factory_free_space",
    "estimate_factory_selection",
    "estimate_factory_selection_payload",
    "install_factory_disk_guard",
    "required_factory_free_bytes",
]
