#!/usr/bin/env python3
"""Final Factory render-safety refinements over the video-quality policy."""
from __future__ import annotations

import logging
import math
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

PUBLIC_LONG_MAX_SEC = 900.0
_UNITY_SPEED_ABS_TOL = 1e-9
_LONG_INTERVAL_ABS_TOL = 1e-6
_LONG_PUBLIC_END: ContextVar[float | None] = ContextVar(
    "factory_long_public_end",
    default=None,
)
_INSTALLED = False

CopyTransform = Callable[[Path, Path], Awaitable[bool]]
FallbackTransform = Callable[..., Awaitable[bool]]


def strict_unity_speed_video_copy(
    *,
    normalize_audio: bool,
    speed: float,
) -> bool:
    """Allow packet-copy only when requested video speed is effectively 1.0."""
    if not normalize_audio:
        return False
    try:
        value = float(speed)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(
        math.isfinite(value)
        and math.isclose(value, 1.0, rel_tol=0.0, abs_tol=_UNITY_SPEED_ABS_TOL)
    )


def validated_factory_long_interval(
    start_seconds: Any,
    end_seconds: Any,
) -> tuple[float, float] | None:
    """Return a finite public LONG interval or fail closed before FFmpeg."""
    try:
        start = float(start_seconds)
        end = float(end_seconds)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    duration = end - start
    if (
        start < 0.0
        or duration <= 0.0
        or duration > PUBLIC_LONG_MAX_SEC + _LONG_INTERVAL_ABS_TOL
    ):
        return None
    return start, min(end, start + PUBLIC_LONG_MAX_SEC)


def clamp_factory_long_silence_end(adjusted_end: Any) -> Any:
    """Clamp a silence-snap result to the request-local public LONG end."""
    public_end = _LONG_PUBLIC_END.get()
    if public_end is None:
        return adjusted_end
    try:
        value = float(adjusted_end)
    except (TypeError, ValueError, OverflowError):
        return adjusted_end
    if not math.isfinite(value):
        return adjusted_end
    return min(value, public_end)


async def normalize_factory_short_with_fallback(
    input_path: Path,
    output_path: Path,
    *,
    copy_transform: CopyTransform,
    fallback_transform: FallbackTransform,
) -> bool:
    """Try video packet-copy, then one canonical transform if muxing fails."""
    if await copy_transform(input_path, output_path):
        return True
    logger.warning(
        "Factory normalize-only packet-copy failed; retrying once with the "
        "canonical Short transform"
    )
    return bool(
        await fallback_transform(
            input_path,
            output_path,
            normalize_audio=True,
            speed=1.0,
        )
    )


def install_factory_render_polish() -> bool:
    """Tighten installed Factory render seams before disk/fit capture."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import services.shorts_factory_video_quality as quality
    import services.shorts_video_impl as short_video

    original_copy_transform = quality.normalize_factory_short_audio_copy_video
    original_long_render = quality.render_factory_long_h264
    original_find_silence_end = quality._find_silence_end
    canonical_short_transform = short_video.postprocess_short

    async def resilient_copy_transform(input_path: Path, output_path: Path) -> bool:
        return await normalize_factory_short_with_fallback(
            Path(input_path),
            Path(output_path),
            copy_transform=original_copy_transform,
            fallback_transform=canonical_short_transform,
        )

    async def capped_find_silence_end(*args, **kwargs):
        adjusted = await original_find_silence_end(*args, **kwargs)
        return clamp_factory_long_silence_end(adjusted)

    async def capped_long_render(
        source_video_path: Path,
        output_path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> bool:
        interval = validated_factory_long_interval(start_seconds, end_seconds)
        if interval is None:
            logger.warning(
                "Factory LONG rejected outside finite public duration contract: %r..%r",
                start_seconds,
                end_seconds,
            )
            return False
        start, end = interval
        token = _LONG_PUBLIC_END.set(start + PUBLIC_LONG_MAX_SEC)
        try:
            return await original_long_render(
                Path(source_video_path), Path(output_path), start, end
            )
        finally:
            _LONG_PUBLIC_END.reset(token)

    quality.factory_normalize_only_uses_video_copy = strict_unity_speed_video_copy
    quality.normalize_factory_short_audio_copy_video = resilient_copy_transform
    quality._find_silence_end = capped_find_silence_end
    quality.render_factory_long_h264 = capped_long_render

    _INSTALLED = True
    logger.info(
        "Factory render polish installed: exact-unity packet-copy, canonical "
        "normalize fallback, LONG silence snap <=900s"
    )
    return True


__all__ = [
    "PUBLIC_LONG_MAX_SEC",
    "clamp_factory_long_silence_end",
    "install_factory_render_polish",
    "normalize_factory_short_with_fallback",
    "strict_unity_speed_video_copy",
    "validated_factory_long_interval",
]
