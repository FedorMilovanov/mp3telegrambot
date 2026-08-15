#!/usr/bin/env python3
"""Ordinary Shorts source-window and public-duration safety.

The ordinary Shorts pipeline still uses this policy while its remaining state
is moved into the pipeline owner.  Silence snapping is already explicit: the
calculated absolute ceiling is passed directly to ``render_short_clip`` and no
imported function is rebound.
"""
from __future__ import annotations

import copy
import logging
import math
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from services.media_delivery_probe import DeliveryFileSelection

logger = logging.getLogger(__name__)

PUBLIC_SHORT_MAX_SEC = 180.0
PUBLIC_DURATION_EPSILON_SEC = 0.05
_SPEED_EPSILON = 0.01
_INTERVAL_EPSILON_SEC = 1e-6
_INSTALLED = False

_STATE: ContextVar[dict[str, Any] | None] = ContextVar(
    "ordinary_shorts_duration_safety",
    default=None,
)


def _factory_active() -> bool:
    try:
        import services.shorts_factory_runtime as runtime

        return runtime._FACTORY_SETTINGS.get() is not None
    except Exception:
        return False


def _finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _bounded_env_float(name: str, default: float, maximum: float = 30.0) -> float:
    value = _finite_float(os.getenv(name, ""), default)
    assert value is not None
    return max(0.0, min(float(value), maximum))


def _nonnegative_optional(value: Any) -> float:
    parsed = _finite_float(value, 0.0) or 0.0
    return max(0.0, parsed)


def short_speed_transform_required(speed: Any) -> bool:
    value = _finite_float(speed)
    return bool(value is not None and value > 0 and abs(value - 1.0) > _SPEED_EPSILON)


def public_short_duration_ok(duration: Any) -> bool:
    value = _finite_float(duration)
    return bool(
        value is not None
        and value > 0.0
        and value <= PUBLIC_SHORT_MAX_SEC + PUBLIC_DURATION_EPSILON_SEC
    )


def plan_short_source_window(
    candidate: dict[str, Any],
    *,
    speed: Any,
    boundary_padding: bool,
    source_duration: Any = 0.0,
    pre_roll: float | None = None,
    post_roll: float | None = None,
) -> tuple[float, float, float] | None:
    """Return start, requested end and absolute silence-snap ceiling."""
    start = _finite_float(candidate.get("start_seconds"))
    end = _finite_float(candidate.get("end_seconds"))
    speed_value = _finite_float(speed)
    if (
        start is None
        or end is None
        or speed_value is None
        or start < 0.0
        or end <= start
        or speed_value <= 0.0
    ):
        return None

    source_limit = _finite_float(source_duration, 0.0) or 0.0
    if source_limit > 0.0 and end > source_limit + _INTERVAL_EPSILON_SEC:
        return None

    source_budget = PUBLIC_SHORT_MAX_SEC * speed_value
    semantic_span = end - start
    if semantic_span > source_budget + _INTERVAL_EPSILON_SEC:
        return None

    desired_pre = 0.0
    desired_post = 0.0
    if boundary_padding:
        desired_pre = (
            _bounded_env_float("SHORTS_PREROLL_SECONDS", 1.5)
            if pre_roll is None
            else _nonnegative_optional(pre_roll)
        )
        desired_post = (
            _bounded_env_float("SHORTS_POSTROLL_SECONDS", 2.5)
            if post_roll is None
            else _nonnegative_optional(post_roll)
        )
        desired_pre = min(desired_pre, start)

    spare = max(0.0, source_budget - semantic_span)
    desired_extra = desired_pre + desired_post
    scale = 1.0
    if desired_extra > 0.0 and desired_extra > spare:
        scale = spare / desired_extra

    render_start = max(0.0, start - desired_pre * scale)
    render_end = end + desired_post * scale
    if source_limit > 0.0:
        render_end = min(render_end, source_limit)
    if render_end <= render_start:
        return None

    # Renderer may round a snapped end. Use a whole-second ceiling which cannot
    # round beyond the public source budget or the proved source duration.
    snap_ceiling = math.floor(render_start + source_budget + 1e-9)
    if source_limit > 0.0:
        snap_ceiling = min(snap_ceiling, math.floor(source_limit + 1e-9))
    snap_ceiling = max(render_end, float(snap_ceiling))
    if snap_ceiling - render_start > source_budget + _INTERVAL_EPSILON_SEC:
        snap_ceiling = render_end

    return render_start, render_end, snap_ceiling


def authoritative_short_source_start(
    pipeline_source_start: Any,
    render_window: Any,
) -> float:
    fallback = max(0.0, _finite_float(pipeline_source_start, 0.0) or 0.0)
    if not isinstance(render_window, (tuple, list)) or not render_window:
        return fallback
    actual = _finite_float(render_window[0])
    if actual is None or actual < 0.0:
        return fallback
    return actual


def _short_index_from_path(path: Any) -> int | None:
    try:
        name = Path(path).name
    except (TypeError, ValueError, OSError):
        return None
    marker = "_short_"
    if marker not in name:
        return None
    tail = name.rsplit(marker, 1)[1]
    token = tail.split("_", 1)[0]
    try:
        index = int(token)
    except ValueError:
        return None
    return index if index > 0 else None


def _source_duration_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> float:
    raw = kwargs.get("duration") if "duration" in kwargs else (args[5] if len(args) > 5 else 0)
    value = _finite_float(raw, 0.0) or 0.0
    return max(0.0, value)


def install_shorts_duration_safety() -> bool:
    """Install ordinary-Shorts guards until the remaining state is pipeline-owned."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.shorts as shorts_module

    original_process = shorts_module.process_and_send_shorts
    original_candidates = shorts_module.create_shorts_candidates
    original_speed_get = shorts_module.ashorts_speed_get
    original_setting_get = shorts_module.asettings_get
    original_render = shorts_module.render_short_clip
    original_postprocess = shorts_module.postprocess_short
    original_select = shorts_module.select_delivery_file
    original_probe = shorts_module.probe_media_async
    original_resolve_timing = shorts_module.resolve_delivery_timing

    async def safe_process(*args, **kwargs):
        if _factory_active():
            return await original_process(*args, **kwargs)
        state = {
            "speed": 1.0,
            "boundary_padding": False,
            "source_duration": _source_duration_from_call(args, kwargs),
            "candidates": [],
            "current_index": None,
            "render_windows": {},
            "speed_transform": {},
            "raw_probe_count": {},
        }
        token = _STATE.set(state)
        try:
            return await original_process(*args, **kwargs)
        finally:
            _STATE.reset(token)

    async def safe_speed_get():
        value = await original_speed_get()
        state = _STATE.get()
        if state is not None and not _factory_active():
            parsed = _finite_float(value)
            if parsed is not None and parsed > 0.0:
                state["speed"] = parsed
        return value

    async def safe_setting_get(key: str):
        value = await original_setting_get(key)
        state = _STATE.get()
        if state is not None and not _factory_active() and key == "shorts_boundary_padding":
            state["boundary_padding"] = bool(value)
        return value

    async def safe_candidates(*args, **kwargs):
        candidates = await original_candidates(*args, **kwargs)
        state = _STATE.get()
        if state is None or _factory_active() or not isinstance(candidates, list):
            return candidates
        speed = float(state.get("speed") or 1.0)
        enriched: list[Any] = []
        for item in candidates:
            if isinstance(item, dict):
                copied = copy.deepcopy(item)
                copied["_short_requested_speed"] = speed
                copied["_short_public_max_seconds"] = PUBLIC_SHORT_MAX_SEC
                enriched.append(copied)
            else:
                enriched.append(item)
        state["candidates"] = enriched
        return enriched

    async def safe_render(
        source_video_path,
        output_path,
        start_seconds,
        end_seconds,
        *,
        visual_mode="full_frame_vertical",
    ):
        state = _STATE.get()
        if state is None or _factory_active():
            return await original_render(
                source_video_path,
                output_path,
                start_seconds,
                end_seconds,
                visual_mode=visual_mode,
            )
        index = _short_index_from_path(output_path)
        candidates = state.get("candidates") or []
        if (
            index is None
            or index > len(candidates)
            or not isinstance(candidates[index - 1], dict)
        ):
            logger.warning(
                "Shorts duration safety could not bind render to candidate: %s",
                output_path,
            )
            return False
        candidate = candidates[index - 1]
        plan = plan_short_source_window(
            candidate,
            speed=state.get("speed", 1.0),
            boundary_padding=bool(state.get("boundary_padding")),
            source_duration=state.get("source_duration", 0.0),
        )
        if plan is None:
            logger.warning(
                "Shorts duration safety rejected candidate %d: %r",
                index,
                candidate,
            )
            return False
        render_start, render_end, snap_ceiling = plan
        state["current_index"] = index
        state.setdefault("render_windows", {})[index] = (render_start, render_end)
        return await original_render(
            source_video_path,
            output_path,
            render_start,
            render_end,
            visual_mode=visual_mode,
            silence_snap_max_end=snap_ceiling,
        )

    async def safe_postprocess(
        input_path,
        output_path,
        *,
        normalize_audio=True,
        speed=1.0,
    ):
        result = await original_postprocess(
            input_path,
            output_path,
            normalize_audio=normalize_audio,
            speed=speed,
        )
        state = _STATE.get()
        if state is not None and not _factory_active():
            index = _short_index_from_path(output_path) or state.get("current_index")
            if index is not None and short_speed_transform_required(speed):
                state.setdefault("speed_transform", {})[index] = bool(result)
        return result

    def safe_select(primary_path, fallback_path=None, *, max_size_mb):
        decision = original_select(
            primary_path,
            fallback_path,
            max_size_mb=max_size_mb,
        )
        state = _STATE.get()
        if state is None or _factory_active():
            return decision
        index = _short_index_from_path(primary_path) or state.get("current_index")
        speed = state.get("speed", 1.0)
        if (
            index is not None
            and short_speed_transform_required(speed)
            and state.get("speed_transform", {}).get(index) is not True
        ):
            logger.warning(
                "Shorts %s rejected: required speed transform failed; raw fallback is unsafe",
                index,
            )
            return DeliveryFileSelection(
                path=None,
                selected="none",
                reason="required_speed_transform_failed",
                primary_size_mb=decision.primary_size_mb,
                fallback_size_mb=decision.fallback_size_mb,
            )
        return decision

    async def safe_probe(path, *, timeout=20):
        probe = await original_probe(path, timeout=timeout)
        state = _STATE.get()
        if state is None or _factory_active() or probe is None:
            return probe
        index = _short_index_from_path(path)
        if index is None:
            return probe
        name = Path(path).name
        final_check = not name.endswith("_raw.mp4")
        if not final_check:
            counts = state.setdefault("raw_probe_count", {})
            counts[index] = int(counts.get(index, 0)) + 1
            final_check = counts[index] > 1
        if final_check and not public_short_duration_ok(getattr(probe, "duration", 0.0)):
            logger.warning(
                "Shorts %d rejected: final duration %.3fs exceeds public %.0fs cap",
                index,
                float(getattr(probe, "duration", 0.0) or 0.0),
                PUBLIC_SHORT_MAX_SEC,
            )
            return None
        return probe

    def safe_resolve_timing(
        *,
        source_start,
        raw_duration,
        source_duration=0.0,
        speed=1.0,
        speed_applied=False,
        final_duration=0.0,
    ):
        state = _STATE.get()
        if state is not None and not _factory_active():
            index = state.get("current_index")
            render_window = state.get("render_windows", {}).get(index)
            source_start = authoritative_short_source_start(source_start, render_window)
        return original_resolve_timing(
            source_start=source_start,
            raw_duration=raw_duration,
            source_duration=source_duration,
            speed=speed,
            speed_applied=speed_applied,
            final_duration=final_duration,
        )

    shorts_module.process_and_send_shorts = safe_process
    shorts_module.ashorts_speed_get = safe_speed_get
    shorts_module.asettings_get = safe_setting_get
    shorts_module.create_shorts_candidates = safe_candidates
    shorts_module.render_short_clip = safe_render
    shorts_module.postprocess_short = safe_postprocess
    shorts_module.select_delivery_file = safe_select
    shorts_module.probe_media_async = safe_probe
    shorts_module.resolve_delivery_timing = safe_resolve_timing

    _INSTALLED = True
    logger.info(
        "Ordinary Shorts duration safety installed: explicit silence ceiling, one speed "
        "compensation, required speed fail-closed, authoritative replay timing, "
        "final duration <=180s"
    )
    return True


__all__ = [
    "PUBLIC_DURATION_EPSILON_SEC",
    "PUBLIC_SHORT_MAX_SEC",
    "authoritative_short_source_start",
    "install_shorts_duration_safety",
    "plan_short_source_window",
    "public_short_duration_ok",
    "short_speed_transform_required",
]
