#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed QA for the final upload-ready Dub Studio MP4 files.

The PCM master is not the delivery artifact. AAC encoding can change true peak,
loudness and packet duration, so the finished MP4 is measured again with the
same FFmpeg BS.1770/EBU-R128 implementation that produced the master.
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
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,bit_rate,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(process.stdout or "{}")
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise RuntimeError(f"В конечном файле нет читаемой аудиодорожки: {path}")
    stream = dict(streams[0])
    format_payload = payload.get("format") if isinstance(payload, dict) else {}
    format_duration = (
        format_payload.get("duration")
        if isinstance(format_payload, dict)
        else None
    )
    stream_duration = stream.get("duration")
    duration = _float(
        stream_duration if stream_duration not in (None, "N/A") else format_duration,
        field="duration",
    )
    return {
        "codec_name": str(stream.get("codec_name") or "").casefold(),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "bit_rate": int(stream.get("bit_rate") or 0),
        "duration": duration,
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
            (
                f"loudnorm=I={target_i}:LRA={target_lra}:"
                f"TP={target_tp}:print_format=json"
            ),
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


def verify_final_file(
    path: Path,
    *,
    source_duration: float,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Конечный MP4 не создан или пуст: {path}")

    media = probe_media(path)
    loudness = measure_loudness(
        path,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    duration_delta = abs(float(media["duration"]) - float(source_duration))
    failures: list[str] = []

    if media["codec_name"] != EXPECTED_CODEC:
        failures.append(f"codec={media['codec_name'] or 'unknown'}, нужен AAC")
    if media["sample_rate"] != EXPECTED_SAMPLE_RATE:
        failures.append(
            f"sample_rate={media['sample_rate']}, нужен {EXPECTED_SAMPLE_RATE}"
        )
    if media["channels"] != EXPECTED_CHANNELS:
        failures.append(f"channels={media['channels']}, нужно stereo")
    if duration_delta > DURATION_TOLERANCE_SECONDS:
        failures.append(
            f"duration delta={duration_delta:.3f}s > {DURATION_TOLERANCE_SECONDS:.2f}s"
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

    report = {
        "path": str(path),
        "passed": not failures,
        "media": media,
        "loudness": loudness,
        "source_duration": float(source_duration),
        "duration_delta_seconds": duration_delta,
        "limits": {
            "integrated_target_lufs": float(target_i),
            "loudness_tolerance_lu": LOUDNESS_TOLERANCE_LU,
            "true_peak_delivery_ceiling_dbtp": TRUE_PEAK_DELIVERY_CEILING_DBTP,
            "duration_tolerance_seconds": DURATION_TOLERANCE_SECONDS,
            "sample_rate": EXPECTED_SAMPLE_RATE,
            "channels": EXPECTED_CHANNELS,
            "codec": EXPECTED_CODEC,
        },
        "failures": failures,
    }
    if failures:
        raise RuntimeError(
            f"Конечный media-QA не принят для {path.name}: " + "; ".join(failures)
        )
    return report


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
    report = {
        "schema_version": "dub-final-media-qa-v1",
        "measurement": "FFmpeg loudnorm / ITU-R BS.1770 / EBU R128",
        "mixed": verify_final_file(
            mixed_video,
            source_duration=source_duration,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        ),
        "russian_only": verify_final_file(
            russian_only_video,
            source_duration=source_duration,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


__all__ = [
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
