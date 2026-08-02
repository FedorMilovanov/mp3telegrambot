#!/usr/bin/env python3
"""Final media timing, size and silence evidence for public video delivery."""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaProbe:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    audio_sample_rate: int = 0
    audio_codec: str = ""
    has_video: bool = False
    has_audio: bool = False
    size_mb: float = 0.0


@dataclass(frozen=True)
class DeliveryTiming:
    source_start: float
    source_end: float
    raw_duration: float
    delivery_duration: float
    speed_applied: bool


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _positive_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def file_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _probe_from_payload(path: Path, payload: dict[str, Any]) -> MediaProbe:
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        {},
    )
    format_payload = payload.get("format") if isinstance(payload, dict) else {}
    format_payload = format_payload if isinstance(format_payload, dict) else {}
    duration = _finite_float(format_payload.get("duration"))
    if duration <= 0:
        duration = max(
            (_finite_float(item.get("duration")) for item in streams if isinstance(item, dict)),
            default=0.0,
        )
    return MediaProbe(
        duration=max(0.0, duration),
        width=_positive_int(video.get("width")),
        height=_positive_int(video.get("height")),
        audio_sample_rate=_positive_int(audio.get("sample_rate")),
        audio_codec=str(audio.get("codec_name") or "").strip().lower(),
        has_video=bool(video),
        has_audio=bool(audio),
        size_mb=file_size_mb(path),
    )


def probe_media(path: Path, *, timeout: int = 20) -> MediaProbe | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return None
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,duration,width,height,sample_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if process.returncode != 0:
            return None
        payload = json.loads(process.stdout or "{}")
        if not isinstance(payload, dict):
            return None
        return _probe_from_payload(path, payload)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


async def probe_media_async(path: Path, *, timeout: int = 20) -> MediaProbe | None:
    return await asyncio.to_thread(probe_media, path, timeout=timeout)


def resolve_delivery_timing(
    *,
    source_start: float,
    raw_duration: float,
    source_duration: float = 0.0,
    speed: float = 1.0,
    speed_applied: bool = False,
    final_duration: float = 0.0,
) -> DeliveryTiming:
    start = max(0.0, _finite_float(source_start))
    raw = max(0.001, _finite_float(raw_duration, 0.001))
    source_end = start + raw
    source_limit = _finite_float(source_duration)
    if source_limit > 0:
        source_end = min(source_limit, source_end)
    speed_value = max(0.01, _finite_float(speed, 1.0))
    final = _finite_float(final_duration)
    if final <= 0:
        final = raw / speed_value if speed_applied else raw
    return DeliveryTiming(
        source_start=round(start, 3),
        source_end=round(max(start, source_end), 3),
        raw_duration=round(raw, 3),
        delivery_duration=round(max(0.001, final), 3),
        speed_applied=bool(speed_applied),
    )


def parse_silencedetect(stderr: str, *, duration: float) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    pending: float | None = None
    for line in str(stderr or "").splitlines():
        if "silence_start:" in line:
            token = line.rsplit("silence_start:", 1)[1].strip().split()[0]
            value = _finite_float(token, -1.0)
            pending = value if value >= 0 else pending
            continue
        if "silence_end:" not in line:
            continue
        token = line.rsplit("silence_end:", 1)[1].strip().split()[0]
        end = _finite_float(token, -1.0)
        if pending is not None and end > pending:
            intervals.append((pending, end))
        pending = None
    if pending is not None and duration > pending:
        intervals.append((pending, duration))

    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 0.08:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def evaluate_highlights_delivery(
    probe: MediaProbe | None,
    silence_intervals: list[tuple[float, float]],
    *,
    expected_duration: float,
    max_internal_silence: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    if probe is None:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": ["ffprobe_unavailable_or_invalid"],
        }

    if not probe.has_video:
        reasons.append("video_stream_missing")
    if not probe.has_audio:
        reasons.append("audio_stream_missing")
    if probe.width != 720 or probe.height != 1280:
        reasons.append("unexpected_dimensions")
    if probe.audio_sample_rate != 48000:
        reasons.append("unexpected_audio_sample_rate")
    if probe.audio_codec != "aac":
        reasons.append("unexpected_audio_codec")

    expected = max(0.0, _finite_float(expected_duration))
    tolerance = max(0.75, expected * 0.015)
    duration_delta = abs(probe.duration - expected) if expected > 0 else 0.0
    if expected > 0 and duration_delta > tolerance:
        reasons.append("duration_mismatch")

    bad_silences = []
    for start, end in silence_intervals:
        silence_duration = max(0.0, end - start)
        touches_edge = start <= 0.35 or end >= probe.duration - 0.35
        if touches_edge and silence_duration <= 0.55:
            continue
        if silence_duration >= max(0.0, max_internal_silence - 0.02):
            bad_silences.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(silence_duration, 3),
                }
            )
    if bad_silences:
        reasons.append("long_internal_silence")

    return {
        "policy": "final-render-highlights-delivery-v2",
        "accepted": not reasons,
        "reasons": reasons,
        "probe": asdict(probe),
        "expected_duration": round(expected, 3),
        "duration_delta": round(duration_delta, 3),
        "duration_tolerance": round(tolerance, 3),
        "max_internal_silence": round(max_internal_silence, 3),
        "silence_intervals": [
            {"start": start, "end": end, "duration": round(end - start, 3)}
            for start, end in silence_intervals
        ],
        "bad_silences": bad_silences,
    }


async def verify_highlights_delivery(
    path: Path,
    *,
    expected_duration: float,
) -> dict[str, Any]:
    probe = await probe_media_async(path)
    if probe is None:
        return evaluate_highlights_delivery(
            None,
            [],
            expected_duration=expected_duration,
            max_internal_silence=2.8,
        )

    try:
        max_silence = float(os.getenv("HIGHLIGHTS_FINAL_MAX_SILENCE_SECONDS", "2.8"))
    except ValueError:
        max_silence = 2.8
    max_silence = min(6.0, max(1.2, max_silence))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": ["ffmpeg_unavailable"],
            "probe": asdict(probe),
        }
    command = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise=-38dB:d={max_silence:.3f}",
        "-f",
        "null",
        "-",
    ]
    try:
        process = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": [f"silence_probe_error:{type(exc).__name__}"],
            "probe": asdict(probe),
        }
    if process.returncode != 0:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": ["silence_probe_failed"],
            "probe": asdict(probe),
            "stderr_tail": (process.stderr or "")[-800:],
        }
    intervals = parse_silencedetect(process.stderr, duration=probe.duration)
    return evaluate_highlights_delivery(
        probe,
        intervals,
        expected_duration=expected_duration,
        max_internal_silence=max_silence,
    )


__all__ = [
    "DeliveryTiming",
    "MediaProbe",
    "evaluate_highlights_delivery",
    "file_size_mb",
    "parse_silencedetect",
    "probe_media",
    "probe_media_async",
    "resolve_delivery_timing",
    "verify_highlights_delivery",
]
