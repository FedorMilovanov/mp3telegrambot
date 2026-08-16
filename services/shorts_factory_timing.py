#!/usr/bin/env python3
"""Speech-proven render boundaries for translated SHORTS FACTORY cuts.

Gemini chooses semantic candidates from the original source. For a foreign-
language source those timestamps are discovery anchors, not publication
boundaries. This module derives exact Russian-speech evidence from the
provenance-bound Yandex VOT audio, optionally adds provider-caption evidence for
source-language speech, and refines every translated candidate on the real
final-mix timeline.

Evidence is passed explicitly by the Factory composition owner. There is no
ambient timeline state and no fallback to unverified original-language timestamps.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from services.async_process import run_cancellable_process
from services.livedub_mix import get_mix_params
from services.livedub_ru_provenance import read_ru_audio_provenance

logger = logging.getLogger(__name__)

PUBLIC_SHORT_MAX_SEC = 180.0
PUBLIC_LONG_MAX_SEC = 900.0
SHORT_MIN_SEC = 35.0
LONG_MIN_SEC = 300.0
RU_BOUNDARY_PROOF = "exact-vot-ru-plus-source-speech-v4"
RU_ONLY_BOUNDARY_PROOF = "exact-vot-ru-silencedetect-v4"
CandidateKind = Literal["short", "long"]

_STAGE_ONLY_WORDS = {
    "applause",
    "applauding",
    "cheering",
    "cheers",
    "crowd",
    "laugh",
    "laughing",
    "laughter",
    "music",
    "музыка",
    "аплодисменты",
    "смех",
}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Read one finite bounded Factory tuning value without changing its meaning."""
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(float(minimum), min(value, float(maximum)))


def _env_quality_min(name: str, default: float, minimum: float, maximum: float) -> float:
    """For minimum-quality thresholds, permit only equal or stricter (higher) values."""
    return max(float(default), _env_float(name, default, minimum, maximum))


def _env_quality_max(name: str, default: float, minimum: float, maximum: float) -> float:
    """For maximum tolerances, permit only equal or stricter (lower) values."""
    return min(float(default), _env_float(name, default, minimum, maximum))


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


def _strip_simple_html(text: str) -> str:
    out: list[str] = []
    in_tag = False
    for char in str(text or ""):
        if char == "<":
            in_tag = True
            continue
        if char == ">" and in_tag:
            in_tag = False
            continue
        if not in_tag:
            out.append(char)
    return "".join(out)


def _caption_cue_is_lexical_speech(text: str) -> bool:
    cleaned = " ".join(_strip_simple_html(text).replace("\x00", " ").split()).strip()
    if not cleaned:
        return False
    without_notes = cleaned.replace("♪", " ").replace("♫", " ").strip()
    if not any(char.isalnum() for char in without_notes):
        return False

    candidate = without_notes.casefold().strip()
    wrappers = (("[", "]"), ("{", "}"), ("(", ")"))
    for left, right in wrappers:
        if candidate.startswith(left) and candidate.endswith(right):
            inner = candidate[1:-1].strip(" .!?:;-—–_\t")
            words = [
                "".join(char for char in token if char.isalpha())
                for token in inner.split()
            ]
            words = [word for word in words if word]
            if words and all(word in _STAGE_ONLY_WORDS for word in words):
                return False
    return True


def _silence_event_value(line: str, marker: str) -> float | None:
    position = str(line or "").find(marker)
    if position < 0:
        return None
    tail = str(line)[position + len(marker) :].lstrip()
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
    try:
        limit = max(0.0, float(duration))
    except (TypeError, ValueError):
        return []
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
    clean: list[tuple[float, float]] = []
    for raw_start, raw_end in intervals:
        try:
            start = max(0.0, float(raw_start))
            end = max(0.0, float(raw_end))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        clean.append((start, end))
    clean.sort(key=lambda pair: pair[0])

    merged: list[tuple[float, float]] = []
    for start, end in clean:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


async def _probe_audio_duration(path: Path) -> float:
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


async def _detect_exact_ru_speech(ru_audio_path: Path) -> dict[str, Any]:
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
        [
            (float(cue.start), float(cue.end))
            for cue in cues
            if _caption_cue_is_lexical_speech(getattr(cue, "text", ""))
        ],
        max_gap=0.35,
    )
    if not intervals:
        raise RuntimeError("provider source captions contain no usable lexical speech intervals")
    return intervals, "provider-source-srt"


async def prepare_factory_ru_boundary_evidence(
    *,
    url: str,
    workdir: Path,
    source_language: str,
) -> dict[str, Any]:
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
            "using RU-only gap evidence: %s",
            str(exc)[:220],
        )

    proof = RU_BOUNDARY_PROOF if source_intervals else RU_ONLY_BOUNDARY_PROOF
    return {
        **ru_evidence,
        "proof": proof,
        "source_speech_intervals": source_intervals,
        "source_speech_proof": source_proof,
    }


def _candidate_limits(candidate_kind: CandidateKind) -> tuple[float, float, bool]:
    if candidate_kind == "long":
        return LONG_MIN_SEC, PUBLIC_LONG_MAX_SEC, True
    if candidate_kind == "short":
        return SHORT_MIN_SEC, PUBLIC_SHORT_MAX_SEC, False
    raise ValueError(f"unsupported Factory candidate kind: {candidate_kind!r}")


def _find_interval_containing(intervals: list[tuple[float, float]], point: float) -> tuple[float, float] | None:
    for start, end in intervals:
        if start <= point <= end:
            return start, end
    return None


def _next_interval(intervals: list[tuple[float, float]], point: float) -> tuple[float, float] | None:
    for start, end in intervals:
        if start >= point:
            return start, end
    return None


def _previous_interval(intervals: list[tuple[float, float]], point: float) -> tuple[float, float] | None:
    previous = None
    for start, end in intervals:
        if end <= point:
            previous = (start, end)
        else:
            break
    return previous


def _clip_speech_stats(intervals: list[tuple[float, float]], start: float, end: float) -> tuple[float, float]:
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


def _subtract_intervals(base, cover, *, start: float, end: float, cover_grace: float):
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
            next_fragments = []
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
        uncovered.extend((a, b) for a, b in fragments if b - a >= 0.05)
    return _merge_intervals(uncovered, max_gap=0.10)


def _source_without_ru_stats(source_intervals, ru_intervals, *, start, end, cover_grace):
    uncovered = _subtract_intervals(source_intervals, ru_intervals, start=start, end=end, cover_grace=cover_grace)
    if not uncovered:
        return 0.0, 0.0, []
    total = sum(b - a for a, b in uncovered)
    largest = max(b - a for a, b in uncovered)
    return total, largest, uncovered


def _edge_uncovered_max(spans, *, start, end, edge_window):
    windows = [(start, min(end, start + edge_window)), (max(start, end - edge_window), end)]
    largest = 0.0
    for a, b in spans:
        for c, d in windows:
            largest = max(largest, min(b, d) - max(a, c))
    return max(0.0, largest)


def _reclaim_public_limit(*, render_start, render_end, target_start, target_end, public_max):
    overflow = (render_end - render_start) - public_max
    if overflow <= 1e-6:
        return render_start, render_end
    reclaimable_right = max(0.0, render_end - max(render_start, target_end))
    take = min(overflow, reclaimable_right)
    render_end -= take
    overflow -= take
    reclaimable_left = max(0.0, min(target_start, render_end) - render_start)
    take = min(overflow, reclaimable_left)
    render_start += take
    overflow -= take
    if overflow > 1e-6 or render_end <= render_start:
        return None
    return render_start, render_end


def align_candidates_to_ru_speech(
    candidates: list[dict[str, Any]], *, source_duration: int | float,
    speech_intervals: list[tuple[float, float]], delay_seconds: float,
    source_speech_intervals: list[tuple[float, float]] | None = None,
    source_speech_proof: str = "", proof: str | None = None,
    candidate_kind: CandidateKind = "short",
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    minimum, public_max, is_long = _candidate_limits(candidate_kind)
    intervals = _merge_intervals(speech_intervals, max_gap=0.10)
    if not intervals:
        raise RuntimeError("Factory translated cuts have no proved Russian speech timeline")
    source_intervals = _merge_intervals(list(source_speech_intervals or []), max_gap=0.35)
    effective_proof = proof or (RU_BOUNDARY_PROOF if source_intervals else RU_ONLY_BOUNDARY_PROOF)
    source_limit = float(source_duration)
    if not math.isfinite(source_limit) or source_limit <= 0:
        raise RuntimeError("Factory translated source duration is not finite/positive")
    delay = max(0.0, float(delay_seconds)) if math.isfinite(float(delay_seconds)) else 0.0
    max_start_back = _env_quality_max("SHORTS_FACTORY_RU_START_BACK_SEC", 3.0, 0.25, 8.0)
    max_start_forward = _env_quality_max("SHORTS_FACTORY_RU_START_FORWARD_SEC", 4.0, 0.25, 10.0)
    max_end_forward = _env_quality_max("SHORTS_FACTORY_RU_END_FORWARD_SEC", 4.0, 0.25, 10.0)
    max_end_back = _env_quality_max("SHORTS_FACTORY_RU_END_BACK_SEC", 4.0, 0.25, 10.0)
    end_pad = _env_float("SHORTS_FACTORY_RU_END_PAD_SEC", 0.08, 0.0, 0.30)
    source_cover_grace = _env_quality_max("SHORTS_FACTORY_SOURCE_RU_COVERAGE_GRACE_SEC", 0.20, 0.0, 0.75)
    edge_window = _env_quality_min("SHORTS_FACTORY_SOURCE_EDGE_WINDOW_SEC", 2.0, 0.5, 5.0)
    no_source_max_gap = _env_quality_max("SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_NO_SOURCE_LONG_SEC" if is_long else "SHORTS_FACTORY_RU_MAX_INTERNAL_GAP_NO_SOURCE_SEC", 12.0 if is_long else 4.5, 1.0, 30.0)
    minimum_coverage = _env_quality_min("SHORTS_FACTORY_RU_MIN_COVERAGE_LONG" if is_long else "SHORTS_FACTORY_RU_MIN_COVERAGE", 0.30 if is_long else 0.45, 0.15, 0.98)
    max_source_without_ru = _env_quality_max("SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_LONG_SEC" if is_long else "SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC", 8.0 if is_long else 4.0, 1.0, 20.0)
    configured_edge_limit = _env_quality_max("SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_EDGE_LONG_SEC" if is_long else "SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_EDGE_SEC", 1.50 if is_long else 1.25, 0.50, 4.0)
    edge_source_without_ru = max(configured_edge_limit, delay + source_cover_grace + 0.15)
    aligned, rejected = [], []
    for item in copy.deepcopy(candidates):
        semantic_start, semantic_end = _candidate_seconds(item)
        semantic_duration = semantic_end - semantic_start
        if semantic_duration <= 0 or semantic_duration > public_max + 1e-6:
            rejected.append(str(item.get("title") or "invalid-or-overlong")); continue
        target_start, target_end = semantic_start + delay, semantic_end + delay
        start_interval = _find_interval_containing(intervals, target_start)
        if start_interval:
            render_start = start_interval[0] if target_start - start_interval[0] <= max_start_back else target_start
        else:
            next_interval = _next_interval(intervals, target_start)
            if next_interval is None or next_interval[0] - target_start > max_start_forward:
                rejected.append(str(item.get("title") or "start-no-ru-speech")); continue
            render_start = next_interval[0]
        end_interval = _find_interval_containing(intervals, target_end)
        if end_interval:
            render_end = end_interval[1] + end_pad if end_interval[1] - target_end <= max_end_forward else target_end
        else:
            previous_interval = _previous_interval(intervals, target_end)
            if previous_interval is None or target_end - previous_interval[1] > max_end_back:
                rejected.append(str(item.get("title") or "end-no-ru-speech")); continue
            render_end = previous_interval[1] + end_pad
        render_start, render_end = max(0.0, min(source_limit, render_start)), max(0.0, min(source_limit, render_end))
        reclaimed = _reclaim_public_limit(render_start=render_start, render_end=render_end, target_start=target_start, target_end=target_end, public_max=public_max)
        if reclaimed is None:
            rejected.append(str(item.get("title") or "too-long-after-ru-align")); continue
        render_start, render_end = reclaimed
        rendered_duration = render_end - render_start
        if rendered_duration < minimum - 1e-6:
            rejected.append(str(item.get("title") or "too-short-after-ru-align")); continue
        coverage, largest_gap = _clip_speech_stats(intervals, render_start, render_end)
        if coverage + 1e-6 < minimum_coverage:
            rejected.append(str(item.get("title") or "low-ru-speech-coverage")); continue
        source_uncovered_total = source_uncovered_largest = source_uncovered_edge = 0.0
        if source_intervals:
            source_uncovered_total, source_uncovered_largest, uncovered_spans = _source_without_ru_stats(source_intervals, intervals, start=render_start, end=render_end, cover_grace=source_cover_grace)
            source_uncovered_edge = _edge_uncovered_max(uncovered_spans, start=render_start, end=render_end, edge_window=edge_window)
            if source_uncovered_edge > edge_source_without_ru + 1e-6:
                rejected.append(str(item.get("title") or "source-edge-without-ru")); continue
            if source_uncovered_largest > max_source_without_ru + 1e-6:
                rejected.append(str(item.get("title") or "source-speech-without-ru")); continue
        elif largest_gap > no_source_max_gap + 1e-6:
            rejected.append(str(item.get("title") or "untranslated-ru-gap")); continue
        item.update({
            "start_seconds": render_start, "end_seconds": render_end,
            "duration_seconds": rendered_duration, "start": _format_seconds(render_start),
            "end": _format_seconds(render_end), "livedub_semantic_start_seconds": semantic_start,
            "livedub_semantic_end_seconds": semantic_end, "livedub_ru_target_start_seconds": target_start,
            "livedub_ru_target_end_seconds": target_end, "livedub_ru_boundary_proof": effective_proof,
            "livedub_ru_start_shift_seconds": render_start - semantic_start,
            "livedub_ru_end_shift_seconds": render_end - semantic_end,
            "livedub_ru_speech_coverage": coverage, "livedub_ru_max_internal_gap_seconds": largest_gap,
            "livedub_source_speech_proof": source_speech_proof or "unavailable",
            "livedub_source_without_ru_seconds": source_uncovered_total,
            "livedub_source_without_ru_max_burst_seconds": source_uncovered_largest,
            "livedub_source_without_ru_edge_seconds": source_uncovered_edge,
            "livedub_candidate_kind": candidate_kind,
        })
        aligned.append(item)
    if rejected:
        logger.warning("Shorts Factory RU boundary alignment rejected %d/%d %s candidates: %s", len(rejected), len(candidates), candidate_kind, ", ".join(rejected[:8]))
    return aligned


def align_factory_livedub_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_duration: int | float,
    evidence: dict[str, Any],
    candidate_kind: CandidateKind = "short",
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    timeline = dict(evidence or {})
    if not timeline:
        raise RuntimeError(
            "Exact VOT RU boundary proof is unavailable; "
            "refusing unverified original-timeline cuts"
        )
    return align_candidates_to_ru_speech(
        candidates,
        source_duration=source_duration,
        speech_intervals=list(timeline.get("intervals") or []),
        delay_seconds=float(timeline.get("delay_seconds") or 0.0),
        source_speech_intervals=list(timeline.get("source_speech_intervals") or []),
        source_speech_proof=str(timeline.get("source_speech_proof") or "unavailable"),
        proof=str(timeline.get("proof") or RU_ONLY_BOUNDARY_PROOF),
        candidate_kind=candidate_kind,
    )


__all__ = [
    "PUBLIC_LONG_MAX_SEC", "PUBLIC_SHORT_MAX_SEC", "RU_BOUNDARY_PROOF",
    "RU_ONLY_BOUNDARY_PROOF", "align_candidates_to_ru_speech",
    "align_factory_livedub_candidates",
    "prepare_factory_ru_boundary_evidence", "speech_intervals_from_silence_log",
]
