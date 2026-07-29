#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed QA for the final upload-ready Dub Studio MP4 files."""
from __future__ import annotations

import json
import math
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
TARGET_I_RANGE = (-70.0, -5.0)
TARGET_LRA_RANGE = (1.0, 50.0)
TARGET_TP_RANGE = (-9.0, 0.0)


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


def _range(value: Any, *, field: str, limits: tuple[float, float]) -> float:
    result = _float(value, field=field)
    low, high = limits
    if not low <= result <= high:
        raise RuntimeError(f"{field}={result} вне диапазона {low}..{high}")
    return result


def _contract_values(
    *,
    source_duration: Any,
    target_i: Any,
    target_lra: Any,
    target_tp: Any,
) -> dict[str, float]:
    duration = _float(source_duration, field="source_duration")
    if duration <= 0.0:
        raise RuntimeError("source_duration должен быть > 0")
    return {
        "source_duration": duration,
        "target_i": _range(target_i, field="target_i", limits=TARGET_I_RANGE),
        "target_lra": _range(target_lra, field="target_lra", limits=TARGET_LRA_RANGE),
        "target_tp": _range(target_tp, field="target_tp", limits=TARGET_TP_RANGE),
    }


def _report_value(value: Any) -> float | str | None:
    if value is None:
        return None
    try:
        return _float(value, field="report_value")
    except RuntimeError:
        return repr(value)


def _last_json_object(text: str) -> dict[str, Any]:
    """Read the final loudnorm object without assuming a flat regex shape."""
    source = str(text or "")
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, char in enumerate(source):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(source[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "input_i" in payload:
            candidates.append(payload)
    if not candidates:
        raise RuntimeError("FFmpeg loudnorm не вернул JSON для конечного MP4.")
    return candidates[-1]


def probe_media(path: Path) -> dict[str, Any]:
    process = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,sample_rate,channels,bit_rate,duration,start_time:format=duration,start_time",
            "-of", "json", str(path),
        ]
    )
    payload = json.loads(process.stdout or "{}")
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        raise RuntimeError(f"ffprobe не вернул потоки конечного файла: {path}")
    audio = next(
        (dict(item) for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        None,
    )
    video = next(
        (dict(item) for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    if audio is None:
        raise RuntimeError(f"В конечном файле нет читаемой аудиодорожки: {path}")
    if video is None:
        raise RuntimeError(f"В конечном файле нет читаемого видеопотока: {path}")
    format_payload = payload.get("format") if isinstance(payload, dict) else None
    if not isinstance(format_payload, dict):
        raise RuntimeError(f"ffprobe не вернул длительность контейнера: {path}")
    audio_start = _float(audio.get("start_time"), field="audio_start_time")
    video_start = _float(video.get("start_time"), field="video_start_time")
    return {
        "audio_codec_name": str(audio.get("codec_name") or "").casefold(),
        "audio_sample_rate": int(audio.get("sample_rate") or 0),
        "audio_channels": int(audio.get("channels") or 0),
        "audio_bit_rate": int(audio.get("bit_rate") or 0),
        "audio_duration": _float(audio.get("duration"), field="audio_duration"),
        "audio_start_time": audio_start,
        "video_codec_name": str(video.get("codec_name") or "").casefold(),
        "video_start_time": video_start,
        "av_start_delta_seconds": abs(audio_start - video_start),
        "container_start_time": _float(format_payload.get("start_time"), field="container_start_time"),
        "container_duration": _float(format_payload.get("duration"), field="container_duration"),
    }


def measure_loudness(
    path: Path,
    *,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, float]:
    contract = _contract_values(
        source_duration=1.0,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    process = _run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-map", "0:a:0", "-af",
            f"loudnorm=I={contract['target_i']}:LRA={contract['target_lra']}:TP={contract['target_tp']}:print_format=json",
            "-f", "null", "-",
        ]
    )
    payload = _last_json_object(process.stderr or process.stdout or "")
    return {
        "integrated_lufs": _float(payload.get("input_i"), field="input_i"),
        "true_peak_dbtp": _float(payload.get("input_tp"), field="input_tp"),
        "lra_lu": _float(payload.get("input_lra"), field="input_lra"),
        "threshold_lufs": _float(payload.get("input_thresh"), field="input_thresh"),
    }


def _empty_report(path: Path, source_duration: Any) -> dict[str, Any]:
    return {
        "path": str(path),
        "passed": False,
        "media": {},
        "loudness": {},
        "source_duration": _report_value(source_duration),
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
            "target_i_range": list(TARGET_I_RANGE),
            "target_lra_range": list(TARGET_LRA_RANGE),
            "target_tp_range": list(TARGET_TP_RANGE),
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
    try:
        contract = _contract_values(
            source_duration=source_duration,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        )
    except RuntimeError as exc:
        report["failures"].append(f"contract: {exc}")
        return report
    source_duration = contract["source_duration"]
    target_i = contract["target_i"]
    target_lra = contract["target_lra"]
    target_tp = contract["target_tp"]
    report["source_duration"] = source_duration
    report["limits"]["integrated_target_lufs"] = target_i

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

    audio_delta = abs(float(media["audio_duration"]) - source_duration)
    container_delta = abs(float(media["container_duration"]) - source_duration)
    av_start_delta = float(media["av_start_delta_seconds"])
    report.update(
        audio_duration_delta_seconds=audio_delta,
        container_duration_delta_seconds=container_delta,
        av_start_delta_seconds=av_start_delta,
    )
    failures: list[str] = report["failures"]
    if media["audio_codec_name"] != EXPECTED_CODEC:
        failures.append(f"codec={media['audio_codec_name'] or 'unknown'}, нужен AAC")
    if media["audio_sample_rate"] != EXPECTED_SAMPLE_RATE:
        failures.append(f"sample_rate={media['audio_sample_rate']}, нужен {EXPECTED_SAMPLE_RATE}")
    if media["audio_channels"] != EXPECTED_CHANNELS:
        failures.append(f"channels={media['audio_channels']}, нужно stereo")
    if not media["video_codec_name"]:
        failures.append("не определён codec видеопотока")
    if audio_delta > DURATION_TOLERANCE_SECONDS:
        failures.append(f"audio duration delta={audio_delta:.3f}s > {DURATION_TOLERANCE_SECONDS:.2f}s")
    if container_delta > DURATION_TOLERANCE_SECONDS:
        failures.append(f"container duration delta={container_delta:.3f}s > {DURATION_TOLERANCE_SECONDS:.2f}s")
    if av_start_delta > AV_START_TOLERANCE_SECONDS:
        failures.append(f"A/V start delta={av_start_delta:.3f}s > {AV_START_TOLERANCE_SECONDS:.2f}s")
    loudness_error = abs(float(loudness["integrated_lufs"]) - target_i)
    if loudness_error > LOUDNESS_TOLERANCE_LU:
        failures.append(
            f"loudness={loudness['integrated_lufs']:.2f} LUFS; target={target_i:.2f}±{LOUDNESS_TOLERANCE_LU:.1f}"
        )
    if float(loudness["true_peak_dbtp"]) > TRUE_PEAK_DELIVERY_CEILING_DBTP:
        failures.append(
            f"true peak={loudness['true_peak_dbtp']:.2f} dBTP > {TRUE_PEAK_DELIVERY_CEILING_DBTP:.1f} dBTP"
        )
    report["passed"] = not failures
    return report


def _failure_summary(label: str, report: dict[str, Any]) -> str:
    return f"{label}: " + "; ".join(str(item) for item in report.get("failures") or ["неизвестная ошибка"])


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
        "schema_version": "dub-final-media-qa-v4",
        "measurement": "FFmpeg loudnorm / ITU-R BS.1770 / EBU R128",
        "passed": bool(mixed.get("passed") and russian_only.get("passed")),
        "mixed": mixed,
        "russian_only": russian_only,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if not report["passed"]:
        summaries = []
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
    "TARGET_I_RANGE",
    "TARGET_LRA_RANGE",
    "TARGET_TP_RANGE",
    "TRUE_PEAK_DELIVERY_CEILING_DBTP",
    "measure_loudness",
    "probe_media",
    "verify_final_file",
    "verify_final_outputs",
]
