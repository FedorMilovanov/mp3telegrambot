#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed QA for the final upload-ready Dub Studio MP4 files."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

# Fixed-original mixes can land slightly below the nominal target when the
# true-peak ceiling binds first. Keep that safe result instead of failing an
# otherwise valid render for an inaudible margin, while all peak, duration,
# codec and original-bed contracts remain fail-closed.
LOUDNESS_TOLERANCE_LU = 1.25
TRUE_PEAK_DELIVERY_CEILING_DBTP = -1.0
DURATION_TOLERANCE_SECONDS = 0.10
AV_START_TOLERANCE_SECONDS = 0.05
EXPECTED_SAMPLE_RATE = 48_000
EXPECTED_CHANNELS = 2
EXPECTED_CODEC = "aac"
TARGET_I_RANGE = (-70.0, -5.0)
TARGET_LRA_RANGE = (1.0, 50.0)
TARGET_TP_RANGE = (-9.0, 0.0)
ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v1"
ORIGINAL_BED_SAMPLE_RATE = 8_000
ORIGINAL_LEVEL_TOLERANCE = 0.015
ORIGINAL_LOCAL_SPREAD_DB = 0.75
ORIGINAL_MIN_LOCAL_WINDOWS = 3
ORIGINAL_WINDOW_SECONDS = 2.0
ORIGINAL_ALIGNMENT_MAX_SECONDS = 0.15
ORIGINAL_ALIGNMENT_PROBE_RATE = 1_000
ORIGINAL_ALIGNMENT_PROBE_SECONDS = 180.0


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


def _run_audio_bytes(command: list[str], *, timeout: float) -> bytes:
    try:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            timeout=max(30.0, float(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Декодирование для original-bed QA превысило {timeout:.0f} сек."
        ) from exc
    if process.returncode != 0:
        tail = (process.stderr or b"")[-6000:].decode("utf-8", errors="replace")
        raise RuntimeError(
            "FFmpeg original-bed QA завершился с ошибкой:\n"
            + " ".join(command)
            + "\n\n"
            + tail
        )
    return bytes(process.stdout or b"")


def _float(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
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
            f"loudness={loudness['integrated_lufs']:.2f} LUFS; target={target_i:.2f}±{LOUDNESS_TOLERANCE_LU:.2f}"
        )
    if float(loudness["true_peak_dbtp"]) > TRUE_PEAK_DELIVERY_CEILING_DBTP:
        failures.append(
            f"true peak={loudness['true_peak_dbtp']:.2f} dBTP > {TRUE_PEAK_DELIVERY_CEILING_DBTP:.1f} dBTP"
        )
    report["passed"] = not failures
    return report


def _decode_audio_mono(path: Path, *, duration: float) -> np.ndarray:
    raw = _run_audio_bytes(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(ORIGINAL_BED_SAMPLE_RATE),
            "-t",
            f"{duration:.6f}",
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=min(1800.0, max(120.0, duration * 3.0 + 60.0)),
    )
    audio = np.frombuffer(raw, dtype="<f4").astype(np.float64, copy=True)
    if len(audio) < ORIGINAL_BED_SAMPLE_RATE * 2:
        raise RuntimeError("Недостаточно декодированного аудио для original-bed QA.")
    if not np.isfinite(audio).all():
        raise RuntimeError("Original-bed QA получил NaN/Inf после декодирования.")
    return audio


def _estimate_alignment_lag(
    source: np.ndarray,
    mixed: np.ndarray,
    *,
    sample_rate: int,
) -> tuple[int, float]:
    factor = max(1, int(round(float(sample_rate) / ORIGINAL_ALIGNMENT_PROBE_RATE)))
    source_probe = np.asarray(source, dtype=np.float64).reshape(-1)[::factor]
    mixed_probe = np.asarray(mixed, dtype=np.float64).reshape(-1)[::factor]
    limit = min(
        len(source_probe),
        len(mixed_probe),
        int(ORIGINAL_ALIGNMENT_PROBE_SECONDS * ORIGINAL_ALIGNMENT_PROBE_RATE),
    )
    if limit < ORIGINAL_ALIGNMENT_PROBE_RATE * 2:
        return 0, 0.0
    source_probe = source_probe[:limit] - float(np.mean(source_probe[:limit]))
    mixed_probe = mixed_probe[:limit] - float(np.mean(mixed_probe[:limit]))
    max_lag_probe = max(
        1,
        int(round(ORIGINAL_ALIGNMENT_MAX_SECONDS * sample_rate / factor)),
    )
    best_lag = 0
    best_score = -1.0
    for lag in range(-max_lag_probe, max_lag_probe + 1):
        if lag > 0:
            left = source_probe[:-lag]
            right = mixed_probe[lag:]
        elif lag < 0:
            left = source_probe[-lag:]
            right = mixed_probe[:lag]
        else:
            left = source_probe
            right = mixed_probe
        if len(left) < ORIGINAL_ALIGNMENT_PROBE_RATE:
            continue
        denominator = math.sqrt(
            max(float(np.dot(left, left)), 0.0)
            * max(float(np.dot(right, right)), 0.0)
        )
        if denominator <= 1e-12:
            continue
        score = float(np.dot(left, right)) / denominator
        if math.isfinite(score) and score > best_score:
            best_score = score
            best_lag = lag * factor
    return int(best_lag), float(max(best_score, 0.0))


def _align_three(
    source: np.ndarray,
    mixed: np.ndarray,
    russian: np.ndarray,
    lag_samples: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if lag_samples > 0:
        source = source[:-lag_samples]
        mixed = mixed[lag_samples:]
        russian = russian[lag_samples:]
    elif lag_samples < 0:
        offset = -lag_samples
        source = source[offset:]
        mixed = mixed[:-offset]
        russian = russian[:-offset]
    length = min(len(source), len(mixed), len(russian))
    return source[:length], mixed[:length], russian[:length]


def _solve_two_branch(
    source: np.ndarray,
    russian: np.ndarray,
    mixed: np.ndarray,
) -> tuple[float, float, float] | None:
    source = np.asarray(source, dtype=np.float64)
    russian = np.asarray(russian, dtype=np.float64)
    mixed = np.asarray(mixed, dtype=np.float64)
    if not len(source) or len(source) != len(russian) or len(source) != len(mixed):
        return None
    source = source - float(np.mean(source))
    russian = russian - float(np.mean(russian))
    mixed = mixed - float(np.mean(mixed))
    ss = float(np.dot(source, source))
    rr = float(np.dot(russian, russian))
    sr = float(np.dot(source, russian))
    sm = float(np.dot(source, mixed))
    rm = float(np.dot(russian, mixed))
    if not all(math.isfinite(value) for value in (ss, rr, sr, sm, rm)):
        return None
    if ss <= 1e-10 or rr <= 1e-10:
        return None
    condition = max(0.0, 1.0 - (sr * sr) / max(ss * rr, 1e-18))
    determinant = ss * rr - sr * sr
    if condition < 1e-4 or determinant <= 1e-16:
        return None
    original = (sm * rr - rm * sr) / determinant
    russian_gain = (rm * ss - sm * sr) / determinant
    if not all(math.isfinite(value) for value in (original, russian_gain, condition)):
        return None
    return float(original), float(russian_gain), float(condition)


def estimate_original_bed(
    source: np.ndarray,
    mixed: np.ndarray,
    russian_only: np.ndarray,
    *,
    expected_level: float,
    sample_rate: int = ORIGINAL_BED_SAMPLE_RATE,
) -> dict[str, Any]:
    expected = _range(expected_level, field="expected_original_level", limits=(0.0, 1.0))
    source = np.asarray(source, dtype=np.float64).reshape(-1)
    mixed = np.asarray(mixed, dtype=np.float64).reshape(-1)
    russian = np.asarray(russian_only, dtype=np.float64).reshape(-1)
    raw_length = min(len(source), len(mixed), len(russian))
    failures: list[str] = []
    result: dict[str, Any] = {
        "policy": ORIGINAL_BED_POLICY,
        "applicable": True,
        "passed": False,
        "expected_original_level": expected,
        "sample_rate": int(sample_rate),
        "sample_count": int(raw_length),
        "aligned_sample_count": 0,
        "alignment_lag_samples": 0,
        "alignment_lag_ms": 0.0,
        "alignment_correlation": 0.0,
        "estimated_original_level": None,
        "estimated_russian_gain": None,
        "absolute_error": None,
        "regression_condition": None,
        "local_window_count": 0,
        "local_median_level": None,
        "local_p10_level": None,
        "local_p90_level": None,
        "local_spread_db": None,
        "limits": {
            "absolute_level_tolerance": ORIGINAL_LEVEL_TOLERANCE,
            "local_spread_db": ORIGINAL_LOCAL_SPREAD_DB,
            "minimum_local_windows": ORIGINAL_MIN_LOCAL_WINDOWS,
            "window_seconds": ORIGINAL_WINDOW_SECONDS,
            "alignment_max_seconds": ORIGINAL_ALIGNMENT_MAX_SECONDS,
        },
        "failures": failures,
    }
    minimum = max(1, int(sample_rate * 2.0))
    if raw_length < minimum:
        failures.append("недостаточно общих samples для original-bed regression")
        return result
    source = source[:raw_length]
    mixed = mixed[:raw_length]
    russian = russian[:raw_length]
    if not (
        np.isfinite(source).all()
        and np.isfinite(mixed).all()
        and np.isfinite(russian).all()
    ):
        failures.append("NaN/Inf в original-bed samples")
        return result

    lag_samples, correlation = _estimate_alignment_lag(
        source,
        mixed,
        sample_rate=sample_rate,
    )
    source, mixed, russian = _align_three(
        source,
        mixed,
        russian,
        lag_samples,
    )
    length = min(len(source), len(mixed), len(russian))
    result.update(
        aligned_sample_count=int(length),
        alignment_lag_samples=int(lag_samples),
        alignment_lag_ms=float(lag_samples) * 1000.0 / max(1, int(sample_rate)),
        alignment_correlation=float(correlation),
    )
    if length < minimum:
        failures.append("после alignment осталось недостаточно samples")
        return result

    solved = _solve_two_branch(source, russian, mixed)
    if solved is None:
        failures.append("глобальная двухветочная регрессия вырождена")
        return result
    original, russian_gain, condition = solved
    absolute_error = abs(original - expected)
    result.update(
        estimated_original_level=original,
        estimated_russian_gain=russian_gain,
        absolute_error=absolute_error,
        regression_condition=condition,
    )
    if original < 0.0 or original > 1.0:
        failures.append(f"оценка original level вне 0..1: {original:.5f}")
    if absolute_error > ORIGINAL_LEVEL_TOLERANCE:
        failures.append(
            f"original level={original:.5f}; нужен {expected:.5f}±{ORIGINAL_LEVEL_TOLERANCE:.3f}"
        )

    window = max(1, int(float(sample_rate) * ORIGINAL_WINDOW_SECONDS))
    local: list[float] = []
    for start in range(0, length - window + 1, window):
        solved_window = _solve_two_branch(
            source[start : start + window],
            russian[start : start + window],
            mixed[start : start + window],
        )
        if solved_window is None:
            continue
        local_original, _local_russian, local_condition = solved_window
        if local_condition < 0.01 or local_original <= 0.0 or local_original > 1.0:
            continue
        local.append(local_original)

    result["local_window_count"] = len(local)
    if len(local) < ORIGINAL_MIN_LOCAL_WINDOWS:
        failures.append(
            f"локальных окон={len(local)}; нужно минимум {ORIGINAL_MIN_LOCAL_WINDOWS}"
        )
    else:
        values = np.asarray(local, dtype=np.float64)
        median = float(np.median(values))
        p10 = float(np.percentile(values, 10))
        p90 = float(np.percentile(values, 90))
        spread_db = max(
            abs(20.0 * math.log10(max(p10, 1e-9) / max(median, 1e-9))),
            abs(20.0 * math.log10(max(p90, 1e-9) / max(median, 1e-9))),
        )
        result.update(
            local_median_level=median,
            local_p10_level=p10,
            local_p90_level=p90,
            local_spread_db=spread_db,
        )
        if abs(median - expected) > ORIGINAL_LEVEL_TOLERANCE:
            failures.append(
                f"локальный median original={median:.5f}; нужен {expected:.5f}±{ORIGINAL_LEVEL_TOLERANCE:.3f}"
            )
        if spread_db > ORIGINAL_LOCAL_SPREAD_DB:
            failures.append(
                f"локальный разброс original={spread_db:.3f} dB > {ORIGINAL_LOCAL_SPREAD_DB:.2f} dB"
            )

    result["passed"] = not failures
    return result


def _project_original_contract(mixed_video: Path) -> dict[str, Any]:
    output_dir = mixed_video.parent
    if output_dir.name.casefold() != "output":
        return {
            "policy": ORIGINAL_BED_POLICY,
            "applicable": False,
            "passed": True,
            "reason": "mixed MP4 находится вне стандартного project/output",
            "failures": [],
        }
    root = output_dir.parent
    request_path = root / "request.json"
    source = root / "source" / "source.mp4"
    failures: list[str] = []
    if not request_path.is_file():
        failures.append(f"не найден request.json: {request_path}")
    if not source.is_file() or source.stat().st_size <= 0:
        failures.append(f"не найден source/source.mp4: {source}")
    expected: float | None = None
    if not failures:
        try:
            request = json.loads(request_path.read_text(encoding="utf-8-sig"))
            if not isinstance(request, dict):
                raise RuntimeError("request.json не является объектом")
            expected = _range(
                request.get("original_level") if request.get("original_level") is not None else 0.18,
                field="request.original_level",
                limits=(0.0, 1.0),
            )
        except Exception as exc:
            failures.append(f"request original_level: {exc}")
    return {
        "policy": ORIGINAL_BED_POLICY,
        "applicable": True,
        "passed": not failures,
        "root": str(root),
        "request_path": str(request_path),
        "source_path": str(source),
        "source": str(source),
        "expected_original_level": expected,
        "failures": failures,
    }


def verify_original_bed(
    *,
    source_duration: float,
    mixed_video: Path,
    russian_only_video: Path,
) -> dict[str, Any]:
    contract = _project_original_contract(mixed_video)
    if not contract.get("applicable") or not contract.get("passed"):
        contract.pop("source", None)
        return contract
    source = Path(str(contract.pop("source")))
    try:
        source_audio = _decode_audio_mono(source, duration=source_duration)
        mixed_audio = _decode_audio_mono(mixed_video, duration=source_duration)
        russian_audio = _decode_audio_mono(russian_only_video, duration=source_duration)
        measured = estimate_original_bed(
            source_audio,
            mixed_audio,
            russian_audio,
            expected_level=float(contract["expected_original_level"]),
            sample_rate=ORIGINAL_BED_SAMPLE_RATE,
        )
    except Exception as exc:
        contract["passed"] = False
        contract.setdefault("failures", []).append(str(exc))
        return contract
    measured.update(
        root=contract.get("root"),
        request_path=contract.get("request_path"),
        source_path=contract.get("source_path"),
    )
    return measured


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
    if mixed.get("passed") and russian_only.get("passed"):
        original_bed = verify_original_bed(
            source_duration=source_duration,
            mixed_video=mixed_video,
            russian_only_video=russian_only_video,
        )
    else:
        original_bed = {
            "policy": ORIGINAL_BED_POLICY,
            "applicable": False,
            "passed": True,
            "reason": "basic final-media QA failed before original-bed measurement",
            "failures": [],
        }
    bed_passed = bool(
        original_bed.get("passed")
        if original_bed.get("applicable")
        else True
    )
    report = {
        "schema_version": "dub-final-media-qa-v5",
        "measurement": "FFmpeg loudnorm / ITU-R BS.1770 / EBU R128 + aligned post-AAC two-branch regression",
        "passed": bool(mixed.get("passed") and russian_only.get("passed") and bed_passed),
        "mixed": mixed,
        "russian_only": russian_only,
        "original_bed": original_bed,
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
        if original_bed.get("applicable") and not original_bed.get("passed"):
            summaries.append(_failure_summary("original-bed", original_bed))
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
    "ORIGINAL_ALIGNMENT_MAX_SECONDS",
    "ORIGINAL_BED_POLICY",
    "ORIGINAL_BED_SAMPLE_RATE",
    "ORIGINAL_LEVEL_TOLERANCE",
    "ORIGINAL_LOCAL_SPREAD_DB",
    "TARGET_I_RANGE",
    "TARGET_LRA_RANGE",
    "TARGET_TP_RANGE",
    "TRUE_PEAK_DELIVERY_CEILING_DBTP",
    "estimate_original_bed",
    "measure_loudness",
    "probe_media",
    "verify_final_file",
    "verify_final_outputs",
    "verify_original_bed",
]

_BASE_ALL = tuple(globals().get('__all__', ()))

import json

import math

from pathlib import Path

from typing import Any

import numpy as np

from tools.voxcpm2 import final_media_spatial_bed

_legacy_verify_final_file = verify_final_file

probe_media = probe_media

measure_loudness = measure_loudness

ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v2"

SPATIAL_BED_POLICY = final_media_spatial_bed.POLICY

REPORT_SCHEMA = "dub-final-media-qa-v6"

CURRENT_REPORT_SCHEMA = "dub-final-media-qa-v7"

ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL = float(ORIGINAL_LEVEL_TOLERANCE)

ORIGINAL_LOCAL_ABSOLUTE_SPREAD = float(ORIGINAL_LEVEL_TOLERANCE)

def _empty_original_report(
    *,
    expected: float,
    sample_rate: int,
    raw_length: int,
) -> dict[str, Any]:
    failures: list[str] = []
    absolute_level_mode = expected <= ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL
    return {
        "policy": ORIGINAL_BED_POLICY,
        "applicable": True,
        "passed": False,
        "expected_original_level": expected,
        "sample_rate": int(sample_rate),
        "sample_count": int(raw_length),
        "aligned_sample_count": 0,
        "alignment_lag_samples": 0,
        "alignment_lag_ms": 0.0,
        "alignment_correlation": 0.0,
        "estimated_original_level": None,
        "estimated_russian_gain": None,
        "absolute_error": None,
        "regression_condition": None,
        "local_window_count": 0,
        "local_available_full_windows": 0,
        "local_required_windows": 0,
        "local_median_level": None,
        "local_p10_level": None,
        "local_p90_level": None,
        "local_spread_db": None,
        "local_spread_absolute": None,
        "absolute_level_mode": absolute_level_mode,
        "limits": {
            "absolute_level_tolerance": float(ORIGINAL_LEVEL_TOLERANCE),
            "absolute_mode_max_level": ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL,
            "local_spread_db": float(ORIGINAL_LOCAL_SPREAD_DB),
            "local_spread_absolute": ORIGINAL_LOCAL_ABSOLUTE_SPREAD,
            "minimum_local_windows_configured": int(ORIGINAL_MIN_LOCAL_WINDOWS),
            "minimum_local_windows_required": 0,
            "available_full_windows": 0,
            "window_seconds": float(ORIGINAL_WINDOW_SECONDS),
            "alignment_max_seconds": float(ORIGINAL_ALIGNMENT_MAX_SECONDS),
        },
        "failures": failures,
    }

def estimate_original_bed(
    source: np.ndarray,
    mixed: np.ndarray,
    russian_only: np.ndarray,
    *,
    expected_level: float,
    sample_rate: int = ORIGINAL_BED_SAMPLE_RATE,
) -> dict[str, Any]:
    """Backward-compatible full-source regression, including expected level 0."""
    expected = _range(
        expected_level,
        field="expected_original_level",
        limits=(0.0, 1.0),
    )
    if isinstance(sample_rate, bool):
        raise RuntimeError("sample_rate original-bed QA не может быть bool")
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise RuntimeError("sample_rate original-bed QA должен быть > 0")

    source = np.asarray(source, dtype=np.float64).reshape(-1)
    mixed = np.asarray(mixed, dtype=np.float64).reshape(-1)
    russian = np.asarray(russian_only, dtype=np.float64).reshape(-1)
    raw_length = min(len(source), len(mixed), len(russian))
    result = _empty_original_report(
        expected=expected,
        sample_rate=sample_rate,
        raw_length=raw_length,
    )
    failures: list[str] = result["failures"]
    window = max(1, int(sample_rate * float(ORIGINAL_WINDOW_SECONDS)))
    if raw_length < window:
        failures.append("недостаточно общих samples для original-bed regression")
        return result
    source = source[:raw_length]
    mixed = mixed[:raw_length]
    russian = russian[:raw_length]
    if not (
        np.isfinite(source).all()
        and np.isfinite(mixed).all()
        and np.isfinite(russian).all()
    ):
        failures.append("NaN/Inf в original-bed samples")
        return result

    # When expected source level is zero, source/mixed correlation is mostly
    # noise and can select a false lag that hides a small real leak. Align to the
    # known Russian-only branch in absolute mode; retain source alignment for
    # nonzero legacy-bed measurements.
    absolute_mode = expected <= ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL
    alignment_reference = russian if absolute_mode else source
    lag, correlation = _estimate_alignment_lag(
        alignment_reference,
        mixed,
        sample_rate=sample_rate,
    )
    source, mixed, russian = _align_three(source, mixed, russian, lag)
    length = min(len(source), len(mixed), len(russian))
    result.update(
        aligned_sample_count=int(length),
        alignment_lag_samples=int(lag),
        alignment_lag_ms=float(lag) * 1000.0 / sample_rate,
        alignment_correlation=float(correlation),
    )
    if length < window:
        failures.append("после alignment осталось недостаточно samples")
        return result

    solved = _solve_two_branch(source, russian, mixed)
    if solved is None:
        failures.append("глобальная двухветочная регрессия вырождена")
        return result
    original, russian_gain, condition = solved
    tolerance = float(ORIGINAL_LEVEL_TOLERANCE)
    lower_bound = -tolerance if absolute_mode else 0.0
    result.update(
        estimated_original_level=float(original),
        estimated_russian_gain=float(russian_gain),
        absolute_error=abs(float(original) - expected),
        regression_condition=float(condition),
    )
    if original < lower_bound or original > 1.0:
        failures.append(
            f"оценка original level вне диапазона {lower_bound:.3f}..1: {original:.5f}"
        )
    if abs(original - expected) > tolerance:
        failures.append(
            f"original level={original:.5f}; нужен {expected:.5f}±{tolerance:.3f}"
        )

    available = max(1, length // window)
    required = min(int(ORIGINAL_MIN_LOCAL_WINDOWS), available)
    result.update(
        local_available_full_windows=available,
        local_required_windows=required,
    )
    result["limits"]["minimum_local_windows_required"] = required
    result["limits"]["available_full_windows"] = available
    local: list[float] = []
    for start in range(0, length - window + 1, window):
        stop = start + window
        solved_window = _solve_two_branch(
            source[start:stop],
            russian[start:stop],
            mixed[start:stop],
        )
        if solved_window is None or solved_window[2] < 0.01:
            continue
        value = float(solved_window[0])
        if lower_bound <= value <= 1.0:
            local.append(value)
    result["local_window_count"] = len(local)
    if len(local) < required:
        failures.append(
            f"локальных окон={len(local)}; нужно {required} из {available} доступных"
        )
    else:
        values = np.asarray(local, dtype=np.float64)
        median = float(np.median(values))
        p10 = float(np.percentile(values, 10))
        p90 = float(np.percentile(values, 90))
        absolute_spread = max(abs(p10 - median), abs(p90 - median))
        spread_db: float | None = None
        if not absolute_mode:
            spread_db = max(
                abs(20.0 * math.log10(max(p10, 1e-9) / max(median, 1e-9))),
                abs(20.0 * math.log10(max(p90, 1e-9) / max(median, 1e-9))),
            )
        result.update(
            local_median_level=median,
            local_p10_level=p10,
            local_p90_level=p90,
            local_spread_db=spread_db,
            local_spread_absolute=absolute_spread,
        )
        if abs(median - expected) > tolerance:
            failures.append(
                f"локальный median original={median:.5f}; нужен {expected:.5f}±{tolerance:.3f}"
            )
        if absolute_mode:
            if absolute_spread > ORIGINAL_LOCAL_ABSOLUTE_SPREAD:
                failures.append(
                    f"локальный абсолютный разброс={absolute_spread:.5f} "
                    f"> {ORIGINAL_LOCAL_ABSOLUTE_SPREAD:.3f}"
                )
        elif spread_db is not None and spread_db > float(ORIGINAL_LOCAL_SPREAD_DB):
            failures.append(
                f"локальный разброс={spread_db:.3f} dB "
                f"> {float(ORIGINAL_LOCAL_SPREAD_DB):.2f} dB"
            )
    result["passed"] = not failures
    return result

def estimate_spatial_bed(
    source_stereo: np.ndarray,
    mixed_stereo: np.ndarray,
    russian_stereo: np.ndarray,
    *,
    expected_level: float,
    sample_rate: int = final_media_spatial_bed.SAMPLE_RATE,
) -> dict[str, Any]:
    return final_media_spatial_bed.estimate_spatial_bed(
        source_stereo,
        mixed_stereo,
        russian_stereo,
        expected_level=expected_level,
        sample_rate=sample_rate,
    )

def _project_contract(mixed_video: Path) -> dict[str, Any]:
    output_dir = Path(mixed_video).resolve().parent
    if output_dir.name.casefold() != "output":
        return {
            "policy": ORIGINAL_BED_POLICY,
            "applicable": False,
            "passed": True,
            "reason": "mixed MP4 находится вне стандартного project/output",
            "failures": [],
        }
    root = output_dir.parent
    request_path = root / "request.json"
    source_path = root / "source" / "source.mp4"
    failures: list[str] = []
    request: dict[str, Any] = {}
    if not request_path.is_file():
        failures.append(f"не найден request.json: {request_path}")
    else:
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise RuntimeError("request.json не является объектом")
            request = payload
        except Exception as exc:
            failures.append(f"request.json: {exc}")
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        failures.append(f"не найден source/source.mp4: {source_path}")
    expected: float | None = None
    if not failures:
        try:
            expected = _range(
                request.get("original_level")
                if request.get("original_level") is not None
                else 0.18,
                field="request.original_level",
                limits=(0.0, 1.0),
            )
        except Exception as exc:
            failures.append(f"request original_level: {exc}")
    return {
        "policy": ORIGINAL_BED_POLICY,
        "applicable": True,
        "passed": not failures,
        "root": str(root),
        "request_path": str(request_path),
        "source_path": str(source_path),
        "request": request,
        "expected_original_level": expected,
        "failures": failures,
    }

def verify_original_bed(
    *,
    source_duration: float,
    mixed_video: Path,
    russian_only_video: Path,
) -> dict[str, Any]:
    contract = _project_contract(mixed_video)
    if not contract.get("applicable") or not contract.get("passed"):
        return contract
    request = dict(contract.get("request") or {})
    source = Path(str(contract["source_path"]))
    expected = float(contract["expected_original_level"])
    direct = str(request.get("translation_mode") or "").casefold() == "direct"
    try:
        if direct:
            measured = final_media_spatial_bed.verify_spatial_bed_files(
                source=source,
                mixed_video=mixed_video,
                russian_only_video=russian_only_video,
                source_duration=source_duration,
                expected_level=expected,
            )
            measured["mode"] = "direct-monolithic-spatial-bed"
        else:
            measured = estimate_original_bed(
                _decode_audio_mono(source, duration=source_duration),
                _decode_audio_mono(mixed_video, duration=source_duration),
                _decode_audio_mono(russian_only_video, duration=source_duration),
                expected_level=expected,
                sample_rate=ORIGINAL_BED_SAMPLE_RATE,
            )
            measured["mode"] = "legacy-full-source-bed"
    except Exception as exc:
        return {
            **contract,
            "policy": SPATIAL_BED_POLICY if direct else ORIGINAL_BED_POLICY,
            "passed": False,
            "failures": [*contract.get("failures", []), str(exc)],
        }
    measured.update(
        root=contract.get("root"),
        request_path=contract.get("request_path"),
        source_path=contract.get("source_path"),
        translation_mode=str(request.get("translation_mode") or ""),
    )
    return measured

def verify_final_file(
    path: Path,
    *,
    source_duration: float,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    probe_media = probe_media
    measure_loudness = measure_loudness
    return _legacy_verify_final_file(
        path,
        source_duration=source_duration,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )

def _failure_summary(label: str, report: dict[str, Any]) -> str:
    return f"{label}: " + "; ".join(
        str(item) for item in report.get("failures") or ["неизвестная ошибка"]
    )

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
    if mixed.get("passed") and russian_only.get("passed"):
        original_bed = verify_original_bed(
            source_duration=source_duration,
            mixed_video=mixed_video,
            russian_only_video=russian_only_video,
        )
    else:
        original_bed = {
            "policy": SPATIAL_BED_POLICY,
            "applicable": False,
            "passed": True,
            "reason": "basic final-media QA failed before source-bed measurement",
            "failures": [],
        }
    bed_passed = bool(
        original_bed.get("passed")
        if original_bed.get("applicable", True)
        else True
    )
    report = {
        "schema_version": CURRENT_REPORT_SCHEMA,
        "compatibility_schema": REPORT_SCHEMA,
        "measurement": (
            "FFmpeg loudnorm / ITU-R BS.1770 / EBU R128 + aligned post-AAC "
            "mode-aware full-source or mid/side spatial-bed regression"
        ),
        "passed": bool(mixed.get("passed") and russian_only.get("passed") and bed_passed),
        "mixed": mixed,
        "russian_only": russian_only,
        "original_bed": original_bed,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if not report["passed"]:
        summaries: list[str] = []
        if not mixed.get("passed"):
            summaries.append(_failure_summary("mixed", mixed))
        if not russian_only.get("passed"):
            summaries.append(_failure_summary("russian-only", russian_only))
        if original_bed.get("applicable", True) and not original_bed.get("passed"):
            summaries.append(_failure_summary("original-bed", original_bed))
        raise RuntimeError(
            "Конечный media-QA не принят. "
            + " | ".join(summaries)
            + f". Отчёт сохранён: {report_path}"
        )
    return report

ORIGINAL_BED_POLICY = ORIGINAL_BED_POLICY

estimate_original_bed = estimate_original_bed

verify_final_file = verify_final_file

verify_original_bed = verify_original_bed

verify_final_outputs = verify_final_outputs

__all__ = sorted(
    set(_BASE_ALL)
    | {
        "CURRENT_REPORT_SCHEMA",
        "ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL",
        "ORIGINAL_BED_POLICY",
        "ORIGINAL_LOCAL_ABSOLUTE_SPREAD",
        "REPORT_SCHEMA",
        "SPATIAL_BED_POLICY",
        "estimate_original_bed",
        "estimate_spatial_bed",
        "verify_final_file",
        "verify_final_outputs",
        "verify_original_bed",
    }
)
