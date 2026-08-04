#!/usr/bin/env python3
"""Fail-closed quality floors and exact audited boundaries for Shorts Factory."""
from __future__ import annotations

import functools
import logging
import math
import re
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MIN_FACTORY_GEMINI_VERSION = (3, 1)
REQUIRED_FACTORY_WHISPER_MODEL = "large-v3"
MIN_FACTORY_FREE_GB = 2.0
MIN_FACTORY_LIVEDUB_TIMEOUT_SEC = 1800

_MODEL_RE = re.compile(
    r"^gemini-(?P<version>\d+(?:\.\d+){0,2})-pro(?:[-_.].*)?$",
    re.IGNORECASE,
)
_INSTALLED = False


def require_factory_model_floor(model: Any) -> str:
    """Accept canonical Gemini Pro names at or above the 3.1 floor."""
    value = str(model or "").strip()
    match = _MODEL_RE.fullmatch(value)
    if not match:
        raise RuntimeError(
            "SHORTS FACTORY MAX requires a canonical Gemini Pro model at or "
            f"above 3.1; received {value!r}"
        )

    version_parts = match.group("version").split(".")
    numbers = [int(part) for part in version_parts]
    version = tuple((numbers + [0, 0])[:2])
    if version < MIN_FACTORY_GEMINI_VERSION:
        raise RuntimeError(
            "SHORTS FACTORY MAX refuses an older Pro model: "
            f"{value!r} < Gemini {MIN_FACTORY_GEMINI_VERSION[0]}."
            f"{MIN_FACTORY_GEMINI_VERSION[1]} Pro"
        )
    return value


def precise_factory_seconds(value: Any) -> float:
    """Preserve millisecond cut precision from Gemini structured output."""
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            seconds = float(text)
        except ValueError:
            parts = text.split(":")
            try:
                values = [float(part) for part in parts]
            except ValueError:
                return 0.0
            if len(values) == 2:
                seconds = values[0] * 60.0 + values[1]
            elif len(values) == 3:
                seconds = values[0] * 3600.0 + values[1] * 60.0 + values[2]
            else:
                return 0.0

    if not math.isfinite(seconds):
        return 0.0
    return round(max(0.0, seconds), 3)


def enforce_quality_floor(value: Any, default: float, maximum: float) -> float:
    """Environment overrides may tighten a Factory floor, never lower it."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(float(default), min(number, float(maximum)))


def hardened_factory_subtitle_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Reject compressed/smaller Whisper routes and force all precision flags."""
    result = dict(profile or {})
    requested = str(result.get("model_name") or "").strip()
    if requested.casefold() != REQUIRED_FACTORY_WHISPER_MODEL:
        raise RuntimeError(
            "SHORTS FACTORY MAX requires faster-whisper large-v3 exactly; "
            f"{requested!r} is a quality downgrade"
        )
    result.update(
        {
            "model_name": REQUIRED_FACTORY_WHISPER_MODEL,
            "karaoke": True,
            "word_timestamps": True,
            "light": False,
            "gemini_hints": True,
        }
    )
    return result


async def resolve_factory_silence_end(
    original: Callable[..., Awaitable[float]],
    source_path: Any,
    target_end: float,
    *args: Any,
    factory_active: bool,
    **kwargs: Any,
) -> float:
    """Factory trusts the audited end; ordinary modes keep silence snapping."""
    if factory_active:
        return float(target_end)
    return float(await original(source_path, target_end, *args, **kwargs))


def install_factory_no_downgrade_policy() -> bool:
    """Patch Factory-only quality controls before the public router is installed."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import services.render_clips_montage as render_clips_module
    import services.shorts_factory_candidates as candidates_module
    import services.shorts_factory_execution_guard as execution_module
    import services.shorts_factory_quality_gate as quality_gate_module
    import services.shorts_factory_runtime as runtime_module
    import services.shorts_factory_timing as timing_module
    import services.shorts_video_impl as shorts_video_module

    original_model_selector = candidates_module.shorts_factory_model
    original_score_threshold = quality_gate_module._score_threshold
    original_subtitle_profile = runtime_module.factory_subtitle_profile
    original_timing_float = timing_module._env_float
    original_min_free_gb = execution_module._min_free_gb
    original_env_int = execution_module._env_int
    original_short_silence_end = shorts_video_module._find_silence_end
    original_clip_silence_end = render_clips_module._find_silence_end

    @functools.wraps(original_model_selector)
    def strict_model_selector() -> str:
        return require_factory_model_floor(original_model_selector())

    @functools.wraps(original_score_threshold)
    def strict_score_threshold(name: str, default: float) -> float:
        selected = original_score_threshold(name, default)
        hardened = enforce_quality_floor(selected, default, 100.0)
        if hardened != selected:
            logger.warning(
                "Ignoring Factory score downgrade %s=%s; minimum remains %s",
                name,
                selected,
                hardened,
            )
        return hardened

    @functools.wraps(original_subtitle_profile)
    def strict_subtitle_profile() -> dict[str, Any]:
        return hardened_factory_subtitle_profile(original_subtitle_profile())

    @functools.wraps(original_timing_float)
    def strict_timing_float(name: str, default: float) -> float:
        selected = original_timing_float(name, default)
        hardened = enforce_quality_floor(selected, default, 30.0)
        if hardened != selected:
            logger.warning(
                "Ignoring Factory timing downgrade %s=%s; minimum remains %s",
                name,
                selected,
                hardened,
            )
        return hardened

    @functools.wraps(original_min_free_gb)
    def strict_min_free_gb() -> float:
        return enforce_quality_floor(
            original_min_free_gb(),
            MIN_FACTORY_FREE_GB,
            100.0,
        )

    @functools.wraps(original_env_int)
    def strict_env_int(
        name: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        if name == "SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC":
            default = max(default, MIN_FACTORY_LIVEDUB_TIMEOUT_SEC)
            minimum = max(minimum, MIN_FACTORY_LIVEDUB_TIMEOUT_SEC)
        return original_env_int(name, default, minimum, maximum)

    @functools.wraps(original_short_silence_end)
    async def factory_short_silence_end(
        source_path: Any,
        target_end: float,
        *args: Any,
        **kwargs: Any,
    ) -> float:
        return await resolve_factory_silence_end(
            original_short_silence_end,
            source_path,
            target_end,
            *args,
            factory_active=runtime_module._FACTORY_SETTINGS.get() is not None,
            **kwargs,
        )

    @functools.wraps(original_clip_silence_end)
    async def factory_clip_silence_end(
        source_path: Any,
        target_end: float,
        *args: Any,
        **kwargs: Any,
    ) -> float:
        return await resolve_factory_silence_end(
            original_clip_silence_end,
            source_path,
            target_end,
            *args,
            factory_active=runtime_module._FACTORY_SETTINGS.get() is not None,
            **kwargs,
        )

    # Validate all fail-closed startup knobs before mutating any imported module.
    # A bad .env therefore leaves no half-installed wrappers behind.
    validated_model = require_factory_model_floor(original_model_selector())
    validated_profile = hardened_factory_subtitle_profile(
        original_subtitle_profile()
    )

    candidates_module.shorts_factory_model = strict_model_selector
    candidates_module._seconds = precise_factory_seconds
    quality_gate_module._score_threshold = strict_score_threshold
    runtime_module.factory_subtitle_profile = strict_subtitle_profile
    timing_module._env_float = strict_timing_float
    execution_module._min_free_gb = strict_min_free_gb
    execution_module._env_int = strict_env_int
    shorts_video_module._find_silence_end = factory_short_silence_end
    render_clips_module._find_silence_end = factory_clip_silence_end

    _INSTALLED = True
    logger.info(
        "Shorts Factory no-downgrade policy installed: model=%s, "
        "Whisper=%s, non-lowerable quality/timing floors, millisecond "
        "timestamps and exact audited render ends",
        validated_model,
        validated_profile["model_name"],
    )
    return True


__all__ = [
    "MIN_FACTORY_FREE_GB",
    "MIN_FACTORY_GEMINI_VERSION",
    "MIN_FACTORY_LIVEDUB_TIMEOUT_SEC",
    "REQUIRED_FACTORY_WHISPER_MODEL",
    "enforce_quality_floor",
    "hardened_factory_subtitle_profile",
    "install_factory_no_downgrade_policy",
    "precise_factory_seconds",
    "require_factory_model_floor",
    "resolve_factory_silence_end",
]
