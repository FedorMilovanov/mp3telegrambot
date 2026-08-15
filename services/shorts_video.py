#!/usr/bin/env python3
"""Transactional public boundary for Shorts video processing.

The established implementation remains in :mod:`services.shorts_video_impl`,
but this module is now a normal Python facade: it never rewrites
``sys.modules`` and never mutates the implementation module after import.
Transactional output ownership lives here and callers keep importing the
historic ``services.shorts_video`` surface normally.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from services import shorts_video_impl as _impl


TRANSACTIONAL_SHORTS_OUTPUT_POLICY = "transactional-shorts-outputs-v2"

_LEGACY_RENDER_SHORT_CLIP = _impl._unowned_render_short_clip
_LEGACY_SHORT_TRANSFORM = _impl._unowned_short_transform
_LEGACY_CREATE_SHORT_TITLE_POSTER = _impl.create_short_title_poster
_LEGACY_CREATE_SHORT_SNAPSHOT = _impl.create_short_snapshot


def __getattr__(name: str) -> Any:
    """Delegate unchanged public helpers to the implementation module."""
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
    """Remove stale or partial outputs without deleting protected inputs."""
    for path in paths:
        if path is None:
            continue
        if any(_same_short_path(path, keep) for keep in protected):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _impl.logger.warning("Shorts cleanup failed for %s: %s", path, exc)


def _normalize_only_can_copy_video(*, normalize_audio: bool, speed: float) -> bool:
    """True when the transform changes audio only and video packets may be copied."""
    try:
        value = float(speed)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(normalize_audio and abs(value - 1.0) <= 1e-9)


async def _normalize_audio_copy_video(input_path: Path, output_path: Path) -> bool:
    """Run loudnorm without creating another lossy video generation."""
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


async def _owned_render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    _unlink_short_paths(output_path, protected=(source_video_path,))
    try:
        result = await _LEGACY_RENDER_SHORT_CLIP(
            source_video_path,
            output_path,
            start_seconds,
            end_seconds,
            visual_mode=visual_mode,
        )
    except asyncio.CancelledError:
        _unlink_short_paths(output_path, protected=(source_video_path,))
        raise
    except Exception as exc:
        _impl.logger.warning(
            "render_short_clip transaction error: %s: %s",
            type(exc).__name__,
            exc,
        )
        _unlink_short_paths(output_path, protected=(source_video_path,))
        return False

    if not result or not output_path.exists() or output_path.stat().st_size == 0:
        _unlink_short_paths(output_path, protected=(source_video_path,))
        return False
    return True


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
    start_seconds: int,
    end_seconds: int,
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    return await _impl.await_owned_coroutine(
        _owned_render_short_clip(
            source_video_path,
            output_path,
            start_seconds,
            end_seconds,
            visual_mode=visual_mode,
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
