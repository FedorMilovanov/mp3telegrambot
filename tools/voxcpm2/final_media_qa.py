#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed QA for the final upload-ready Dub Studio MP4 files.

The PCM master is not the delivery artifact. AAC encoding can change true peak,
loudness, packet duration and stream start time, so the finished MP4 is measured
again. Reports are always written before a delivery failure is raised.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

LOUDNESS_TOLERANCE_LU = 0.9
TRUE_PEAK_DELIVERY_CEILING_DBTP = -1.0
DURATION_TOLERANCE_SECONDS = 0.10
AV_START_TOLERANCE_SECONDS = 0.05
EXPECTED_SAMPLE_RATE = 48_000
EXPECTED_CHANNELS = 2
EXPECTED_CODEC = "aac"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        tail = (process.stderr or process.stdout or "")[-6000:]
        raise RuntimeError(
            "Команда финального media-QA завершилась с ошибкой:\n"
            + " ".join(command)
            + "\n\n"
            + tail
        )
    return process


def _float(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"Нефинитное значение {field}: {value!r}")
    return result


def _last_json_object(text: str) -> dict[str, Any]:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", str(text or ""), flags=re.S)
    if not matches:
        raise RuntimeError("FFmpeg loudnorm не вернул JSON для конечного MP4.")
    payload = json.loads(matches[-1])
    if not isinstance(payload, dict):
        raise RuntimeError("Некорректный loudnorm JSON конечного MP4.")
    return payload


def probe_media(path: Path) -> dict[str, Any]:
    process = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,sample_rate,channels,bit_rate,duration,start_time:format=duration,start_time",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(process.stdout or "{}")
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        raise RuntimeError(f"ffprobe не вернул потоки конечного файла: {path}")

    audio_stream = next(
        (
            dict(item)
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "audio"
        ),
        None,
    )
    video_stream = next(
        (
            dict(item)
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "video"
        ),
        None,
    )
    if audio_stream is None:
        raise RuntimeError(f"В конечном файле нет читаемой аудиодорожки: {path}")
    if video_stream is None:
        raise RuntimeError(f"В конечном файле нет читаемого видеопотока: {path}")

    format_payload = payload.get("format") if isinstance(payload, dict) else None
    if not isinstance(format_payload, dict):
        raise RuntimeError(f"ffprobe не вернул длительность контейнера: {path}")

    audio_start = _float(audio_stream.get("start_time"), field="audio_start_time")
    video_start = _float(video_stream.get("start_time"), field="video_start_time")
    return {
        "audio_codec_name": str(audio_stream.get("codec_name") or "").casefold(),
        "audio_sample_rate": int(audio_stream.get("sample_rate") or 0),
        "audio_channels": int(audio_stream.get("channels") or 0),
        "audio_bit_rate": int(audio_stream.get("bit_rate") or 0),
        "audio_duration": _float(audio_stream.get("duration"), field="audio_duration"),
        "audio_start_time": audio_start,
        "video_codec_name": str(video_stream.get("codec_name") or "").casefold(),
        "video_start_time": video_start,
        "av_start_delta_seconds": abs(audio_start - video_start),
        "container_start_time": _float(
            format_payload.get("start_time"),
            field="container_start_time",
        ),
        "container_duration": _float(
            format_payload.get("duration"),
            field="container_duration",
        ),
    }


def measure_loudness(
    path: Path,
    *,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, float]:
    process = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    payload = _last_json_object(process.stderr or process.stdout or "")
    return {
        "integrated_lufs": _float(payload.get("input_i"), field="input_i"),
        "true_peak_dbtp": _float(payload.get("input_tp"), field="input_tp"),
        "lra_lu": _float(payload.get("input_lra"), field="input_lra"),
        "threshold_lufs": _float(payload.get("input_thresh"), field="input_thresh"),
    }


def _empty_report(path: Path, source_duration: float) -> dict[str, Any]:
    return {
        "path": str(path),
        "passed": False,
        "media": {},
        "loudness": {},
        "source_duration": float(source_duration),
        "audio_duration_delta_seconds": None,
        "container_duration_delta_seconds": None,
        "av_start_delta_seconds": None,
        "limits": {
            "integrated_target_lufs": None,
            "loudness_tolerance_lu": LOUDNESS_TOLERANCE_LU,
            "true_peak_delivery_ceiling_dbtp": TRUE_PEAK_DELIVERY_CEILING_DBTP,
            "duration_tolerance_seconds": DURATION_TOLERANCE_SECONDS,
            "av_start_tolerance_seconds": AV_START_TOLERANCE_SECONDS,
            "sample_rate": EXPECTED_SAMPLE_RATE,
            "channels": EXPECTED_CHANNELS,
            "codec": EXPECTED_CODEC,
        },
        "failures": [],
    }


def verify_final_file(
    path: Path,
    *,
    source_duration: float,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    report = _empty_report(path, source_duration)
    report["limits"]["integrated_target_lufs"] = float(target_i)

    if not path.is_file() or path.stat().st_size <= 0:
        report["failures"].append("конечный MP4 не создан или пуст")
        return report

    try:
        media = probe_media(path)
    except Exception as exc:
        report["failures"].append(f"ffprobe: {exc}")
        return report
    report["media"] = media

    try:
        loudness = measure_loudness(
            path,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        )
    except Exception as exc:
        report["failures"].append(f"loudness: {exc}")
        return report
    report["loudness"] = loudness

    audio_delta = abs(float(media["audio_duration"]) - float(source_duration))
    container_delta = abs(float(media["container_duration"]) - float(source_duration))
    av_start_delta = float(media["av_start_delta_seconds"])
    report["audio_duration_delta_seconds"] = audio_delta
    report["container_duration_delta_seconds"] = container_delta
    report["av_start_delta_seconds"] = av_start_delta
    failures: list[str] = report["failures"]

    if media["audio_codec_name"] != EXPECTED_CODEC:
        failures.append(f"codec={media['audio_codec_name'] or 'unknown'}, нужен AAC")
    if media["audio_sample_rate"] != EXPECTED_SAMPLE_RATE:
        failures.append(
            f"sample_rate={media['audio_sample_rate']}, нужен {EXPECTED_SAMPLE_RATE}"
        )
    if media["audio_channels"] != EXPECTED_CHANNELS:
        failures.append(f"channels={media['audio_channels']}, нужно stereo")
    if not media["video_codec_name"]:
        failures.append("не определён codec видеопотока")
    if audio_delta > DURATION_TOLERANCE_SECONDS:
        failures.append(
            f"audio duration delta={audio_delta:.3f}s > {DURATION_TOLERANCE_SECONDS:.2f}s"
        )
    if container_delta > DURATION_TOLERANCE_SECONDS:
        failures.append(
            f"container duration delta={container_delta:.3f}s > {DURATION_TOLERANCE_SECONDS:.2f}s"
        )
    if av_start_delta > AV_START_TOLERANCE_SECONDS:
        failures.append(
            f"A/V start delta={av_start_delta:.3f}s > {AV_START_TOLERANCE_SECONDS:.2f}s"
        )

    loudness_error = abs(float(loudness["integrated_lufs"]) - float(target_i))
    if loudness_error > LOUDNESS_TOLERANCE_LU:
        failures.append(
            f"loudness={loudness['integrated_lufs']:.2f} LUFS; "
            f"target={target_i:.2f}±{LOUDNESS_TOLERANCE_LU:.1f}"
        )
    if float(loudness["true_peak_dbtp"]) > TRUE_PEAK_DELIVERY_CEILING_DBTP:
        failures.append(
            f"true peak={loudness['true_peak_dbtp']:.2f} dBTP > "
            f"{TRUE_PEAK_DELIVERY_CEILING_DBTP:.1f} dBTP"
        )

    report["passed"] = not failures
    return report


def _failure_summary(label: str, report: dict[str, Any]) -> str:
    failures = report.get("failures") or ["неизвестная ошибка"]
    return f"{label}: " + "; ".join(str(item) for item in failures)


def verify_final_outputs(
    *,
    source_duration: float,
    mixed_video: Path,
    russian_only_video: Path,
    target_i: float,
    target_lra: float,
    target_tp: float,
    report_path: Path,
) -> dict[str, Any]:
    mixed = verify_final_file(
        mixed_video,
        source_duration=source_duration,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    russian_only = verify_final_file(
        russian_only_video,
        source_duration=source_duration,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    report = {
        "schema_version": "dub-final-media-qa-v3",
        "measurement": "FFmpeg loudnorm / ITU-R BS.1770 / EBU R128",
        "passed": bool(mixed.get("passed") and russian_only.get("passed")),
        "mixed": mixed,
        "russian_only": russian_only,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not report["passed"]:
        summaries: list[str] = []
        if not mixed.get("passed"):
            summaries.append(_failure_summary("mixed", mixed))
        if not russian_only.get("passed"):
            summaries.append(_failure_summary("russian-only", russian_only))
        raise RuntimeError(
            "Конечный media-QA не принят. "
            + " | ".join(summaries)
            + f". Отчёт сохранён: {report_path}"
        )
    return report


__all__ = [
    "AV_START_TOLERANCE_SECONDS",
    "DURATION_TOLERANCE_SECONDS",
    "EXPECTED_CHANNELS",
    "EXPECTED_CODEC",
    "EXPECTED_SAMPLE_RATE",
    "LOUDNESS_TOLERANCE_LU",
    "TRUE_PEAK_DELIVERY_CEILING_DBTP",
    "measure_loudness",
    "probe_media",
    "verify_final_file",
    "verify_final_outputs",
]
