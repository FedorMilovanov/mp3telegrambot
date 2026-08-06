#!/usr/bin/env python3
"""Guard Factory disk usage without multiplying authenticated yt-dlp sessions."""
from __future__ import annotations

import asyncio
import functools
import logging
import math
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_GIB = 1024**3
_AUDIO_UNKNOWN_FLOOR_BYTES = 4 * _GIB
_VIDEO_UNKNOWN_FLOOR_BYTES = 6 * _GIB
_MAX_DURATION_HINTS = 256
_MAX_ACTIVE_REQUESTS = 64
_INSTALLED = False


@dataclass
class _FactoryRequestState:
    duration: float
    audio_done: asyncio.Event
    audio_error: BaseException | None = None


_DURATION_HINTS: OrderedDict[str, float] = OrderedDict()
_ACTIVE_REQUESTS: OrderedDict[str, _FactoryRequestState] = OrderedDict()


def _finite_positive(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _request_key(url: str) -> str:
    return str(url or "").strip()


def _remember_duration(url: str, duration: Any) -> float:
    key = _request_key(url)
    value = _finite_positive(duration)
    if not key or not value:
        return 0.0
    _DURATION_HINTS[key] = value
    _DURATION_HINTS.move_to_end(key)
    while len(_DURATION_HINTS) > _MAX_DURATION_HINTS:
        _DURATION_HINTS.popitem(last=False)
    return value


def _duration_hint(url: str) -> float:
    key = _request_key(url)
    value = _DURATION_HINTS.get(key, 0.0)
    if value:
        _DURATION_HINTS.move_to_end(key)
    return value


def register_factory_source_info(
    url: str,
    info: dict[str, Any],
) -> float:
    """Record already-fetched metadata for local disk proof and request ordering."""
    duration = _remember_duration(url, info.get("duration"))
    key = _request_key(url)
    if not key:
        return duration

    _ACTIVE_REQUESTS[key] = _FactoryRequestState(
        duration=duration,
        audio_done=asyncio.Event(),
    )
    _ACTIVE_REQUESTS.move_to_end(key)
    while len(_ACTIVE_REQUESTS) > _MAX_ACTIVE_REQUESTS:
        _ACTIVE_REQUESTS.popitem(last=False)
    return duration


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
    """Return selected download bytes and duration from an existing yt-dlp JSON."""
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
        pcm_bound = duration * 48000.0 * 2.0 * 2.0
        modeled = estimate + pcm_bound * 1.10 + 512 * 1024**2
        floor = _AUDIO_UNKNOWN_FLOOR_BYTES if not estimate else 2 * _GIB
        return int(math.ceil(max(modeled, floor)))
    if kind == "video":
        if not estimate:
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


def factory_delivery_sort_args(base_args: Iterable[str]) -> list[str]:
    """Prefer SDR first, then maximize resolution/FPS for the SDR H.264 output."""
    result = list(base_args)
    result.extend(["--format-sort", "hdr:0,res,fps"])
    return result


async def estimate_factory_selection(
    url: str,
    format_selector: str,
) -> tuple[int, float]:
    """Return a conservative local estimate from metadata already fetched by Factory.

    The former implementation launched ``yt-dlp --simulate`` here. Audio and
    video guards ran concurrently, so one Factory request could open several
    authenticated YouTube extractor sessions before the real downloads even
    started. That multiplied proxy timeouts and Firefox-cookie access. Unknown
    selected bytes are intentionally represented as zero; the duration-aware
    conservative floors below remain the source of truth.
    """
    del format_selector
    return 0, _duration_hint(url)


def _state_for(url: str) -> _FactoryRequestState | None:
    key = _request_key(url)
    state = _ACTIVE_REQUESTS.get(key)
    if state is not None:
        _ACTIVE_REQUESTS.move_to_end(key)
    return state


def _finish_request(url: str, state: _FactoryRequestState | None) -> None:
    if state is None:
        return
    key = _request_key(url)
    if _ACTIVE_REQUESTS.get(key) is state:
        _ACTIVE_REQUESTS.pop(key, None)


def install_factory_disk_guard() -> bool:
    """Install local disk proof and one-at-a-time Factory source acquisition."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.shorts_factory as factory_pipeline
    import services.shorts_factory_source as source
    from services.shorts_factory_long_fit import install_factory_long_fit_policy

    original_load_info = factory_pipeline._load_video_info
    original_audio = source.download_factory_audio_source
    original_video = source.download_factory_video_source
    original_sort_reset = source._factory_quality_sort_reset

    @functools.wraps(original_load_info)
    async def load_info_with_disk_hint(url: str) -> dict[str, Any]:
        info = await original_load_info(url)
        if isinstance(info, dict):
            register_factory_source_info(url, info)
        return info

    @functools.wraps(original_sort_reset)
    def output_safe_sort_reset() -> list[str]:
        return factory_delivery_sort_args(original_sort_reset())

    source._factory_quality_sort_reset = output_safe_sort_reset
    factory_pipeline._load_video_info = load_info_with_disk_hint

    @functools.wraps(original_audio)
    async def guarded_audio(url: str, media_id: str) -> Path:
        state = _state_for(url)
        _estimated, duration = await estimate_factory_selection(
            url,
            "bestaudio/best",
        )
        required = required_factory_free_bytes("audio", 0, duration)
        ensure_factory_free_space(
            [source.DOWNLOAD_DIR],
            required_bytes=required,
            label="максимального аудио и lossless FLAC",
        )
        try:
            return await original_audio(url, media_id)
        except BaseException as exc:
            if state is not None:
                state.audio_error = exc
            raise
        finally:
            if state is not None:
                state.audio_done.set()

    @functools.wraps(original_video)
    async def guarded_video(
        url: str,
        media_id: str,
        workdir: Path | None = None,
    ) -> Path:
        state = _state_for(url)
        _estimated, duration = await estimate_factory_selection(
            url,
            "bestvideo+bestaudio/best",
        )
        required = required_factory_free_bytes("video", 0, duration)
        target_dir = Path(workdir) if workdir is not None else source.DOWNLOAD_DIR
        ensure_factory_free_space(
            [target_dir, source.DOWNLOAD_DIR],
            required_bytes=required,
            label="максимального видео, отдельных потоков и merge",
        )

        # Factory creates the video task first, then awaits its analysis audio.
        # Wait locally so two yt-dlp processes do not read browser cookies and
        # hammer the same proxy/YouTube session at once. As soon as audio is
        # ready, video download overlaps with Gemini analysis exactly as intended.
        if state is not None:
            await state.audio_done.wait()
            if state.audio_error is not None:
                raise RuntimeError(
                    "Factory video download skipped because the audio source failed"
                ) from state.audio_error

        try:
            return await original_video(url, media_id, workdir=workdir)
        finally:
            _finish_request(url, state)

    source.download_factory_audio_source = guarded_audio
    source.download_factory_video_source = guarded_video
    factory_pipeline._download_factory_audio = guarded_audio
    factory_pipeline.download_video_for_shorts = guarded_video

    eager_factory = sys.modules.get("pipelines.shorts_factory")
    if eager_factory is not None:
        eager_factory._load_video_info = load_info_with_disk_hint
        eager_factory._download_factory_audio = guarded_audio
        eager_factory.download_video_for_shorts = guarded_video

    if not install_factory_long_fit_policy():
        return False

    _INSTALLED = True
    logger.info(
        "Shorts Factory disk/fidelity guard installed: metadata-only local "
        "disk proof, no yt-dlp simulate preflight, audio-first authenticated "
        "download ordering, SDR-first maximum res/FPS and exact-interval "
        "two-pass fitting for oversized long clips"
    )
    return True


__all__ = [
    "ensure_factory_free_space",
    "estimate_factory_selection",
    "estimate_factory_selection_payload",
    "factory_delivery_sort_args",
    "install_factory_disk_guard",
    "register_factory_source_info",
    "required_factory_free_bytes",
]
