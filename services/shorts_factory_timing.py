#!/usr/bin/env python3
"""Speech-proven render boundaries for translated SHORTS FACTORY cuts.

Gemini still chooses semantic candidates from the original source. For a
foreign-language source those timestamps are semantic anchors, not publication
boundaries. This module derives exact Russian speech evidence from the
provenance-bound VOT audio, optionally adds source-speech timing from provider
captions, and refines every translated candidate on the final mixed timeline.

Translated candidates fail closed individually when their Russian boundaries
cannot be proved. The caller decides whether other valid candidates may still
be rendered.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from services.async_process import run_cancellable_process
from services.livedub_mix import get_mix_params
from services.livedub_ru_provenance import read_ru_audio_provenance

logger = logging.getLogger(__name__)

PUBLIC_SHORT_MAX_SEC = 180.0
PUBLIC_LONG_MAX_SEC = 900.0
SHORT_MIN_SEC = 35.0
LONG_MIN_SEC = 300.0
RU_BOUNDARY_PROOF = "exact-vot-ru-plus-source-speech-v3"

_CURRENT_TIMELINE: ContextVar[dict[str, Any] | None] = ContextVar(
    "factory_ru_boundary_timeline",
    default=None,
)
_TIMELINE_BY_VIDEO: dict[str, dict[str, Any]] = {}
_TIMELINE_LOCK = threading.Lock()
_CAPTURE_INSTALLED = False


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


def _candidate_seconds(item: dict[str, Any]) -> tuple[float, float]:
    try:
        start = max(0.0, float(item.get("start_seconds", 0)))
        end = max(0.0, float(item.get("end_seconds", 0)))
    except (TypeError, ValueError):
        return 0.0, 0.0
    if not math.isfinite(start) or not math.isfinite(end):
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


def _remember_video_timeline(
    video_path: Path | str,
    timeline: dict[str, Any],
) -> None:
    key = _path_key(video_path)
    with _TIMELINE_LOCK:
        if len(_TIMELINE_BY_VIDEO) >= 64 and key not in _TIMELINE_BY_VIDEO:
            oldest_key = next(iter(_TIMELINE_BY_VIDEO), None)
            if oldest_key is not None:
                _TIMELINE_BY_VIDEO.pop(oldest_key, None)
        _TIMELINE_BY_VIDEO[key] = dict(timeline)


def _take_video_timeline(video_path: Path | str) -> dict[str, Any] | None:
    key = _path_key(video_path)
    with _TIMELINE_LOCK:
        return _TIMELINE_BY_VIDEO.pop(key, None)


def _silence_event_value(line: str, marker: str) -> float | None:
    """Parse one FFmpeg silencedetect numeric value without regex."""
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
    return value if math.isfinite(value) and value >= 0 else None


def speech_intervals_from_silence_log(
    stderr: str,
    *,
    duration: float,
    minimum_speech: float = 0.08,
) -> list[tuple[float, float]]:
    """Invert FFmpeg silencedetect events into non-silent spans."""
    limit = max(0.0, float(duration))
    if not math.isfinite(limit) or limit <= 0:
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
    return _merge_intervals(speech, max_gap=0.10)


def _merge_intervals(
    intervals: list[tuple[float, float]],
    *,
    max_gap: float,
) -> list[tuple[float, float]]:
    clean = sorted(
        (
            (max(0.0, float(start)), max(0.0, float(end)))
            for start, end in intervals
            if math.isfinite(float(start))
            and math.isfinite(float(end))
            and float(end) > float(start)
        ),
        key=lambda pair: pair[0],
    )
    merged: list[tuple[float, float]] = []
    for start, end in clean:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


async def _probe_audio_duration(path: Path) -> float:
    """Return the actual audio duration or zero when it cannot be proved."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
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
    if result.returncode != 0:
        return 0.0
    try:
        duration = float((result.stdout or "").strip())
    except (TypeError, ValueError):
        return 0.0
    return duration if math.isfinite(duration) and duration > 0 else 0.0


async def _detect_exact_ru_speech(
    ru_audio_path: Path,
) -> dict[str, Any]:
    """Build a deterministic speech timeline from the exact VOT RU track."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable for Factory RU boundary proof")
    if not ru_audio_path.is_file() or ru_audio_path.stat().st_size <= 1024:
        raise RuntimeError("exact VOT RU audio is missing or empty")

    exact_duration = await _probe_audio_duration(ru_audio_path)
    if exact_duration <= 0:
        raise RuntimeError("exact VOT RU audio duration could not be proved by ffprobe")

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

    raw_intervals = speech_intervals_from_silence_log(
        result.stderr or "",
        duration=exact_duration,
    )
    if not raw_intervals:
        raise RuntimeError("exact VOT RU track contains no proved speech intervals")

    delay_sec = max(0.0, float(get_mix_params().get("delay_ms", 0)) / 1000.0)
    final_intervals = [
        (start + delay_sec, end + delay_sec)
        for start, end in raw_intervals
        if end > start
    ]
    speech_seconds = sum(end - start for start, end in raw_intervals)
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
        "audio_name": ru_audio_path.name,
        "audio_duration_seconds": exact_duration,
        "delay_seconds": delay_sec,
        "intervals": final_intervals,
    }


async def _download_source_speech_intervals(
    *,
    url: str,
    workdir: Path,
    source_language: str,
) -> tuple[list[tuple[float, float]], str]:
    """Use provider/manual captions as source-speech timing when available."""
    from services.translation_editorial import parse_srt
    from services.translation_editorial_factory import download_original_srt

    root = Path(workdir) / "factory_boundary_source_srt"
    srt_path = await download_original_srt(
        url,
        root,
        language=source_language or "en",
    )
    cues = parse_srt(srt_path)
    intervals = _merge_intervals(
        [(float(cue.start), float(cue.end)) for cue in cues],
        max_gap=0.35,
    )
    if not intervals:
        raise RuntimeError("provider source captions contain no usable speech intervals")
    return intervals, "provider-source-srt"


async def prepare_factory_ru_boundary_evidence(
    *,
    url: str,
    workdir: Path,
    source_language: str,
) -> dict[str, Any]:
    """Build RU/source speech evidence before translated rendering."""
    exact_ru = read_ru_audio_provenance(workdir)
    if exact_ru is None:
        raise RuntimeError(
            "Exact VOT RU provenance is unavailable; translated cuts cannot be proved"
        )

    ru_task = asyncio.create_task(
        _detect_exact_ru_speech(exact_ru),
        name="shorts-factory-ru-speech-proof",
    )
    source_task = asyncio.create_task(
        _download_source_speech_intervals(
            url=url,
            workdir=workdir,
            source_language=source_language,
        ),
        name="shorts-factory-source-speech-proof",
    )
    try:
        ru_evidence = await ru_task
    except BaseException:
        if not source_task.done():
            source_task.cancel()
        await asyncio.gather(source_task, return_exceptions=True)
        raise

    source_intervals: list[tuple[float, float]] = []
    source_proof = "unavailable"
    try:
        source_intervals, source_proof = await source_task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Shorts Factory source-speech captions unavailable; "
            "using stricter RU-only gap gate: %s",
            str(exc)[:220],
        )

    proof = (
        RU_BOUNDARY_PROOF
        if source_intervals
        else "exact-vot-ru-silencedetect-v3"
    )
    return {
        **ru_evidence,
        "proof": proof,
        "source_speech_intervals": source_intervals,
        "source_speech_proof": source_proof,
    }


@contextmanager
def factory_ru_boundary_context(
    evidence: dict[str, Any],
) -> Iterator[None]:
    """Bind one Factory request's translation evidence only for alignment."""
    token = _CURRENT_TIMELINE.set(dict(evidence))
    try:
        yield
    finally:
        _CURRENT_TIMELINE.reset(token)


def _prepare_arg(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    name: str,
    position: int,
) -> Any:
    value = kwargs.get(name)
    if value is None and len(args) > position:
        value = args[position]
    return value


def install_factory_ru_boundary_capture() -> bool:
    """Bridge source-task evidence into the parent Factory request task."""
    global _CAPTURE_INSTALLED
    if _CAPTURE_INSTALLED:
        return True

    import pipelines.shorts_factory as factory_pipeline

    current_prepare = factory_pipeline._prepare_translation_video
    if not getattr(current_prepare, "_mp3bot_factory_ru_boundary_capture", False):

        async def captured_prepare(*args: Any, **kwargs: Any):
            result = await current_prepare(*args, **kwargs)
            workdir_value = _prepare_arg(args, kwargs, "workdir", 1)
            url_value = _prepare_arg(args, kwargs, "url", 0)
            language_value = _prepare_arg(args, kwargs, "source_language", 3)
            try:
                if workdir_value is None or not url_value:
                    logger.warning(
                        "Shorts Factory RU boundary evidence missing workdir/url"
                    )
                    return result
                evidence = await prepare_factory_ru_boundary_evidence(
                    url=str(url_value),
                    workdir=Path(workdir_value),
                    source_language=str(language_value or ""),
                )
                _remember_video_timeline(Path(result), evidence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Alignment remains fail-closed. The source can survive for
                # diagnostics, but unproved translated candidates cannot render.
                logger.warning(
                    "Shorts Factory RU boundary evidence failed closed later: %s",
                    str(exc)[:240],
                )
            return result

        captured_prepare._mp3bot_factory_ru_boundary_capture = True  # type: ignore[attr-defined]
        factory_pipeline._prepare_translation_video = captured_prepare

    current_persist = factory_pipeline._persist_factory_source
    if not getattr(current_persist, "_mp3bot_factory_ru_boundary_capture", False):

        def captured_persist(source_path: Path, media_id: str) -> Path:
            evidence = _take_video_timeline(source_path)
            destination = current_persist(source_path, media_id)
            _CURRENT_TIMELINE.set(dict(evidence) if evidence is not None else None)
            if evidence is not None:
                logger.info(
                    "Shorts Factory RU boundary evidence bound to %s "
                    "(ru_intervals=%d source_intervals=%d)",
                    destination.name,
                    len(evidence.get("intervals") or []),
                    len(evidence.get("source_speech_intervals") or []),
                )
            return destination

        captured_persist._mp3bot_factory_ru_boundary_capture = True  # type: ignore[attr-defined]
        factory_pipeline._persist_factory_source = captured_persist

    _CAPTURE_INSTALLED = True
    return True


def _candidate_limits(duration: float) -> tuple[float, float, bool]:
    is_long = duration >= LONG_MIN_SEC
    if is_long:
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
    """Return speech coverage ratio and largest gap inside a clip."""
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


def _subtract_intervals(
    base: list[tuple[float, float]],
    cover: list[tuple[float, float]],
    *,
    start: float,
    end: float,
    cover_grace: float,
) -> list[tuple[float, float]]:
    """Return source-speech spans not covered by contemporaneous RU speech."""
    uncovered: list[tuple[float, float]] = []
    for raw_start, raw_end in base:
        left = max(start, raw_start)
        right = min(end, raw_end)
        if right <= left:
            continue
        fragments = [(left, right)]
        for cover_start, cover_end in cover:
            expanded_start = max(start, cover_start - cover_grace)
            expanded_end = min(end, cover_end + cover_grace)
            if expanded_end <= left or expanded_start >= right:
                continue
            next_fragments: list[tuple[float, float]] = []
            for frag_start, frag_end in fragments:
                if expanded_end <= frag_start or expanded_start >= frag_end:
                    next_fragments.append((frag_start, frag_end))
                    continue
                if expanded_start > frag_start:
                    next_fragments.append((frag_start, min(expanded_start, frag_end)))
                if expanded_end < frag_end:
                    next_fragments.append((max(expanded_end, frag_start), frag_end))
            fragments = next_fragments
            if not fragments:
                break
        uncovered.extend(
            (frag_start, frag_end)
            for frag_start, frag_end in fragments
            if frag_end - frag_start >= 0.05
        )
    return _merge_intervals(uncovered, max_gap=0.10)


def _source_without_ru_stats(
    source_intervals: list[tuple[float, float]],
    ru_intervals: list[tuple[float, float]],
    *,
    start: float,
    end: float,
    cover_grace: float,
) -> tuple[float, float]:
    uncovered = _subtract_intervals(
        source_intervals,
        ru_intervals,
        start=start,
        end=end,
        cover_grace=cover_grace,
    )
    if not uncovered:
        return 0.0, 0.0
    total = sum(span_end - span_start for span_start, span_end in uncovered)
    largest = max(span_end - span_start for span_start, span_end in uncovered)
    return total, largest


def align_candidates_to_ru_speech(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
    speech_intervals: list[tuple[float, float]],
    delay_seconds: float,
    source_speech_intervals: list[tuple[float, float]] | None = None,
    source_speech_proof: str = "",
    proof: str = RU_BOUNDARY_PROOF,
) -> list[dict[str, Any]]:
    """Convert original semantic anchors into publication-safe RU ranges."""
    if not candidates:
        return []
    intervals = _merge_intervals(speech_intervals, max_gap=0.10)
    if not intervals:
        raise RuntimeError(
            "Factory translated cuts have no proved Russian speech timeline"
        )

    source_intervals = _merge_intervals(
        list(source_speech_intervals or []),
        max_gap=0.35,
    )
    source_limit = max(0.0, float(source_duration))
    delay = max(0.0, float(delay_seconds))
    max_start_back = _env_float(
        "SHORTS_FACTORY_RU_START_BACK_SEC", 3.0, 0.25, 8.0
    )
    max_start_forward = _env_float(
        "SHORTS_FACTORY_RU_START_FORWARD_SEC", 4.0, 0.25, 10.0
    )
    max_end_forward = _env_float(
        "SHORTS_FACTORY_RU_END_FORWARD_SEC", 4.0, 0.25, 10.0
    )
    max_end_back = _env_float(
        "SHORTS_FACTORY_RU_END_BACK_SEC", 4.0, 0.25, 10.0
    )
    end_pad = _env_float(
        "SHORTS_FACTORY_RU_END_PAD_SEC", 0.08, 0.0, 0.30
    )
    source_cover_grace = _env_float(
        "SHORTS_FACTORY_SOURCE_RU_COVERAGE_GRACE_SEC",
        0.20,
        0.0,
        0.75,
    )

    aligned: list[dict[str, Any]] = []
    rejected: list[str] = []

    for item in copy.deepcopy(candidates):
        semantic_start, semantic_end = _candidate_seconds(item)
        semantic_duration = semantic_end - semantic_start
        if semantic_duration <= 0:
            rejected.append(str(item.get("title") or "invalid"))
            continue

        minimum, public_max, is_long = _candidate_limits(semantic_duration)
        max_internal_gap = _env_float(
            "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_LONG_SEC"
            if is_long
            else "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_SEC",
            12.0 if is_long else 4.0,
            1.0,
            30.0,
        )
        no_source_max_gap = _env_float(
            "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_NO_SOURCE_LONG_SEC"
            if is_long
            else "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_NO_SOURCE_SEC",
            6.0 if is_long else 2.0,
            0.75,
            20.0,
        )
        minimum_coverage = _env_float(
            "SHORTS_FACTORY_RU_MIN_COVERAGE_LONG"
            if is_long
            else "SHORTS_FACTORY_RU_MIN_COVERAGE",
            0.55 if is_long else 0.65,
            0.20,
            0.98,
        )
        max_source_without_ru = _env_float(
            "SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_LONG_SEC"
            if is_long
            else "SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC",
            2.50 if is_long else 1.75,
            0.50,
            8.0,
        )

        # VOT speech is delayed in the actual mix. The semantic anchors belong
        # to the original timeline, so both anchors must move by that exact
        # configured delay before phrase-boundary refinement.
        target_start = semantic_start + delay
        target_end = semantic_end + delay

        start_interval = _find_interval_containing(intervals, target_start)
        if start_interval is not None:
            distance = target_start - start_interval[0]
            render_start = (
                start_interval[0]
                if distance <= max_start_back
                else target_start
            )
        else:
            next_interval = _next_interval(intervals, target_start)
            if (
                next_interval is None
                or next_interval[0] - target_start > max_start_forward
            ):
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
            if (
                previous_interval is None
                or target_end - previous_interval[1] > max_end_back
            ):
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

        effective_gap_limit = (
            max_internal_gap if source_intervals else no_source_max_gap
        )
        if largest_gap > effective_gap_limit + 1e-6:
            rejected.append(str(item.get("title") or "untranslated-ru-gap"))
            continue

        source_uncovered_total = 0.0
        source_uncovered_largest = 0.0
        if source_intervals:
            (
                source_uncovered_total,
                source_uncovered_largest,
            ) = _source_without_ru_stats(
                source_intervals,
                intervals,
                start=render_start,
                end=render_end,
                cover_grace=source_cover_grace,
            )
            if source_uncovered_largest > max_source_without_ru + 1e-6:
                rejected.append(
                    str(item.get("title") or "source-speech-without-ru")
                )
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
        item["livedub_source_speech_proof"] = source_speech_proof or "unavailable"
        item["livedub_source_without_ru_seconds"] = source_uncovered_total
        item["livedub_source_without_ru_max_burst_seconds"] = (
            source_uncovered_largest
        )
        aligned.append(item)

    if rejected:
        logger.warning(
            "Shorts Factory RU boundary alignment rejected %d/%d candidates: %s",
            len(rejected),
            len(candidates),
            ", ".join(rejected[:8]),
        )
    return aligned


def align_factory_livedub_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
) -> list[dict[str, Any]]:
    """Align one candidate group to request-local VOT/source speech evidence."""
    if not candidates:
        return []
    timeline = _CURRENT_TIMELINE.get()
    if not timeline:
        raise RuntimeError(
            "Exact VOT RU boundary proof is unavailable; refusing unverified "
            "original-timeline cuts"
        )
    return align_candidates_to_ru_speech(
        candidates,
        source_duration=source_duration,
        speech_intervals=list(timeline.get("intervals") or []),
        delay_seconds=float(timeline.get("delay_seconds") or 0.0),
        source_speech_intervals=list(
            timeline.get("source_speech_intervals") or []
        ),
        source_speech_proof=str(
            timeline.get("source_speech_proof") or "unavailable"
        ),
        proof=str(timeline.get("proof") or RU_BOUNDARY_PROOF),
    )


__all__ = [
    "PUBLIC_LONG_MAX_SEC",
    "PUBLIC_SHORT_MAX_SEC",
    "RU_BOUNDARY_PROOF",
    "align_candidates_to_ru_speech",
    "align_factory_livedub_candidates",
    "factory_ru_boundary_context",
    "install_factory_ru_boundary_capture",
    "prepare_factory_ru_boundary_evidence",
    "speech_intervals_from_silence_log",
]
