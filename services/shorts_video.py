#!/usr/bin/env python3
"""Transactional public boundary for Shorts video processing.

The established implementation lives in :mod:`services.shorts_video_impl`.
This module installs fail-closed output ownership around the four active
render surfaces and then exposes the implementation module under the historic
``services.shorts_video`` import path.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from services import shorts_video_impl as _impl


_LEGACY_RENDER_SHORT_CLIP = _impl._unowned_render_short_clip
_LEGACY_SHORT_TRANSFORM = _impl._unowned_short_transform


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
    return bool(normalize_audio and abs(value - 1.0) <= 0.01)


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


async def _unowned_render_short_clip(
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


async def _unowned_short_transform(
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


async def _unowned_create_short_title_poster(
    video_path: Path,
    poster_path: Path,
    title: str,
    clip_duration_seconds: float,
) -> bool:
    _unlink_short_paths(poster_path, protected=(video_path,))
    if not _impl.HAS_PILLOW:
        return False

    frame_path: Path | None = None
    try:
        import tempfile
        from PIL import Image, ImageDraw, ImageFont

        ffmpeg = _impl.shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return False

        seek_time = max(1.0, clip_duration_seconds * 0.25)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = Path(tmp.name)

        command = [
            ffmpeg,
            "-ss",
            str(seek_time),
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-q:v",
            "2",
            "-y",
            str(frame_path),
        ]
        process = await _impl.run_cancellable_process(command, timeout=60, text=True)
        if (
            process.returncode != 0
            or not frame_path.exists()
            or frame_path.stat().st_size == 0
        ):
            _unlink_short_paths(frame_path, poster_path, protected=(video_path,))
            return False

        def _draw_poster() -> bool:
            try:
                with Image.open(frame_path) as base:
                    image = base.convert("RGBA")
                width, height = image.size

                overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)
                gradient_top = int(height * 0.38)
                steps = 50
                for step in range(steps):
                    alpha = int((step / steps) * 175)
                    y0 = gradient_top + int((height - gradient_top) * step / steps)
                    y1 = gradient_top + int(
                        (height - gradient_top) * (step + 1) / steps
                    )
                    overlay_draw.rectangle(
                        [(0, y0), (width, y1)],
                        fill=(0, 0, 0, alpha),
                    )
                image = Image.alpha_composite(image, overlay)
                draw = ImageDraw.Draw(image)

                font_size = max(56, width // 13)
                font = None
                for font_path in [
                    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
                    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                    "/usr/share/truetype/inter/Inter-SemiBold.ttf",
                    "/usr/local/share/fonts/Inter-SemiBold.ttf",
                    "/usr/share/truetype/montserrat/Montserrat-SemiBold.ttf",
                    "/usr/share/truetype/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/truetype/freefont/FreeSansBold.ttf",
                ]:
                    if Path(font_path).exists():
                        try:
                            font = ImageFont.truetype(font_path, font_size)
                            break
                        except Exception:
                            continue
                if font is None:
                    font = ImageFont.load_default()

                lines = _impl._wrap_poster_title(title)
                line_height = int(font_size * 1.30)
                block_height = len(lines) * line_height
                safe_margin = int(height * 0.13)
                text_top = height - safe_margin - block_height
                shadow_layers = [
                    (3, 3, (0, 0, 0, 100)),
                    (2, 2, (0, 0, 0, 160)),
                    (1, 1, (0, 0, 0, 200)),
                ]

                for index, line in enumerate(lines):
                    y = text_top + index * line_height
                    bbox = draw.textbbox((0, 0), line, font=font)
                    x = (width - (bbox[2] - bbox[0])) // 2
                    for shadow_x, shadow_y, shadow_colour in shadow_layers:
                        draw.text(
                            (x + shadow_x, y + shadow_y),
                            line,
                            font=font,
                            fill=shadow_colour,
                        )
                    for delta_x, delta_y in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        draw.text(
                            (x + delta_x, y + delta_y),
                            line,
                            font=font,
                            fill=(0, 0, 0, 180),
                        )
                    draw.text(
                        (x, y),
                        line,
                        font=font,
                        fill=(255, 255, 255, 255),
                    )

                image.convert("RGB").save(str(poster_path), "JPEG", quality=88)
                return True
            except Exception as exc:
                _impl.logger.warning("_draw_poster error: %s", exc)
                return False

        result = await _impl.await_owned_coroutine(asyncio.to_thread(_draw_poster))
        _unlink_short_paths(frame_path, protected=(video_path,))
        frame_path = None

        if result and poster_path.exists() and poster_path.stat().st_size > 0:
            _impl.logger.info("Title poster: %s", poster_path.name)
            return True
        _unlink_short_paths(poster_path, protected=(video_path,))
        return False
    except asyncio.CancelledError:
        _unlink_short_paths(frame_path, poster_path, protected=(video_path,))
        raise
    except Exception as exc:
        _impl.logger.warning(
            "create_short_title_poster error: %s: %s",
            type(exc).__name__,
            exc,
        )
        _unlink_short_paths(frame_path, poster_path, protected=(video_path,))
        return False


async def _unowned_create_short_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    _unlink_short_paths(snapshot_path, protected=(video_path,))
    try:
        ffmpeg = _impl.shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return False

        seek_time = max(1.0, clip_duration_seconds * 0.30)
        command = [
            ffmpeg,
            "-ss",
            str(seek_time),
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-q:v",
            "2",
            "-y",
            str(snapshot_path),
        ]
        process = await _impl.run_cancellable_process(command, timeout=60, text=True)
        if (
            process.returncode != 0
            or not snapshot_path.exists()
            or snapshot_path.stat().st_size == 0
        ):
            _unlink_short_paths(snapshot_path, protected=(video_path,))
            return False
        _impl.logger.info("Snapshot: %s (t=%.1fs)", snapshot_path.name, seek_time)
        return True
    except asyncio.CancelledError:
        _unlink_short_paths(snapshot_path, protected=(video_path,))
        raise
    except Exception as exc:
        _impl.logger.warning(
            "create_short_snapshot error: %s: %s",
            type(exc).__name__,
            exc,
        )
        _unlink_short_paths(snapshot_path, protected=(video_path,))
        return False


async def render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    return await _impl.await_owned_coroutine(
        _unowned_render_short_clip(
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
        _unowned_short_transform(
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
        _unowned_create_short_title_poster(
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
        _unowned_create_short_snapshot(
            video_path,
            snapshot_path,
            clip_duration_seconds,
        )
    )


_impl._same_short_path = _same_short_path
_impl._unlink_short_paths = _unlink_short_paths
_impl._normalize_only_can_copy_video = _normalize_only_can_copy_video
_impl._normalize_audio_copy_video = _normalize_audio_copy_video
_impl._unowned_render_short_clip = _unowned_render_short_clip
_impl._unowned_short_transform = _unowned_short_transform
_impl._unowned_create_short_title_poster = _unowned_create_short_title_poster
_impl._unowned_create_short_snapshot = _unowned_create_short_snapshot
_impl.render_short_clip = render_short_clip
_impl.postprocess_short = postprocess_short
_impl.create_short_title_poster = create_short_title_poster
_impl.create_short_snapshot = create_short_snapshot
_impl.TRANSACTIONAL_SHORTS_OUTPUT_POLICY = "transactional-shorts-outputs-v1"

sys.modules[__name__] = _impl
