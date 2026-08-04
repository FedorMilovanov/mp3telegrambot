#!/usr/bin/env python3
"""Highest-quality two-pass fit for oversized Factory long clips."""
from __future__ import annotations

import functools
import logging
import math
import os
import shutil
from pathlib import Path

from core.database import get_max_file_size_mb
from services.async_process import run_cancellable_process
from services.media_delivery_probe import (
    media_probe_is_deliverable,
    probe_media_async,
)

logger = logging.getLogger(__name__)

_FACTORY_LONG_AUDIO_KBPS = 192
_FACTORY_LONG_MIN_VIDEO_KBPS = 500
_FACTORY_LONG_SIZE_RESERVE = 0.965
_FACTORY_LONG_FIT_TIMEOUT_SEC = 7200
_FACTORY_LONG_MAX_SEC = 900.0
_FACTORY_LONG_DURATION_EPSILON_SEC = 0.05
_FACTORY_LONG_EXPECTED_TOLERANCE_SEC = 0.75
_FACTORY_LONG_FIT_DISK_RESERVE_BYTES = 512 * 1024**2
_INSTALLED = False


def factory_long_target_video_kbps(
    max_file_size_mb: float,
    duration_seconds: float,
    *,
    audio_kbps: int = _FACTORY_LONG_AUDIO_KBPS,
    reserve: float = _FACTORY_LONG_SIZE_RESERVE,
) -> int:
    """Return the highest video bitrate that fits the hard file limit."""
    try:
        size_mb = float(max_file_size_mb)
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Factory long fit received invalid size/duration") from exc
    if not math.isfinite(size_mb) or size_mb <= 0:
        raise RuntimeError("Factory long fit requires a positive file-size limit")
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Factory long fit requires a positive duration")

    bounded_reserve = min(max(float(reserve), 0.80), 0.99)
    total_kbps = size_mb * 1024.0 * 1024.0 * 8.0
    total_kbps = total_kbps * bounded_reserve / duration / 1000.0
    video_kbps = int(math.floor(total_kbps - float(audio_kbps) - 24.0))
    if video_kbps < _FACTORY_LONG_MIN_VIDEO_KBPS:
        raise RuntimeError(
            "Factory long clip cannot fit the Telegram limit without an "
            f"unacceptable video bitrate ({video_kbps} kbps)"
        )
    return video_kbps


def factory_long_fit_required_free_bytes(max_file_size_mb: float) -> int:
    """Reserve one target file plus encoder/passlog overhead before pass one."""
    try:
        size_mb = float(max_file_size_mb)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Factory long fit received invalid size limit") from exc
    if not math.isfinite(size_mb) or size_mb <= 0:
        raise RuntimeError("Factory long fit requires a positive file-size limit")
    target_bytes = size_mb * 1024.0 * 1024.0
    return int(math.ceil(target_bytes * 1.10 + _FACTORY_LONG_FIT_DISK_RESERVE_BYTES))


def ensure_factory_long_fit_space(output_dir: Path, max_file_size_mb: float) -> None:
    """Fail before two-pass encoding when the temporary target cannot fit."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    required = factory_long_fit_required_free_bytes(max_file_size_mb)
    free = int(shutil.disk_usage(output_dir).free)
    if free < required:
        raise RuntimeError(
            "SHORTS FACTORY: недостаточно места для двухпроходного long-fit: "
            f"нужно около {required / (1024**3):.1f} ГБ, "
            f"свободно {free / (1024**3):.1f} ГБ"
        )
    logger.info(
        "Factory long-fit disk guard: path=%s required=%.2fGB free=%.2fGB",
        output_dir,
        required / (1024**3),
        free / (1024**3),
    )


def _fit_artifacts(output_path: Path) -> tuple[Path, Path]:
    fitted = output_path.with_name(f"{output_path.stem}_factory_fit.mp4")
    passlog = output_path.with_name(f"{output_path.stem}_factory_x264")
    return fitted, passlog


def _cleanup_fit_artifacts(fitted: Path, passlog: Path) -> None:
    paths = [fitted]
    paths.extend(passlog.parent.glob(passlog.name + "*"))
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _two_pass_commands(
    ffmpeg: str,
    source_path: Path,
    fitted_path: Path,
    passlog_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    video_kbps: int,
) -> tuple[list[str], list[str]]:
    common = [
        ffmpeg,
        "-i",
        str(source_path),
        "-ss",
        f"{start_seconds:.3f}",
        "-t",
        f"{duration_seconds:.3f}",
        "-map",
        "0:v:0",
        "-sn",
        "-dn",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-b:v",
        f"{video_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-passlogfile",
        str(passlog_path),
    ]
    first = [
        *common,
        "-pass",
        "1",
        "-an",
        "-f",
        "null",
        "-y",
        os.devnull,
    ]
    second = [
        *common,
        "-pass",
        "2",
        "-map",
        "0:a:0",
        "-c:a",
        "aac",
        "-b:a",
        f"{_FACTORY_LONG_AUDIO_KBPS}k",
        "-movflags",
        "+faststart",
        "-y",
        str(fitted_path),
    ]
    return first, second


async def fit_factory_long_clip_to_limit(
    source_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    max_file_size_mb: float,
    ffmpeg: str,
) -> bool:
    """Two-pass encode the exact interval at the highest bitrate that fits."""
    duration = float(end_seconds) - float(start_seconds)
    if duration <= 0 or duration > _FACTORY_LONG_MAX_SEC:
        raise RuntimeError(
            f"Factory long fit received invalid duration {duration:.3f}s"
        )

    ensure_factory_long_fit_space(output_path.parent, max_file_size_mb)
    limit_bytes = int(float(max_file_size_mb) * 1024 * 1024)
    target_kbps = factory_long_target_video_kbps(
        max_file_size_mb,
        duration,
    )
    fitted, passlog = _fit_artifacts(output_path)
    _cleanup_fit_artifacts(fitted, passlog)

    try:
        for attempt in range(2):
            first, second = _two_pass_commands(
                ffmpeg,
                source_path,
                fitted,
                passlog,
                start_seconds=float(start_seconds),
                duration_seconds=duration,
                video_kbps=target_kbps,
            )
            first_process = await run_cancellable_process(
                first,
                timeout=_FACTORY_LONG_FIT_TIMEOUT_SEC,
                text=True,
            )
            if first_process.returncode != 0:
                logger.warning(
                    "Factory long fit pass 1 failed: %s",
                    str(first_process.stderr or "")[-800:],
                )
                return False

            second_process = await run_cancellable_process(
                second,
                timeout=_FACTORY_LONG_FIT_TIMEOUT_SEC,
                text=True,
            )
            if second_process.returncode != 0 or not fitted.is_file():
                logger.warning(
                    "Factory long fit pass 2 failed: %s",
                    str(second_process.stderr or "")[-800:],
                )
                return False

            probe = await probe_media_async(fitted)
            if not media_probe_is_deliverable(probe):
                logger.warning("Factory long fitted file failed media probe")
                return False
            assert probe is not None
            if probe.duration > _FACTORY_LONG_MAX_SEC + _FACTORY_LONG_DURATION_EPSILON_SEC:
                logger.warning(
                    "Factory long fitted duration exceeds public cap: %.3fs",
                    probe.duration,
                )
                return False
            if abs(probe.duration - duration) > _FACTORY_LONG_EXPECTED_TOLERANCE_SEC:
                logger.warning(
                    "Factory long fitted duration mismatch: expected=%.3fs actual=%.3fs",
                    duration,
                    probe.duration,
                )
                return False
            fitted_size = fitted.stat().st_size
            if fitted_size <= limit_bytes:
                output_path.unlink(missing_ok=True)
                fitted.replace(output_path)
                logger.info(
                    "Factory long fitted to Telegram limit: attempt=%d "
                    "bitrate=%dk duration=%.3fs size=%.1fMB limit=%.1fMB",
                    attempt + 1,
                    target_kbps,
                    probe.duration,
                    output_path.stat().st_size / (1024 * 1024),
                    max_file_size_mb,
                )
                return True

            ratio = max(0.50, min(0.98, limit_bytes / fitted_size * 0.98))
            target_kbps = int(target_kbps * ratio)
            if target_kbps < _FACTORY_LONG_MIN_VIDEO_KBPS:
                logger.warning(
                    "Factory long second fit would fall below %dkbps",
                    _FACTORY_LONG_MIN_VIDEO_KBPS,
                )
                return False
            fitted.unlink(missing_ok=True)
            for artifact in passlog.parent.glob(passlog.name + "*"):
                artifact.unlink(missing_ok=True)
        return False
    finally:
        _cleanup_fit_artifacts(fitted, passlog)


def install_factory_long_fit_policy() -> bool:
    """Wrap long rendering only inside the task-local Factory context."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.clips as clips_module
    import services.render_clips_montage as render_module
    import services.shorts_factory_runtime as runtime_module

    original_render_clip = render_module.render_clip

    @functools.wraps(original_render_clip)
    async def factory_size_safe_render_clip(
        source_video_path: Path,
        output_path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> bool:
        rendered = await original_render_clip(
            source_video_path,
            output_path,
            start_seconds,
            end_seconds,
        )
        if not rendered or runtime_module._FACTORY_SETTINGS.get() is None:
            return rendered

        max_size_mb = float(get_max_file_size_mb())
        limit_bytes = int(max_size_mb * 1024 * 1024)
        if output_path.is_file() and output_path.stat().st_size <= limit_bytes:
            return True

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.error("Factory long fit requires ffmpeg")
            return False
        logger.warning(
            "Factory long clip exceeds Telegram limit; starting two-pass fit: "
            "file=%s size=%.1fMB limit=%.1fMB",
            output_path.name,
            output_path.stat().st_size / (1024 * 1024),
            max_size_mb,
        )
        return await fit_factory_long_clip_to_limit(
            source_video_path,
            output_path,
            start_seconds,
            end_seconds,
            max_file_size_mb=max_size_mb,
            ffmpeg=ffmpeg,
        )

    render_module.render_clip = factory_size_safe_render_clip
    clips_module.render_clip = factory_size_safe_render_clip

    _INSTALLED = True
    logger.info(
        "Shorts Factory long-fit policy installed: original CRF render first, "
        "then exact-interval two-pass libx264 slow only when the hard Telegram "
        "size limit is exceeded"
    )
    return True


__all__ = [
    "ensure_factory_long_fit_space",
    "factory_long_fit_required_free_bytes",
    "factory_long_target_video_kbps",
    "fit_factory_long_clip_to_limit",
    "install_factory_long_fit_policy",
]
