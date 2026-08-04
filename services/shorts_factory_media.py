#!/usr/bin/env python3
"""Strict media and timing policy for Factory and legacy LiveDub cut modes."""
from __future__ import annotations

import copy
import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from services.livedub_mix import get_mix_params
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async

logger = logging.getLogger(__name__)

_LIVEDUB_SOURCE_DURATION: ContextVar[float] = ContextVar(
    "livedub_downstream_source_duration",
    default=0.0,
)
_LIVEDUB_POLICY_INSTALLED = False


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(value, 30.0))


def livedub_downstream_envelope() -> tuple[float, float]:
    """Return ``(pre_roll, full_tail)`` for downstream translated cuts."""
    params = get_mix_params()
    required_tail = max(0.0, float(params.get("tail_pad_ms", 0)) / 1000.0)
    return (
        _env_float("LIVEDUB_DOWNSTREAM_PREROLL_SEC", 0.25),
        required_tail
        + _env_float("LIVEDUB_DOWNSTREAM_TAIL_EXTRA_SEC", 0.15),
    )


def align_livedub_interval(
    start_seconds: float,
    end_seconds: float,
    *,
    source_duration: float,
    public_max_seconds: float = 0.0,
) -> tuple[float, float] | None:
    """Build a context-safe interval without cutting the delayed Russian tail."""
    try:
        semantic_start = max(0.0, float(start_seconds))
        semantic_end = max(0.0, float(end_seconds))
        source_limit = max(0.0, float(source_duration))
        public_max = max(0.0, float(public_max_seconds))
    except (TypeError, ValueError, OverflowError):
        return None
    if semantic_end <= semantic_start or source_limit <= 0:
        return None

    pre_roll, desired_tail = livedub_downstream_envelope()
    required_tail = max(
        0.0,
        float(get_mix_params().get("tail_pad_ms", 0)) / 1000.0,
    )
    semantic_duration = semantic_end - semantic_start
    if public_max > 0 and semantic_duration + required_tail > public_max + 1e-6:
        return None

    available_extra = (
        max(0.0, public_max - semantic_duration)
        if public_max > 0
        else pre_roll + desired_tail
    )
    actual_pre = min(
        pre_roll,
        semantic_start,
        max(0.0, available_extra - required_tail),
    )
    actual_tail = min(desired_tail, max(0.0, available_extra - actual_pre))
    render_start = max(0.0, semantic_start - actual_pre)
    render_end = min(source_limit, semantic_end + actual_tail)

    if render_end - semantic_end + 1e-6 < required_tail:
        return None
    if render_end <= render_start:
        return None
    if public_max > 0 and render_end - render_start > public_max + 1e-6:
        return None
    return render_start, render_end


def align_livedub_candidate(
    candidate: dict[str, Any],
    *,
    source_duration: float,
    public_max_seconds: float,
) -> dict[str, Any] | None:
    """Copy one candidate and replace only its render-facing numeric interval."""
    if not isinstance(candidate, dict):
        return None
    interval = align_livedub_interval(
        candidate.get("start_seconds", 0),
        candidate.get("end_seconds", 0),
        source_duration=source_duration,
        public_max_seconds=public_max_seconds,
    )
    if interval is None:
        return None

    render_start, render_end = interval
    item = copy.deepcopy(candidate)
    item["livedub_semantic_start_seconds"] = float(candidate.get("start_seconds", 0))
    item["livedub_semantic_end_seconds"] = float(candidate.get("end_seconds", 0))
    item["start_seconds"] = render_start
    item["end_seconds"] = render_end
    item["duration_seconds"] = render_end - render_start
    return item


def align_livedub_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: float,
    public_max_seconds: float,
) -> list[dict[str, Any]]:
    aligned: list[dict[str, Any]] = []
    rejected: list[str] = []
    for candidate in candidates or []:
        item = align_livedub_candidate(
            candidate,
            source_duration=source_duration,
            public_max_seconds=public_max_seconds,
        )
        if item is None:
            rejected.append(str((candidate or {}).get("title") or "без названия"))
        else:
            aligned.append(item)
    if rejected:
        logger.warning(
            "LiveDub downstream rejected %d/%d cuts without a complete Russian tail: %s",
            len(rejected),
            len(candidates or []),
            ", ".join(rejected[:8]),
        )
    return aligned


def align_livedub_montage_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: float,
) -> list[dict[str, Any]]:
    """Expand every montage fragment and keep only fully safe candidates."""
    output: list[dict[str, Any]] = []
    for candidate in copy.deepcopy(candidates or []):
        fragments = candidate.get("fragments") or []
        aligned = align_livedub_candidates(
            fragments,
            source_duration=source_duration,
            public_max_seconds=0.0,
        )
        if not fragments or len(aligned) != len(fragments):
            continue
        candidate["fragments"] = aligned
        candidate["total_dur"] = sum(
            float(item["end_seconds"]) - float(item["start_seconds"])
            for item in aligned
        )
        output.append(candidate)
    return output


async def probe_livedub_source_duration(
    source_path: Path,
    *,
    fallback_duration: float = 0.0,
) -> float:
    """Return exact translated duration; fail closed by default on probe failure."""
    probe = await probe_media_async(Path(source_path))
    if media_probe_is_deliverable(probe):
        assert probe is not None
        return float(probe.duration)

    if _env_bool("LIVEDUB_DOWNSTREAM_REQUIRE_PROBE", True):
        raise RuntimeError(
            "LiveDub-файл не прошёл обязательный media probe: нужны "
            "доказанные video+audio и точная длительность"
        )

    logger.warning(
        "LiveDub source probe failed; explicit degraded fallback duration=%.3f",
        float(fallback_duration or 0.0),
    )
    return max(0.0, float(fallback_duration or 0.0))


async def validated_factory_source_duration(
    source_path: Path,
    expected_duration: int,
) -> float:
    """Return exact ffprobe duration and reject truncated video/audio sources."""
    probe = await probe_media_async(source_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("Общий Factory-источник не прошёл media probe (нужны video+audio)")
    assert probe is not None
    exact_duration = float(probe.duration)
    if exact_duration + 3.0 < float(expected_duration):
        raise RuntimeError(
            "Общий Factory-источник обрезан: "
            f"ожидалось около {expected_duration:.0f}с, получено {exact_duration:.3f}с"
        )
    return exact_duration


def install_livedub_downstream_media_policy() -> bool:
    """Install task-local wrappers for Shorts, Clips, Montage and Highlights."""
    global _LIVEDUB_POLICY_INSTALLED
    if _LIVEDUB_POLICY_INSTALLED:
        return True

    import pipelines.clips as clips_module
    import pipelines.main_pipeline as main_pipeline_module
    import pipelines.montage as montage_module
    import pipelines.shorts as shorts_module

    original_process_shorts = shorts_module.process_and_send_shorts
    original_process_clips = clips_module.process_and_send_clips
    original_process_montage = montage_module.process_and_send_montage
    original_process_highlights = montage_module.process_and_send_highlights
    original_shorts_candidates = shorts_module.create_shorts_candidates
    original_clips_candidates = clips_module.create_clips_candidates
    original_shorts_setting = shorts_module.asettings_get
    original_render_clip = clips_module.render_clip

    async def livedub_shorts_setting(key: str):
        if _LIVEDUB_SOURCE_DURATION.get() > 0 and key == "shorts_boundary_padding":
            return False
        return await original_shorts_setting(key)

    async def livedub_shorts_candidates(*args, **kwargs):
        candidates = await original_shorts_candidates(*args, **kwargs)
        source_duration = _LIVEDUB_SOURCE_DURATION.get()
        if source_duration <= 0:
            return candidates
        return align_livedub_candidates(
            candidates,
            source_duration=source_duration,
            public_max_seconds=180.0,
        )

    async def livedub_clips_candidates(*args, **kwargs):
        candidates = await original_clips_candidates(*args, **kwargs)
        source_duration = _LIVEDUB_SOURCE_DURATION.get()
        if source_duration <= 0:
            return candidates
        return align_livedub_candidates(
            candidates,
            source_duration=source_duration,
            public_max_seconds=900.0,
        )

    async def verified_render_clip(*args, **kwargs):
        ok = await original_render_clip(*args, **kwargs)
        if not ok:
            return False
        output_path = kwargs.get("output_path")
        if output_path is None and len(args) >= 2:
            output_path = args[1]
        if output_path is None:
            return False
        output = Path(output_path)
        probe = await probe_media_async(output)
        if not media_probe_is_deliverable(probe):
            logger.warning(
                "Clips delivery rejected: rendered file lacks verified video+audio: %s",
                output,
            )
            output.unlink(missing_ok=True)
            return False
        return True

    async def process_shorts(
        url,
        media_id,
        mp3_path,
        title,
        performer,
        duration,
        ai_data,
        update,
        existing_audio_part=None,
        existing_client=None,
        rutube_url="",
        vk_url="",
        workdir=None,
        livedub_video_path=None,
    ):
        if not livedub_video_path or not Path(livedub_video_path).exists():
            return await original_process_shorts(
                url,
                media_id,
                mp3_path,
                title,
                performer,
                duration,
                ai_data,
                update,
                existing_audio_part=existing_audio_part,
                existing_client=existing_client,
                rutube_url=rutube_url,
                vk_url=vk_url,
                workdir=workdir,
                livedub_video_path=livedub_video_path,
            )
        actual_duration = await probe_livedub_source_duration(
            Path(livedub_video_path),
            fallback_duration=duration,
        )
        token = _LIVEDUB_SOURCE_DURATION.set(actual_duration)
        try:
            return await original_process_shorts(
                url,
                media_id,
                mp3_path,
                title,
                performer,
                actual_duration,
                ai_data,
                update,
                existing_audio_part=existing_audio_part,
                existing_client=existing_client,
                rutube_url=rutube_url,
                vk_url=vk_url,
                workdir=workdir,
                livedub_video_path=livedub_video_path,
            )
        finally:
            _LIVEDUB_SOURCE_DURATION.reset(token)

    async def process_clips(
        url,
        media_id,
        mp3_path,
        title,
        performer,
        duration,
        ai_data,
        update,
        existing_audio_part=None,
        existing_client=None,
        rutube_url="",
        vk_url="",
        livedub_video_path=None,
    ):
        if not livedub_video_path or not Path(livedub_video_path).exists():
            return await original_process_clips(
                url,
                media_id,
                mp3_path,
                title,
                performer,
                duration,
                ai_data,
                update,
                existing_audio_part=existing_audio_part,
                existing_client=existing_client,
                rutube_url=rutube_url,
                vk_url=vk_url,
                livedub_video_path=livedub_video_path,
            )
        actual_duration = await probe_livedub_source_duration(
            Path(livedub_video_path),
            fallback_duration=duration,
        )
        token = _LIVEDUB_SOURCE_DURATION.set(actual_duration)
        try:
            return await original_process_clips(
                url,
                media_id,
                mp3_path,
                title,
                performer,
                actual_duration,
                ai_data,
                update,
                existing_audio_part=existing_audio_part,
                existing_client=existing_client,
                rutube_url=rutube_url,
                vk_url=vk_url,
                livedub_video_path=livedub_video_path,
            )
        finally:
            _LIVEDUB_SOURCE_DURATION.reset(token)

    async def process_montage(
        url,
        media_id,
        mp3_path,
        title,
        performer,
        duration,
        ai_data,
        update,
        existing_audio_part=None,
        existing_client=None,
        rutube_url="",
        vk_url="",
        prefetched_candidates=None,
        livedub_video_path=None,
    ):
        candidates = prefetched_candidates
        actual_duration = float(duration or 0)
        if livedub_video_path and Path(livedub_video_path).exists():
            actual_duration = await probe_livedub_source_duration(
                Path(livedub_video_path),
                fallback_duration=duration,
            )
            candidates = align_livedub_montage_candidates(
                prefetched_candidates or [],
                source_duration=actual_duration,
            )
        return await original_process_montage(
            url,
            media_id,
            mp3_path,
            title,
            performer,
            actual_duration,
            ai_data,
            update,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
            rutube_url=rutube_url,
            vk_url=vk_url,
            prefetched_candidates=candidates,
            livedub_video_path=livedub_video_path,
        )

    async def process_highlights(
        url,
        media_id,
        mp3_path,
        title,
        performer,
        duration,
        ai_data,
        update,
        existing_audio_part=None,
        existing_client=None,
        rutube_url="",
        vk_url="",
        prefetched_candidates=None,
        livedub_video_path=None,
    ):
        actual_duration = float(duration or 0)
        if livedub_video_path and Path(livedub_video_path).exists():
            actual_duration = await probe_livedub_source_duration(
                Path(livedub_video_path),
                fallback_duration=duration,
            )
        return await original_process_highlights(
            url,
            media_id,
            mp3_path,
            title,
            performer,
            actual_duration,
            ai_data,
            update,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
            rutube_url=rutube_url,
            vk_url=vk_url,
            prefetched_candidates=prefetched_candidates,
            livedub_video_path=livedub_video_path,
        )

    shorts_module.asettings_get = livedub_shorts_setting
    shorts_module.create_shorts_candidates = livedub_shorts_candidates
    shorts_module.process_and_send_shorts = process_shorts
    clips_module.create_clips_candidates = livedub_clips_candidates
    clips_module.render_clip = verified_render_clip
    clips_module.process_and_send_clips = process_clips
    montage_module.process_and_send_montage = process_montage
    montage_module.process_and_send_highlights = process_highlights

    main_pipeline_module.process_and_send_shorts = process_shorts
    main_pipeline_module.process_and_send_clips = process_clips
    main_pipeline_module.process_and_send_montage = process_montage
    main_pipeline_module.process_and_send_highlights = process_highlights

    _LIVEDUB_POLICY_INSTALLED = True
    logger.info(
        "LiveDub downstream media policy installed: exact source duration, "
        "complete Russian tail, verified Clip video+audio"
    )
    return True


__all__ = [
    "align_livedub_candidate",
    "align_livedub_candidates",
    "align_livedub_interval",
    "align_livedub_montage_candidates",
    "install_livedub_downstream_media_policy",
    "livedub_downstream_envelope",
    "probe_livedub_source_duration",
    "validated_factory_source_duration",
]
