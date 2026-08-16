#!/usr/bin/env python3
"""Factory-only video quality/size policy and exact publication source metadata.

Installation happens after the existing maximum-quality source policy and before
the disk guard/long-fit wrappers. That order is deliberate: disk accounting and
oversize fitting continue to own the final Factory source/render functions.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS, _find_silence_end
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async

logger = logging.getLogger(__name__)

FACTORY_VIDEO_FORMAT = (
    "bestvideo[height<=1080]+bestaudio/"
    "best[height<=1080]"
)
_H264_NVENC_AVAILABLE: bool | None = None


def factory_normalize_only_uses_video_copy(
    *,
    normalize_audio: bool,
    speed: float,
) -> bool:
    """True only when the transform can leave every video packet untouched."""
    try:
        normalized_speed = float(speed)
    except (TypeError, ValueError):
        return False
    return bool(normalize_audio and abs(normalized_speed - 1.0) <= 0.01)


async def _ffprobe_evidence(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.is_file():
        return {}
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,size,bit_rate:"
            "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate"
        ),
        "-of",
        "json",
        str(path),
    ]
    try:
        process = await run_cancellable_process(command, timeout=45, text=True)
        if process.returncode != 0:
            return {}
        data = json.loads(process.stdout or "{}")
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.info("Factory media evidence unavailable for %s: %s", path.name, exc)
        return {}


def _fps_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "0/0":
        return ""
    try:
        if "/" in raw:
            left, right = raw.split("/", 1)
            denominator = float(right)
            if denominator:
                return f"{float(left) / denominator:.3f}".rstrip("0").rstrip(".")
        return f"{float(raw):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError, ZeroDivisionError):
        return raw[:24]


async def log_factory_media_evidence(
    label: str,
    path: Path,
    *,
    video_encode_stages: int | None = None,
) -> None:
    data = await _ffprobe_evidence(path)
    streams = data.get("streams") if isinstance(data, dict) else None
    streams = streams if isinstance(streams, list) else []
    video = next(
        (
            row
            for row in streams
            if isinstance(row, dict) and row.get("codec_type") == "video"
        ),
        {},
    )
    fmt = data.get("format") if isinstance(data, dict) else {}
    fmt = fmt if isinstance(fmt, dict) else {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        bitrate = int(float(fmt.get("bit_rate") or 0.0))
    except (TypeError, ValueError):
        bitrate = 0
    size = path.stat().st_size if path.is_file() else 0
    stages = (
        f" video_encode_stages={video_encode_stages}"
        if video_encode_stages is not None
        else ""
    )
    logger.info(
        "Factory media evidence [%s]: file=%s resolution=%sx%s codec=%s fps=%s "
        "duration=%.3fs size=%.1fMB avg_bitrate=%.2fMbps%s",
        label,
        path.name,
        video.get("width") or "?",
        video.get("height") or "?",
        video.get("codec_name") or "?",
        _fps_text(video.get("avg_frame_rate") or video.get("r_frame_rate")) or "?",
        duration,
        size / (1024 * 1024),
        bitrate / 1_000_000 if bitrate else 0.0,
        stages,
    )


async def download_factory_video_1080(
    url: str,
    media_id: str,
    workdir: Path | None = None,
) -> Path:
    """Download the best verified SDR-friendly Factory master up to 1080p."""
    import services.shorts_factory_source as source

    target_dir = Path(workdir) if workdir is not None else source.DOWNLOAD_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{media_id}_factory_max_source"
    source._remove_paths(target_dir.glob(f"{prefix}.*"))
    output_template = target_dir / f"{prefix}.%(ext)s"
    command = list(YTDLP_BASE_ARGS) + source._factory_quality_sort_reset() + [
        "--format",
        FACTORY_VIDEO_FORMAT,
        "--merge-output-format",
        "mkv",
        "--no-playlist",
        "--output",
        str(output_template),
        url,
    ]
    try:
        process = await run_cancellable_process(
            command,
            timeout=source._FACTORY_MEDIA_TIMEOUT_SEC,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "yt-dlp Factory <=1080p source download failed: "
                + source._stderr_tail(process)
            )
        candidates = sorted(
            (
                path
                for path in target_dir.glob(f"{prefix}.*")
                if path.is_file() and not source._partial_media(path)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            probe = await probe_media_async(path)
            if not media_probe_is_deliverable(probe):
                continue
            assert probe is not None
            if int(getattr(probe, "height", 0) or 0) > 1080:
                continue
            await log_factory_media_evidence(
                "source-master<=1080p",
                path,
                video_encode_stages=0,
            )
            return path
        raise RuntimeError(
            "yt-dlp completed without a verified Factory video+audio master <=1080p"
        )
    except BaseException:
        source._remove_paths(target_dir.glob(f"{prefix}.*"))
        raise


async def normalize_factory_short_audio_copy_video(
    input_path: Path,
    output_path: Path,
) -> bool:
    """Normalize Factory speech audio without creating another video generation."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not input_path.is_file():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-y",
        str(output_path),
    ]
    try:
        process = await run_cancellable_process(command, timeout=600, text=True)
    except asyncio.CancelledError:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.warning("Factory normalize-only transform failed: %s", exc)
        output_path.unlink(missing_ok=True)
        return False
    if process.returncode != 0:
        logger.warning(
            "Factory normalize-only ffmpeg error: %s",
            str(process.stderr or "")[-800:],
        )
        output_path.unlink(missing_ok=True)
        return False
    probe = await probe_media_async(output_path)
    if not media_probe_is_deliverable(probe):
        output_path.unlink(missing_ok=True)
        return False
    logger.info(
        "Factory Short normalize-only: -c:v copy; video encode stages are "
        "crop/scale + subtitle burn (2 total, not 3)"
    )
    return True


def factory_h264_nvenc_quality_args() -> tuple[list[str], list[str]]:
    """Talking-head quality-per-byte profile; higher CQ than the old LONG CQ22."""
    return (
        [
            "-rc",
            "vbr",
            "-cq",
            "25",
            "-b:v",
            "0",
            "-spatial-aq",
            "1",
            "-rc-lookahead",
            "20",
        ],
        ["-preset", "p6", "-tune", "hq"],
    )


def factory_libx264_quality_args() -> tuple[list[str], list[str]]:
    preset = (os.getenv("VIDEO_CPU_PRESET", "") or "medium").strip().lower()
    allowed = {
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    }
    if preset not in allowed:
        preset = "medium"
    return ["-crf", "23"], ["-preset", preset]


def _h264_nvenc_available_sync(ffmpeg: str) -> bool:
    global _H264_NVENC_AVAILABLE
    if _H264_NVENC_AVAILABLE is not None:
        return _H264_NVENC_AVAILABLE
    if os.getenv("VIDEO_FORCE_CPU", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        _H264_NVENC_AVAILABLE = False
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        _H264_NVENC_AVAILABLE = result.returncode == 0 and "h264_nvenc" in combined
    except Exception:
        _H264_NVENC_AVAILABLE = False
    return _H264_NVENC_AVAILABLE


async def _factory_h264_encoder(
    ffmpeg: str,
) -> tuple[str, list[str], list[str]]:
    from services.async_worker import await_owned_coroutine

    has_nvenc = await await_owned_coroutine(
        asyncio.to_thread(_h264_nvenc_available_sync, ffmpeg)
    )
    if has_nvenc:
        quality, preset = factory_h264_nvenc_quality_args()
        return "h264_nvenc", quality, preset
    quality, preset = factory_libx264_quality_args()
    return "libx264", quality, preset


def _long_scale_filter(width: int, height: int) -> str:
    if width <= 1920 and height <= 1080:
        return ""
    return (
        "scale=w='min(iw,1920)':h='min(ih,1080)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _factory_long_command(
    *,
    ffmpeg: str,
    source_video_path: Path,
    output_path: Path,
    start: float,
    duration: float,
    scale_filter: str,
    encoder: str,
    quality: list[str],
    preset: list[str],
) -> list[str]:
    command = [
        ffmpeg,
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source_video_path),
        "-t",
        f"{duration:.3f}",
    ]
    if scale_filter:
        command.extend(["-vf", scale_filter])
    command.extend(
        [
            "-c:v",
            encoder,
            *preset,
            *quality,
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
    )
    return command


async def _run_factory_long_command(
    command: list[str],
    output_path: Path,
) -> tuple[bool, str]:
    from core.resource_scheduler import scheduler as resource_scheduler

    try:
        async with resource_scheduler.gpu_render:
            process = await run_cancellable_process(command, timeout=900, text=True)
    except asyncio.CancelledError:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        return False, str(exc)
    if process.returncode != 0:
        return False, str(process.stderr or "")[-900:]
    return True, ""


async def render_factory_long_h264(
    source_video_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> bool:
    """Render one Factory LONG to H.264, never above 1080p, in one video pass."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not source_video_path.is_file():
        return False
    try:
        start = float(start_seconds)
        end = float(end_seconds)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(start) or not math.isfinite(end) or end <= start or start < 0:
        return False

    adjusted_end = await _find_silence_end(
        source_video_path,
        end,
        search_window=8.0,
    )
    min_end = start + max(10.0, (end - start) * 0.5)
    max_end = end + 12.0
    if min_end < adjusted_end <= max_end and abs(adjusted_end - end) > 0.1:
        end = float(adjusted_end)
    duration = end - start
    if not math.isfinite(duration) or duration <= 0:
        return False

    source_probe = await probe_media_async(source_video_path)
    if not media_probe_is_deliverable(source_probe):
        return False
    assert source_probe is not None

    encoder, quality, preset = await _factory_h264_encoder(ffmpeg)
    scale_filter = _long_scale_filter(
        int(getattr(source_probe, "width", 0) or 0),
        int(getattr(source_probe, "height", 0) or 0),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    command = _factory_long_command(
        ffmpeg=ffmpeg,
        source_video_path=source_video_path,
        output_path=output_path,
        start=start,
        duration=duration,
        scale_filter=scale_filter,
        encoder=encoder,
        quality=quality,
        preset=preset,
    )
    rendered, error = await _run_factory_long_command(command, output_path)

    if not rendered and encoder == "h264_nvenc":
        global _H264_NVENC_AVAILABLE
        _H264_NVENC_AVAILABLE = False
        output_path.unlink(missing_ok=True)
        cpu_quality, cpu_preset = factory_libx264_quality_args()
        logger.warning(
            "Factory LONG h264_nvenc runtime failed; retrying once with libx264: %s",
            error,
        )
        cpu_command = _factory_long_command(
            ffmpeg=ffmpeg,
            source_video_path=source_video_path,
            output_path=output_path,
            start=start,
            duration=duration,
            scale_filter=scale_filter,
            encoder="libx264",
            quality=cpu_quality,
            preset=cpu_preset,
        )
        rendered, error = await _run_factory_long_command(cpu_command, output_path)

    if not rendered:
        logger.warning("Factory LONG H.264 ffmpeg error: %s", error)
        output_path.unlink(missing_ok=True)
        return False

    probe = await probe_media_async(output_path)
    if not media_probe_is_deliverable(probe):
        output_path.unlink(missing_ok=True)
        return False
    assert probe is not None
    if int(getattr(probe, "width", 0) or 0) > 1920:
        output_path.unlink(missing_ok=True)
        return False
    if int(getattr(probe, "height", 0) or 0) > 1080:
        output_path.unlink(missing_ok=True)
        return False
    await log_factory_media_evidence(
        "long-output-h264",
        output_path,
        video_encode_stages=1,
    )
    return True


__all__ = [
    "FACTORY_VIDEO_FORMAT",
    "download_factory_video_1080",
    "factory_h264_nvenc_quality_args",
    "factory_libx264_quality_args",
    "factory_normalize_only_uses_video_copy",
    "log_factory_media_evidence",
    "normalize_factory_short_audio_copy_video",
    "render_factory_long_h264",
]
