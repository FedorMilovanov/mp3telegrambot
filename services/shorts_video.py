#!/usr/bin/env python3
"""Owned public boundary for Shorts video processing.

The legacy implementation module still provides unchanged helpers while public
long-running operations and transactional outputs live here. This module never
rewrites ``sys.modules`` or mutates imported modules at runtime.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

from services import shorts_video_impl as _impl


TRANSACTIONAL_SHORTS_OUTPUT_POLICY = "transactional-shorts-outputs-v3"

_LEGACY_DOWNLOAD_VIDEO = _impl._unowned_download_video_for_shorts
_LEGACY_TRANSCRIBE_SHORT_CLIP = _impl._unowned_transcribe_short_clip
_LEGACY_SHORT_TRANSFORM = _impl._unowned_short_transform
_LEGACY_CREATE_SHORT_TITLE_POSTER = _impl.create_short_title_poster
_LEGACY_CREATE_SHORT_SNAPSHOT = _impl.create_short_snapshot


def __getattr__(name: str) -> Any:
    """Delegate unchanged helpers only; long-running public operations are explicit."""
    try:
        return getattr(_impl, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))


def _same_short_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return left.absolute() == right.absolute()


def _unlink_short_paths(
    *paths: Path | None,
    protected: tuple[Path, ...] = (),
) -> None:
    for path in paths:
        if path is None:
            continue
        if any(_same_short_path(path, keep) for keep in protected):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _impl.logger.warning("Shorts cleanup failed for %s: %s", path, exc)


def _finite_ceiling(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


async def download_video_for_shorts(
    url: str,
    media_id: str,
    workdir: Path | None = None,
):
    """Own the full download awaitable so repeated outer cancellation cannot orphan it."""
    return await _impl.await_owned_coroutine(
        _LEGACY_DOWNLOAD_VIDEO(url, media_id, workdir=workdir)
    )


async def transcribe_short_clip(
    video_path: Path,
    ai_data: dict | None = None,
) -> list[dict]:
    """Own extraction + Whisper as one public operation, not only the worker thread."""
    return await _impl.await_owned_coroutine(
        _LEGACY_TRANSCRIBE_SHORT_CLIP(video_path, ai_data=ai_data)
    )


async def _owned_render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    visual_mode: str = "full_frame_vertical",
    silence_snap_max_end: float | None = None,
    snap_to_silence: bool = True,
) -> bool:
    """Render one vertical clip with explicit silence-snap policy and ceiling."""
    source_video_path = Path(source_video_path)
    output_path = Path(output_path)
    hard_ceiling = _finite_ceiling(silence_snap_max_end)
    try:
        start_seconds = float(start_seconds)
        end_seconds = float(end_seconds)
    except (TypeError, ValueError, OverflowError):
        _impl.logger.warning("render_short_clip: invalid numeric boundaries")
        return False

    if hard_ceiling is not None and hard_ceiling + 1e-9 < end_seconds:
        _impl.logger.warning(
            "render_short_clip: requested end %.3fs exceeds hard snap ceiling %.3fs",
            end_seconds,
            hard_ceiling,
        )
        return False

    _unlink_short_paths(output_path, protected=(source_video_path,))
    try:
        ffmpeg = _impl.shutil.which("ffmpeg")
        if not ffmpeg:
            _impl.logger.warning("render_short_clip: ffmpeg not found")
            return False
        if not source_video_path.exists():
            _impl.logger.warning(
                "render_short_clip: source file not found: %s", source_video_path
            )
            return False
        if end_seconds <= start_seconds:
            _impl.logger.warning(
                "render_short_clip: invalid range %.3f..%.3f",
                start_seconds,
                end_seconds,
            )
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if snap_to_silence:
            adjusted_end = await _impl._find_silence_end(
                source_video_path,
                float(end_seconds),
            )
            try:
                adjusted_end = float(adjusted_end)
            except (TypeError, ValueError, OverflowError):
                adjusted_end = end_seconds
            if not math.isfinite(adjusted_end):
                adjusted_end = end_seconds

            min_end = start_seconds + max(10.0, (end_seconds - start_seconds) * 0.5)
            max_end = end_seconds + 10.0
            if hard_ceiling is not None:
                max_end = min(max_end, hard_ceiling)
                adjusted_end = min(adjusted_end, hard_ceiling)

            if min_end < adjusted_end <= max_end and abs(adjusted_end - end_seconds) > 0.1:
                snapped_end = float(round(adjusted_end))
                if hard_ceiling is not None:
                    snapped_end = min(snapped_end, hard_ceiling)
                if snapped_end > start_seconds:
                    _impl.logger.info(
                        "Short end adjusted: %.3fs -> %.3fs (silence snap, ceiling=%s)",
                        end_seconds,
                        snapped_end,
                        f"{hard_ceiling:.3f}" if hard_ceiling is not None else "none",
                    )
                    end_seconds = snapped_end

        if hard_ceiling is not None:
            end_seconds = min(end_seconds, hard_ceiling)
        clip_duration = end_seconds - start_seconds
        if clip_duration <= 0:
            _impl.logger.warning(
                "render_short_clip: clip_duration <= 0 after silence adjustment"
            )
            return False

        black_bars = await _impl._detect_black_bars(
            source_video_path,
            float(start_seconds),
        )
        if visual_mode == "crop_zoom" and await _impl._is_static_video(
            source_video_path,
            float(start_seconds),
        ):
            visual_mode = "full_frame_vertical"
            black_bars = ""
            _impl.logger.info(
                "Short: static frame detected; using full_frame_vertical without cropdetect"
            )

        use_filter_complex = False
        if visual_mode == "crop_zoom":
            bc = f"{black_bars}," if black_bars else ""
            vf = (
                f"{bc}crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                "scale=720:1280"
            )
            mode_label = "crop_zoom(medium)"
        else:
            if black_bars:
                vf = (
                    f"[0:v]{black_bars}[clean];"
                    "[clean]split=2[bg][fg];"
                    "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
                    "crop=720:1280,gblur=sigma=20,setsar=1[blurred];"
                    "[fg]scale=720:1280:force_original_aspect_ratio=decrease,setsar=1[small];"
                    "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
                )
            else:
                vf = (
                    "[0:v]split=2[bg][fg];"
                    "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
                    "crop=720:1280,gblur=sigma=20,setsar=1[blurred];"
                    "[fg]scale=720:1280:force_original_aspect_ratio=decrease,setsar=1[small];"
                    "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
                )
            use_filter_complex = True
            mode_label = "full_frame_blur"

        encoder, quality, preset = await _impl.await_owned_coroutine(
            asyncio.to_thread(_impl._get_video_encoder)
        )
        vf_args = (
            ["-filter_complex", vf, "-map", "[out]", "-map", "0:a?"]
            if use_filter_complex
            else ["-vf", vf]
        )
        command = [
            ffmpeg,
            "-ss",
            str(start_seconds),
            "-i",
            str(source_video_path),
            "-t",
            str(clip_duration),
            *vf_args,
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

        from core.resource_scheduler import scheduler as resource_scheduler

        async with resource_scheduler.gpu_render:
            process = await _impl.run_cancellable_process(
                command,
                timeout=600,
                text=True,
            )
        if process.returncode != 0:
            _impl.logger.warning(
                "render_short_clip ffmpeg error (%s): %s",
                mode_label,
                (process.stderr or "")[-1000:],
            )
            return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            _impl.logger.warning("render_short_clip: output file missing or empty")
            return False
        if output_path.stat().st_size < 10_240:
            _impl.logger.warning(
                "render_short_clip: suspiciously small output (%d bytes): %s",
                output_path.stat().st_size,
                (process.stderr or "")[-1000:],
            )
            output_path.unlink(missing_ok=True)
            return False

        _impl.logger.info(
            "Short rendered [%s] 9:16: %s (%.3fs..%.3fs, %.3fs, %.1fMB)",
            mode_label,
            output_path.name,
            start_seconds,
            end_seconds,
            clip_duration,
            output_path.stat().st_size / (1024 * 1024),
        )
        return True
    except asyncio.CancelledError:
        _unlink_short_paths(output_path, protected=(source_video_path,))
        raise
    except _impl.subprocess.TimeoutExpired:
        _impl.logger.warning("render_short_clip: ffmpeg timeout")
    except Exception as exc:
        _impl.logger.warning(
            "render_short_clip transaction error: %s: %s",
            type(exc).__name__,
            exc,
        )

    _unlink_short_paths(output_path, protected=(source_video_path,))
    return False


def _normalize_only_can_copy_video(*, normalize_audio: bool, speed: float) -> bool:
    try:
        value = float(speed)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(normalize_audio and abs(value - 1.0) <= 1e-9)


async def _normalize_audio_copy_video(input_path: Path, output_path: Path) -> bool:
    ffmpeg = _impl.shutil.which("ffmpeg")
    if not ffmpeg or not input_path.exists():
        return False
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
    process = await _impl.run_cancellable_process(command, timeout=600, text=True)
    if process.returncode != 0:
        _impl.logger.warning(
            "normalize-only video-copy error: %s",
            (process.stderr or "")[-800:],
        )
        return False
    return output_path.exists() and output_path.stat().st_size > 0


async def _owned_short_transform(
    input_path: Path,
    output_path: Path,
    *,
    normalize_audio: bool = True,
    speed: float = 1.0,
) -> bool:
    same_path = _same_short_path(input_path, output_path)
    try:
        speed_value = float(speed)
    except (TypeError, ValueError, OverflowError):
        _impl.logger.warning("Short transform: invalid speed=%r", speed)
        return False
    no_op = not normalize_audio and abs(speed_value - 1.0) <= 0.01
    if same_path:
        if no_op:
            return input_path.exists() and input_path.stat().st_size > 0
        _impl.logger.warning(
            "postprocess_short: input_path and output_path must differ for transforms"
        )
        return False

    _unlink_short_paths(output_path, protected=(input_path,))
    try:
        if _normalize_only_can_copy_video(
            normalize_audio=normalize_audio,
            speed=speed_value,
        ):
            result = await _normalize_audio_copy_video(input_path, output_path)
            if result:
                _impl.logger.info(
                    "Short transform: normalize-only uses -c:v copy; no extra video generation"
                )
        else:
            result = await _LEGACY_SHORT_TRANSFORM(
                input_path,
                output_path,
                normalize_audio=normalize_audio,
                speed=speed_value,
            )
    except asyncio.CancelledError:
        _unlink_short_paths(output_path, protected=(input_path,))
        raise
    except Exception as exc:
        _impl.logger.warning(
            "postprocess_short transaction error: %s: %s",
            type(exc).__name__,
            exc,
        )
        _unlink_short_paths(output_path, protected=(input_path,))
        return False

    if not result or not output_path.exists() or output_path.stat().st_size == 0:
        _unlink_short_paths(output_path, protected=(input_path,))
        return False
    return True


async def _owned_optional_output(
    producer,
    input_path: Path,
    output_path: Path,
    *args,
) -> bool:
    _unlink_short_paths(output_path, protected=(input_path,))
    try:
        result = await producer(input_path, output_path, *args)
    except asyncio.CancelledError:
        _unlink_short_paths(output_path, protected=(input_path,))
        raise
    except Exception as exc:
        _impl.logger.warning(
            "%s transaction error: %s: %s",
            getattr(producer, "__name__", "short-output"),
            type(exc).__name__,
            exc,
        )
        _unlink_short_paths(output_path, protected=(input_path,))
        return False
    if not result or not output_path.exists() or output_path.stat().st_size == 0:
        _unlink_short_paths(output_path, protected=(input_path,))
        return False
    return True


async def render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    visual_mode: str = "full_frame_vertical",
    silence_snap_max_end: float | None = None,
    snap_to_silence: bool = True,
) -> bool:
    """Public renderer with explicit silence-snap ownership."""
    return await _impl.await_owned_coroutine(
        _owned_render_short_clip(
            source_video_path,
            output_path,
            start_seconds,
            end_seconds,
            visual_mode=visual_mode,
            silence_snap_max_end=silence_snap_max_end,
            snap_to_silence=snap_to_silence,
        )
    )


async def postprocess_short(
    input_path: Path,
    output_path: Path,
    *,
    normalize_audio: bool = True,
    speed: float = 1.0,
) -> bool:
    return await _impl.await_owned_coroutine(
        _owned_short_transform(
            input_path,
            output_path,
            normalize_audio=normalize_audio,
            speed=speed,
        )
    )


async def create_short_title_poster(
    video_path: Path,
    poster_path: Path,
    title: str,
    clip_duration_seconds: float,
) -> bool:
    return await _impl.await_owned_coroutine(
        _owned_optional_output(
            _LEGACY_CREATE_SHORT_TITLE_POSTER,
            video_path,
            poster_path,
            title,
            clip_duration_seconds,
        )
    )


async def create_short_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    return await _impl.await_owned_coroutine(
        _owned_optional_output(
            _LEGACY_CREATE_SHORT_SNAPSHOT,
            video_path,
            snapshot_path,
            clip_duration_seconds,
        )
    )


async def burn_subtitles_into_short(
    input_path: Path,
    output_path: Path,
    segments: list[dict],
    *,
    karaoke: bool | None = None,
) -> bool:
    """Delegate to the transactional subtitle owner without legacy burn fallback."""
    from services.shorts_subtitle_burn import burn_subtitles_into_short as owned_burn

    return await owned_burn(
        input_path,
        output_path,
        segments,
        karaoke=karaoke,
    )
