#!/usr/bin/env python3
"""Speech-proven render boundaries for translated SHORTS FACTORY cuts.

Gemini still chooses semantic candidates from the original source. For a
foreign-language source those timestamps are *not* publication boundaries: the
Yandex live-voice track is delayed and can contain translation gaps. This
module binds the exact provenance-recorded VOT Russian MP3 to the active
Factory source, derives its speech timeline once, and refines every translated
cut against proved Russian speech.

If that proof is unavailable, translated Factory cuts fail closed instead of
silently falling back to English-timeline timestamps.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import shutil
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from services.async_process import run_cancellable_process
from services.livedub_mix import get_mix_params
from services.livedub_ru_provenance import read_ru_audio_provenance

logger = logging.getLogger(__name__)

PUBLIC_SHORT_MAX_SEC = 180.0
PUBLIC_LONG_MAX_SEC = 900.0
SHORT_MIN_SEC = 35.0
LONG_MIN_SEC = 300.0

_TIMELINE_BY_VIDEO: dict[str, dict[str, Any]] = {}
_TIMELINE_LOCK = threading.Lock()
_CURRENT_TIMELINE: ContextVar[dict[str, Any] | None] = ContextVar(
    "factory_ru_boundary_timeline",
    default=None,
)
_CAPTURE_INSTALLED = False


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _candidate_seconds(item: dict[str, Any]) -> tuple[float, float]:
    try:
        start = max(0.0, float(item.get("start_seconds", 0)))
        end = max(0.0, float(item.get("end_seconds", 0)))
    except (TypeError, ValueError):
        return 0.0, 0.0
    return start, end


def _format_seconds(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _path_key(path: Path | str) -> str:
    try:
        return str(Path(path).resolve(strict=False)).casefold()
    except (OSError, TypeError, ValueError):
        return str(path).casefold()


def _remember_video_timeline(video_path: Path | str, timeline: dict[str, Any]) -> None:
    key = _path_key(video_path)
    with _TIMELINE_LOCK:
        if len(_TIMELINE_BY_VIDEO) >= 64 and key not in _TIMELINE_BY_VIDEO:
            oldest_key = next(iter(_TIMELINE_BY_VIDEO), None)
            if oldest_key is not None:
                _TIMELINE_BY_VIDEO.pop(oldest_key, None)
        _TIMELINE_BY_VIDEO[key] = timeline


def _take_video_timeline(video_path: Path | str) -> dict[str, Any] | None:
    key = _path_key(video_path)
    with _TIMELINE_LOCK:
        return _TIMELINE_BY_VIDEO.pop(key, None)


def _silence_event_value(line: str, marker: str) -> float | None:
    """Parse one FFmpeg silencedetect numeric value without adding regex debt."""
    position = str(line or "").find(marker)
    if position < 0:
        return None
    tail = str(line)[position + len(marker):].lstrip()
    token = tail.split(maxsplit=1)[0] if tail else ""
    token = token.rstrip("|,;")
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def speech_intervals_from_silence_log(
    stderr: str,
    *,
    duration: float,
    minimum_speech: float = 0.08,
) -> list[tuple[float, float]]:
    """Invert FFmpeg silencedetect events into non-silent RU speech spans."""
    limit = max(0.0, float(duration))
    if limit <= 0:
        return []

    events: list[tuple[float, str]] = []
    for line in str(stderr or "").splitlines():
        silence_start = _silence_event_value(line, "silence_start:")
        if silence_start is not None:
            events.append((silence_start, "start"))
        silence_end = _silence_event_value(line, "silence_end:")
        if silence_end is not None:
            events.append((silence_end, "end"))
    events.sort(key=lambda item: (item[0], 0 if item[1] == "start" else 1))

    speech: list[tuple[float, float]] = []
    cursor = 0.0
    in_silence = False
    for raw_time, kind in events:
        point = max(0.0, min(limit, raw_time))
        if kind == "start":
            if not in_silence and point - cursor >= minimum_speech:
                speech.append((cursor, point))
            in_silence = True
        else:
            if in_silence:
                cursor = max(cursor, point)
            in_silence = False
    if not in_silence and limit - cursor >= minimum_speech:
        speech.append((cursor, limit))

    merged: list[tuple[float, float]] = []
    merge_gap = 0.10
    for start, end in speech:
        if end <= start:
            continue
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


async def _probe_audio_duration(path: Path, fallback: float) -> float:
    """Use the exact VOT artifact duration, never rounded source metadata."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return max(0.0, float(fallback))
    result = await run_cancellable_process(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=60,
        text=True,
    )
    if result.returncode == 0:
        try:
            duration = float((result.stdout or "").strip())
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            return duration
    return max(0.0, float(fallback))


async def _detect_exact_ru_speech(
    ru_audio_path: Path,
    *,
    source_duration: float,
) -> dict[str, Any]:
    """Build one deterministic speech timeline from the exact VOT RU track."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable for Factory RU boundary proof")
    if not ru_audio_path.is_file() or ru_audio_path.stat().st_size <= 1024:
        raise RuntimeError("exact VOT RU audio is missing or empty")

    exact_duration = await _probe_audio_duration(ru_audio_path, source_duration)
    if exact_duration <= 0:
        raise RuntimeError("exact VOT RU audio duration could not be proved")

    noise_db = _env_float("SHORTS_FACTORY_RU_SILENCE_DB", -45.0, -70.0, -20.0)
    min_silence = _env_float(
        "SHORTS_FACTORY_RU_MIN_SILENCE_SEC",
        0.25,
        0.10,
        1.50,
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(ru_audio_path),
        "-vn",
        "-sn",
        "-dn",
        "-af",
        f"silencedetect=noise={noise_db:.1f}dB:d={min_silence:.3f}",
        "-f",
        "null",
        "-",
    ]
    result = await run_cancellable_process(command, timeout=1200, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg could not derive exact VOT RU speech boundaries: "
            + (result.stderr or "")[-300:]
        )

    intervals = speech_intervals_from_silence_log(
        result.stderr or "",
        duration=exact_duration,
    )
    if not intervals:
        raise RuntimeError("exact VOT RU track contains no proved speech intervals")

    delay_sec = max(0.0, float(get_mix_params().get("delay_ms", 0)) / 1000.0)
    final_intervals = [
        (start + delay_sec, end + delay_sec)
        for start, end in intervals
        if end > start
    ]
    speech_seconds = sum(end - start for start, end in intervals)
    logger.info(
        "Shorts Factory RU boundary proof: source=%s duration=%.3fs "
        "intervals=%d speech=%.1fs delay=%.3fs",
        ru_audio_path.name,
        exact_duration,
        len(final_intervals),
        speech_seconds,
        delay_sec,
    )
    return {
        "proof": "exact-vot-ru-silencedetect-v2",
        "audio_name": ru_audio_path.name,
        "audio_duration_seconds": exact_duration,
        "delay_seconds": delay_sec,
        "intervals": final_intervals,
    }


def _factory_prepare_workdir(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Path | None:
    value = kwargs.get("workdir")
    if value is None and len(args) > 1:
        value = args[1]
    try:
        return Path(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _factory_prepare_duration(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> float:
    value = kwargs.get("duration")
    if value is None and len(args) > 2:
        value = args[2]
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def install_factory_ru_boundary_capture() -> bool:
    """Capture exact VOT RU speech only on the active Factory source route."""
    global _CAPTURE_INSTALLED
    if _CAPTURE_INSTALLED:
        return True

    import pipelines.shorts_factory as factory_pipeline

    # Production Factory source quality replaces _prepare_translation_video
    # with a direct get_live_dub_audio + maximum-quality mix. The runtime calls
    # this installer only after that source policy is installed.
    current_prepare = factory_pipeline._prepare_translation_video
    if not getattr(current_prepare, "_mp3bot_factory_ru_boundary_capture", False):

        async def captured_prepare(*args: Any, **kwargs: Any):
            workdir = _factory_prepare_workdir(args, kwargs)
            requested_duration = _factory_prepare_duration(args, kwargs)
            result = await current_prepare(*args, **kwargs)
            try:
                if workdir is None:
                    logger.warning(
                        "Shorts Factory RU boundary proof unavailable: workdir missing"
                    )
                    return result
                exact_ru = read_ru_audio_provenance(workdir)
                if exact_ru is None:
                    logger.warning(
                        "Shorts Factory RU boundary proof unavailable: "
                        "no exact VOT provenance in %s",
                        workdir,
                    )
                    return result
                timeline = await _detect_exact_ru_speech(
                    exact_ru,
                    source_duration=requested_duration,
                )
                _remember_video_timeline(Path(result), timeline)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The parent alignment stage is fail-closed: keeping the source
                # result here preserves diagnostics while unproved cuts cannot
                # escape later as English-timeline fallbacks.
                logger.warning(
                    "Shorts Factory RU boundary capture failed closed later: %s",
                    str(exc)[:240],
                )
            return result

        captured_prepare._mp3bot_factory_ru_boundary_capture = True  # type: ignore[attr-defined]
        factory_pipeline._prepare_translation_video = captured_prepare

    # Source preparation happens in a child asyncio task. ContextVar writes in
    # that child do not propagate to its parent, so transfer the proof by the
    # unique source path when the parent persists the source, then bind it to a
    # request-local ContextVar for candidate alignment.
    current_persist = factory_pipeline._persist_factory_source
    if not getattr(current_persist, "_mp3bot_factory_ru_boundary_capture", False):

        def captured_persist(source_path: Path, media_id: str) -> Path:
            timeline = _take_video_timeline(source_path)
            destination = current_persist(source_path, media_id)
            if timeline is not None:
                _CURRENT_TIMELINE.set(timeline)
                logger.info(
                    "Shorts Factory RU boundary proof bound to %s (%d intervals)",
                    destination.name,
                    len(timeline.get("intervals") or []),
                )
            else:
                _CURRENT_TIMELINE.set(None)
            return destination

        captured_persist._mp3bot_factory_ru_boundary_capture = True  # type: ignore[attr-defined]
        factory_pipeline._persist_factory_source = captured_persist

    _CAPTURE_INSTALLED = True
    return True


def _public_limits(candidates: list[dict[str, Any]]) -> tuple[float, float, bool]:
    for item in candidates:
        start, end = _candidate_seconds(item)
        if end - start >= LONG_MIN_SEC:
            return LONG_MIN_SEC, PUBLIC_LONG_MAX_SEC, True
    return SHORT_MIN_SEC, PUBLIC_SHORT_MAX_SEC, False


def _find_interval_containing(
    intervals: list[tuple[float, float]],
    point: float,
) -> tuple[float, float] | None:
    for start, end in intervals:
        if start <= point <= end:
            return start, end
    return None


def _next_interval(
    intervals: list[tuple[float, float]],
    point: float,
) -> tuple[float, float] | None:
    for start, end in intervals:
        if start >= point:
            return start, end
    return None


def _previous_interval(
    intervals: list[tuple[float, float]],
    point: float,
) -> tuple[float, float] | None:
    previous = None
    for start, end in intervals:
        if end <= point:
            previous = (start, end)
        else:
            break
    return previous


def _clip_speech_stats(
    intervals: list[tuple[float, float]],
    start: float,
    end: float,
) -> tuple[float, float]:
    """Return Russian-speech coverage ratio and largest internal RU gap."""
    if end <= start:
        return 0.0, float("inf")
    overlaps: list[tuple[float, float]] = []
    for speech_start, speech_end in intervals:
        left = max(start, speech_start)
        right = min(end, speech_end)
        if right > left:
            overlaps.append((left, right))
    if not overlaps:
        return 0.0, end - start

    speech_seconds = sum(right - left for left, right in overlaps)
    largest_gap = max(0.0, overlaps[0][0] - start, end - overlaps[-1][1])
    for previous, following in zip(overlaps, overlaps[1:]):
        largest_gap = max(largest_gap, following[0] - previous[1])
    return speech_seconds / (end - start), largest_gap


def align_candidates_to_ru_speech(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
    speech_intervals: list[tuple[float, float]],
    delay_seconds: float,
    proof: str = "exact-vot-ru-silencedetect-v2",
) -> list[dict[str, Any]]:
    """Convert semantic EN ranges into publication-safe RU speech ranges."""
    if not candidates:
        return []
    intervals = sorted(
        (
            (max(0.0, float(start)), max(0.0, float(end)))
            for start, end in speech_intervals
            if float(end) > float(start)
        ),
        key=lambda pair: pair[0],
    )
    if not intervals:
        raise RuntimeError("Factory translated cuts have no proved Russian speech timeline")

    minimum, public_max, is_long = _public_limits(candidates)
    source_limit = max(0.0, float(source_duration))
    max_start_back = _env_float("SHORTS_FACTORY_RU_START_BACK_SEC", 3.0, 0.25, 8.0)
    max_start_forward = _env_float("SHORTS_FACTORY_RU_START_FORWARD_SEC", 4.0, 0.25, 10.0)
    max_end_forward = _env_float("SHORTS_FACTORY_RU_END_FORWARD_SEC", 4.0, 0.25, 10.0)
    max_end_back = _env_float("SHORTS_FACTORY_RU_END_BACK_SEC", 4.0, 0.25, 10.0)
    end_pad = _env_float("SHORTS_FACTORY_RU_END_PAD_SEC", 0.08, 0.0, 0.30)
    max_internal_gap = _env_float(
        "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_LONG_SEC" if is_long
        else "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_SEC",
        12.0 if is_long else 4.0,
        1.0,
        30.0,
    )
    minimum_coverage = _env_float(
        "SHORTS_FACTORY_RU_MIN_COVERAGE_LONG" if is_long
        else "SHORTS_FACTORY_RU_MIN_COVERAGE",
        0.55 if is_long else 0.65,
        0.20,
        0.98,
    )

    aligned: list[dict[str, Any]] = []
    rejected: list[str] = []
    delay = max(0.0, float(delay_seconds))

    for item in copy.deepcopy(candidates):
        semantic_start, semantic_end = _candidate_seconds(item)
        if semantic_end <= semantic_start:
            rejected.append(str(item.get("title") or "invalid"))
            continue
        target_start = semantic_start + delay
        target_end = semantic_end + delay

        start_interval = _find_interval_containing(intervals, target_start)
        if start_interval is not None:
            # Prefer the real RU phrase onset when it is nearby. If the
            # semantic anchor falls inside a longer uninterrupted RU phrase,
            # keep the anchor: RU-speech membership is already proved and we
            # avoid rejecting otherwise good candidates merely because there
            # was no >=250ms silence in the preceding few seconds.
            distance = target_start - start_interval[0]
            render_start = (
                start_interval[0] if distance <= max_start_back else target_start
            )
        else:
            next_interval = _next_interval(intervals, target_start)
            if next_interval is None or next_interval[0] - target_start > max_start_forward:
                rejected.append(str(item.get("title") or "start-no-ru-speech"))
                continue
            render_start = next_interval[0]

        end_interval = _find_interval_containing(intervals, target_end)
        if end_interval is not None:
            distance = end_interval[1] - target_end
            render_end = (
                end_interval[1] + end_pad
                if distance <= max_end_forward
                else target_end
            )
        else:
            previous_interval = _previous_interval(intervals, target_end)
            if previous_interval is None or target_end - previous_interval[1] > max_end_back:
                rejected.append(str(item.get("title") or "end-no-ru-speech"))
                continue
            render_end = previous_interval[1] + end_pad

        render_start = max(0.0, min(source_limit, render_start))
        render_end = max(0.0, min(source_limit, render_end))
        rendered_duration = render_end - render_start
        if rendered_duration < minimum - 1e-6:
            rejected.append(str(item.get("title") or "too-short-after-ru-align"))
            continue
        if rendered_duration > public_max + 1e-6:
            rejected.append(str(item.get("title") or "too-long-after-ru-align"))
            continue

        coverage, largest_gap = _clip_speech_stats(
            intervals,
            render_start,
            render_end,
        )
        if coverage + 1e-6 < minimum_coverage:
            rejected.append(str(item.get("title") or "low-ru-speech-coverage"))
            continue
        if largest_gap > max_internal_gap + 1e-6:
            rejected.append(str(item.get("title") or "untranslated-ru-gap"))
            continue

        item["start_seconds"] = render_start
        item["end_seconds"] = render_end
        item["duration_seconds"] = rendered_duration
        item["start"] = _format_seconds(render_start)
        item["end"] = _format_seconds(render_end)
        item["livedub_semantic_start_seconds"] = semantic_start
        item["livedub_semantic_end_seconds"] = semantic_end
        item["livedub_ru_target_start_seconds"] = target_start
        item["livedub_ru_target_end_seconds"] = target_end
        item["livedub_ru_boundary_proof"] = proof
        item["livedub_ru_start_shift_seconds"] = render_start - semantic_start
        item["livedub_ru_end_shift_seconds"] = render_end - semantic_end
        item["livedub_ru_speech_coverage"] = coverage
        item["livedub_ru_max_internal_gap_seconds"] = largest_gap
        aligned.append(item)

    if rejected:
        logger.warning(
            "Shorts Factory RU boundary alignment rejected %d/%d candidates: %s",
            len(rejected),
            len(candidates),
            ", ".join(rejected[:8]),
        )
    if candidates and not aligned:
        raise RuntimeError(
            "Ни один кандидат не получил доказанные русские границы речи; "
            "английские таймкоды не публикуются как fallback"
        )
    return aligned


def align_factory_livedub_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
) -> list[dict[str, Any]]:
    """Align current translated Factory candidates to exact VOT RU speech."""
    if not candidates:
        return []
    timeline = _CURRENT_TIMELINE.get()
    if not timeline:
        raise RuntimeError(
            "Exact VOT RU boundary proof is unavailable; refusing unverified "
            "English-timeline cuts"
        )
    return align_candidates_to_ru_speech(
        candidates,
        source_duration=source_duration,
        speech_intervals=list(timeline.get("intervals") or []),
        delay_seconds=0.0,
        proof=str(timeline.get("proof") or "exact-vot-ru-silencedetect-v2"),
    )


__all__ = [
    "PUBLIC_LONG_MAX_SEC",
    "PUBLIC_SHORT_MAX_SEC",
    "align_candidates_to_ru_speech",
    "align_factory_livedub_candidates",
    "install_factory_ru_boundary_capture",
    "speech_intervals_from_silence_log",
]
