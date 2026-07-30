#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-AAC cadence and synthesis-tail QA for the final Russian-only MP4.

The renderer already checks raw candidates and the assembled PCM timeline. This
module closes the final release gap by decoding only the last SRT segment from
the encoded Russian-only MP4 and applying the same deterministic cadence and
late-broadband-tail contracts. Work and memory stay bounded for hour-long video.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2.direct_russian_cadence import (
    evaluate_candidate_cadence,
    prosody_contour,
)
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail

POLICY = "post-aac-russian-delivery-v1"
SAMPLE_RATE = 48_000
MAX_SEGMENT_WINDOW_SECONDS = 30.0


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"Нефинитное значение {field}: {value!r}")
    return result


def _last_segment(segments_path: Path) -> dict[str, Any]:
    if not segments_path.is_file():
        raise RuntimeError(f"Не найден segments_ru_final.json: {segments_path}")
    try:
        payload = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не читается segments_ru_final.json: {segments_path}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или повреждён.")
    candidates = [dict(item) for item in payload if isinstance(item, dict)]
    if len(candidates) != len(payload):
        raise RuntimeError("segments_ru_final.json содержит повреждённые записи.")

    def effective_start(item: dict[str, Any]) -> float:
        delay = max(0.0, _finite(item.get("start_delay_ms", 0), field="start_delay_ms") / 1000.0)
        return _finite(item.get("start"), field="start") + delay

    return max(candidates, key=effective_start)


def _decode_segment(
    video: Path,
    *,
    start: float,
    duration: float,
) -> np.ndarray:
    if not video.is_file() or video.stat().st_size <= 0:
        raise RuntimeError(f"Не найден финальный Russian-only MP4: {video}")
    duration = min(MAX_SEGMENT_WINDOW_SECONDS, max(0.35, float(duration)))
    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start):.6f}",
            "-i",
            str(video),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-t",
            f"{duration:.6f}",
            "-f",
            "f32le",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(60.0, duration * 8.0 + 30.0),
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or b"")[-6000:].decode("utf-8", errors="replace")
        raise RuntimeError("FFmpeg post-AAC delivery decode завершился с ошибкой:\n" + detail)
    audio = np.frombuffer(process.stdout or b"", dtype="<f4").astype(np.float32, copy=True)
    if len(audio) < int(SAMPLE_RATE * 0.25):
        raise RuntimeError("Post-AAC delivery QA получил слишком короткое аудио.")
    if not np.isfinite(audio).all():
        raise RuntimeError("Post-AAC delivery QA получил NaN/Inf.")
    return audio


def evaluate_encoded_segment(
    samples: Any,
    sample_rate: int,
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate already encoded audio without changing or repairing it."""
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.reshape(-1)
    rate = max(1, int(sample_rate))
    contour = prosody_contour(audio, rate)
    active_duration = max(
        0.0,
        _finite(contour.get("active_end", 0.0), field="active_end")
        - _finite(contour.get("active_start", 0.0), field="active_start"),
    )
    cadence = evaluate_candidate_cadence(
        {
            "samples": audio,
            "sample_rate": rate,
            "duration": active_duration,
        },
        segment,
    )
    tail = detect_late_broadband_tail(audio, rate)
    failures = list(cadence.get("failures") or [])
    if tail.get("suspicious"):
        failures.append(str(tail.get("artifact_type") or "late_tail"))
    return {
        "policy": POLICY,
        "segment_id": int(segment.get("id") or 0),
        "text": str(segment.get("text") or ""),
        "sample_rate": rate,
        "decoded_seconds": len(audio) / rate,
        "active_speech_seconds": active_duration,
        "cadence": cadence,
        "late_tail": tail,
        "failures": failures,
        "passed": bool(cadence.get("hard_ok") and not tail.get("suspicious")),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _attach_to_final_report(
    final_report_path: Path,
    delivery: dict[str, Any],
) -> None:
    if not final_report_path.is_file():
        return
    try:
        payload = json.loads(final_report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    payload["encoded_russian_delivery"] = delivery
    payload["encoded_russian_delivery_policy"] = POLICY
    payload["passed"] = bool(payload.get("passed") and delivery.get("passed"))
    _write_json(final_report_path, payload)


def verify_final_encoded_russian(
    *,
    russian_only_video: Path,
    segments_path: Path,
    report_path: Path,
    final_media_report_path: Path | None = None,
) -> dict[str, Any]:
    """Decode only the final SRT window, persist evidence and fail closed."""
    segment = _last_segment(segments_path)
    start = _finite(segment.get("start"), field="last_segment.start")
    end = _finite(segment.get("end"), field="last_segment.end")
    delay = max(
        0.0,
        _finite(segment.get("start_delay_ms", 0), field="last_segment.start_delay_ms") / 1000.0,
    )
    duration = end - start
    if start < 0.0 or duration <= 0.0 or duration > MAX_SEGMENT_WINDOW_SECONDS:
        raise RuntimeError(
            f"Некорректное окно последней реплики: start={start:.3f}, duration={duration:.3f}."
        )
    audio = _decode_segment(
        russian_only_video,
        start=start + delay,
        duration=duration,
    )
    delivery = evaluate_encoded_segment(audio, SAMPLE_RATE, segment)
    delivery.update(
        video=str(russian_only_video),
        segments_path=str(segments_path),
        decoded_start_seconds=start + delay,
        requested_window_seconds=duration,
    )
    _write_json(report_path, delivery)
    if final_media_report_path is not None:
        _attach_to_final_report(final_media_report_path, delivery)
    if not delivery["passed"]:
        reasons = ",".join(str(item) for item in delivery.get("failures") or [])
        cadence = delivery.get("cadence") or {}
        raise RuntimeError(
            "Финальная AAC-дорожка не прошла ending/tail QA: "
            f"segment #{delivery['segment_id']}; reasons={reasons or 'delivery_qa'}; "
            f"ending={float(cadence.get('ending_delta_semitones') or 0.0):.2f}st. "
            f"Отчёт: {report_path}"
        )
    return delivery


__all__ = [
    "MAX_SEGMENT_WINDOW_SECONDS",
    "POLICY",
    "SAMPLE_RATE",
    "evaluate_encoded_segment",
    "verify_final_encoded_russian",
]
