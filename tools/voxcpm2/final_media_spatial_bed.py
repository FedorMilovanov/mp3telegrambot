#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone post-AAC mid/side regression for direct monolithic dubbing."""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from tools.voxcpm2 import spatial_bed_contract

POLICY = spatial_bed_contract.QA_POLICY
SAMPLE_RATE = 8_000
WINDOW_SECONDS = 2.0
MIN_LOCAL_WINDOWS = 3
MAX_NORMALIZED_RESIDUAL = 0.22
ALIGNMENT_MAX_SECONDS = 0.35
ALIGNMENT_PROBE_SECONDS = 45.0


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result


def _run_audio_bytes(command: list[str], *, timeout: float) -> bytes:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace")[-1800:]
        raise RuntimeError("FFmpeg spatial-bed decode failed: " + detail)
    return bytes(process.stdout)


def decode_audio_stereo(path: Path, *, duration: float) -> np.ndarray:
    duration = _finite(duration, field="source_duration")
    if duration <= 0.0:
        raise RuntimeError("source_duration должен быть > 0")
    raw = _run_audio_bytes(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(Path(path)),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "2",
            "-ar",
            str(SAMPLE_RATE),
            "-t",
            f"{duration:.6f}",
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=min(1800.0, max(120.0, duration * 3.0 + 60.0)),
    )
    audio = np.frombuffer(raw, dtype="<f4").astype(np.float64, copy=True)
    if len(audio) % 2:
        audio = audio[:-1]
    if len(audio) < SAMPLE_RATE * 4:
        raise RuntimeError("Недостаточно stereo samples для spatial-bed QA.")
    audio = audio.reshape(-1, 2)
    if not np.isfinite(audio).all():
        raise RuntimeError("Spatial-bed QA получил NaN/Inf после декодирования.")
    return audio


def _mid_side(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    stereo = np.asarray(audio, dtype=np.float64)
    if stereo.ndim != 2 or stereo.shape[1] != 2:
        raise RuntimeError("Spatial-bed QA ожидает stereo PCM.")
    return (
        (stereo[:, 0] + stereo[:, 1]) * 0.5,
        (stereo[:, 0] - stereo[:, 1]) * 0.5,
    )


def _alignment_lag(
    reference: np.ndarray,
    value: np.ndarray,
    *,
    sample_rate: int,
) -> tuple[int, float]:
    left = np.asarray(reference, dtype=np.float64).reshape(-1)
    right = np.asarray(value, dtype=np.float64).reshape(-1)
    probe = min(len(left), len(right), int(sample_rate * ALIGNMENT_PROBE_SECONDS))
    if probe < sample_rate:
        return 0, 0.0
    left = left[:probe] - float(np.mean(left[:probe]))
    right = right[:probe] - float(np.mean(right[:probe]))
    max_lag = max(1, int(sample_rate * ALIGNMENT_MAX_SECONDS))
    # Downsample the correlation probe only for lag discovery; final regression
    # remains at the full QA sample rate.
    stride = max(1, int(sample_rate // 2_000))
    left_small = left[::stride]
    right_small = right[::stride]
    max_small = max(1, max_lag // stride)
    best_lag = 0
    best_correlation = -1.0
    for lag in range(-max_small, max_small + 1):
        if lag > 0:
            a = left_small[:-lag]
            b = right_small[lag:]
        elif lag < 0:
            offset = -lag
            a = left_small[offset:]
            b = right_small[:-offset]
        else:
            a, b = left_small, right_small
        if len(a) < 256:
            continue
        denominator = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
        if denominator <= 1e-12:
            continue
        correlation = float(np.dot(a, b)) / denominator
        if correlation > best_correlation:
            best_correlation = correlation
            best_lag = lag * stride
    return int(best_lag), max(0.0, float(best_correlation))


def _align_stereo(
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


def _projection(reference: np.ndarray, value: np.ndarray) -> float | None:
    left = np.asarray(reference, dtype=np.float64).reshape(-1)
    right = np.asarray(value, dtype=np.float64).reshape(-1)
    length = min(len(left), len(right))
    if length < 1:
        return None
    left = left[:length] - float(np.mean(left[:length]))
    right = right[:length] - float(np.mean(right[:length]))
    denominator = float(np.dot(left, left))
    if not math.isfinite(denominator) or denominator <= 1e-12:
        return None
    coefficient = float(np.dot(left, right)) / denominator
    return coefficient if math.isfinite(coefficient) else None


def _joint_solve(
    source: np.ndarray,
    russian: np.ndarray,
    mixed: np.ndarray,
) -> tuple[float, float, float] | None:
    source = np.asarray(source, dtype=np.float64).reshape(-1)
    russian = np.asarray(russian, dtype=np.float64).reshape(-1)
    mixed = np.asarray(mixed, dtype=np.float64).reshape(-1)
    length = min(len(source), len(russian), len(mixed))
    if length < 256:
        return None
    source = source[:length] - float(np.mean(source[:length]))
    russian = russian[:length] - float(np.mean(russian[:length]))
    mixed = mixed[:length] - float(np.mean(mixed[:length]))
    matrix = np.column_stack((source, russian))
    try:
        solution, _residuals, rank, singular = np.linalg.lstsq(matrix, mixed, rcond=1e-6)
    except np.linalg.LinAlgError:
        return None
    if int(rank) < 2 or len(singular) < 2 or singular[-1] <= 1e-12:
        return None
    condition = float(min(1.0, singular[-1] / max(singular[0], 1e-12)))
    center, russian_gain = float(solution[0]), float(solution[1])
    if not all(math.isfinite(item) for item in (center, russian_gain, condition)):
        return None
    return center, russian_gain, condition


def _rms(value: np.ndarray) -> float:
    audio = np.asarray(value, dtype=np.float64).reshape(-1)
    return math.sqrt(float(np.mean(audio**2)) + 1e-18) if len(audio) else 0.0


def estimate_spatial_bed(
    source_stereo: np.ndarray,
    mixed_stereo: np.ndarray,
    russian_stereo: np.ndarray,
    *,
    expected_level: float,
    sample_rate: int = SAMPLE_RATE,
) -> dict[str, Any]:
    """Measure post-AAC center suppression and stereo-side preservation."""
    levels = spatial_bed_contract.source_bed_levels(expected_level)
    source = np.asarray(source_stereo, dtype=np.float64)
    mixed = np.asarray(mixed_stereo, dtype=np.float64)
    russian = np.asarray(russian_stereo, dtype=np.float64)
    raw_length = min(len(source), len(mixed), len(russian))
    failures: list[str] = []
    report: dict[str, Any] = {
        "policy": POLICY,
        "applicable": True,
        "passed": False,
        "sample_rate": int(sample_rate),
        "sample_count": int(raw_length),
        "expected": levels,
        "estimated_center_level": None,
        "estimated_side_level": None,
        "estimated_russian_gain": None,
        "source_side_to_mid_energy_ratio": None,
        "side_measurement_applicable": False,
        "alignment_lag_samples": 0,
        "alignment_lag_ms": 0.0,
        "alignment_correlation": 0.0,
        "regression_condition": None,
        "normalized_residual": None,
        "local_center_window_count": 0,
        "local_side_window_count": 0,
        "local_side_eligible_window_count": 0,
        "local_required_windows": 0,
        "failures": failures,
        "limits": {
            "center_absolute_tolerance": spatial_bed_contract.CENTER_ABSOLUTE_TOLERANCE,
            "side_absolute_tolerance": spatial_bed_contract.SIDE_ABSOLUTE_TOLERANCE,
            "minimum_side_to_mid_energy_ratio": spatial_bed_contract.MIN_SIDE_TO_MID_ENERGY_RATIO,
            "russian_gain": [
                spatial_bed_contract.MIN_RUSSIAN_GAIN,
                spatial_bed_contract.MAX_RUSSIAN_GAIN,
            ],
            "normalized_residual": MAX_NORMALIZED_RESIDUAL,
            "window_seconds": WINDOW_SECONDS,
        },
    }
    minimum = max(1, int(sample_rate * WINDOW_SECONDS))
    if raw_length < minimum:
        failures.append("недостаточно общих stereo samples для spatial-bed regression")
        return report
    source = source[:raw_length]
    mixed = mixed[:raw_length]
    russian = russian[:raw_length]
    if not (
        np.isfinite(source).all()
        and np.isfinite(mixed).all()
        and np.isfinite(russian).all()
    ):
        failures.append("NaN/Inf в spatial-bed samples")
        return report

    mixed_mid, _mixed_side = _mid_side(mixed)
    russian_mid, _russian_side = _mid_side(russian)
    lag, correlation = _alignment_lag(
        russian_mid,
        mixed_mid,
        sample_rate=int(sample_rate),
    )
    source, mixed, russian = _align_stereo(source, mixed, russian, lag)
    source_mid, source_side = _mid_side(source)
    mixed_mid, _mixed_side = _mid_side(mixed)
    russian_mid, _russian_side = _mid_side(russian)
    report.update(
        aligned_sample_count=int(len(source)),
        alignment_lag_samples=int(lag),
        alignment_lag_ms=float(lag) * 1000.0 / max(1, int(sample_rate)),
        alignment_correlation=float(correlation),
    )
    if len(source) < minimum:
        failures.append("после alignment осталось недостаточно stereo samples")
        return report

    solved = _joint_solve(source_mid, russian_mid, mixed_mid)
    if solved is None:
        failures.append("глобальная center/russian regression вырождена")
        return report
    center_level, russian_gain, condition = solved
    residual = mixed - russian * russian_gain
    residual_mid, residual_side = _mid_side(residual)
    side_level = _projection(source_side, residual_side)
    mid_energy = float(np.dot(source_mid, source_mid))
    side_energy = float(np.dot(source_side, source_side))
    side_ratio = side_energy / max(mid_energy, 1e-18)
    side_applicable = bool(
        side_ratio >= spatial_bed_contract.MIN_SIDE_TO_MID_ENERGY_RATIO
    )

    expected_center = float(levels["center_full_mix_level"])
    expected_side = float(levels["expected_total_side_level"])
    predicted_left = russian[:, 0] * russian_gain + (
        source[:, 0] * expected_center
        + (source[:, 0] - source[:, 1]) * 0.5 * float(levels["spatial_side_level"])
    )
    predicted_right = russian[:, 1] * russian_gain + (
        source[:, 1] * expected_center
        + (source[:, 1] - source[:, 0]) * 0.5 * float(levels["spatial_side_level"])
    )
    predicted = np.column_stack((predicted_left, predicted_right))
    normalized_residual = _rms(mixed - predicted) / max(_rms(mixed), 1e-9)
    report.update(
        estimated_center_level=center_level,
        estimated_side_level=side_level,
        estimated_russian_gain=russian_gain,
        source_side_to_mid_energy_ratio=side_ratio,
        side_measurement_applicable=side_applicable,
        regression_condition=condition,
        normalized_residual=normalized_residual,
        residual_mid_rms=_rms(residual_mid),
        residual_side_rms=_rms(residual_side),
    )

    if center_level < -spatial_bed_contract.CENTER_ABSOLUTE_TOLERANCE:
        failures.append(f"center source coefficient отрицателен: {center_level:.5f}")
    if center_level > float(levels["maximum_allowed_center_level"]):
        failures.append(
            "центр исходной речи не подавлен: "
            f"center={center_level:.5f} > {float(levels['maximum_allowed_center_level']):.5f}"
        )
    if abs(center_level - expected_center) > spatial_bed_contract.CENTER_ABSOLUTE_TOLERANCE:
        failures.append(
            f"center={center_level:.5f}; нужен {expected_center:.5f}"
            f"±{spatial_bed_contract.CENTER_ABSOLUTE_TOLERANCE:.3f}"
        )
    if not (
        spatial_bed_contract.MIN_RUSSIAN_GAIN
        <= russian_gain
        <= spatial_bed_contract.MAX_RUSSIAN_GAIN
    ):
        failures.append(f"russian gain вне контракта: {russian_gain:.5f}")
    if normalized_residual > MAX_NORMALIZED_RESIDUAL:
        failures.append(
            f"post-AAC spatial mix residual={normalized_residual:.4f} "
            f"> {MAX_NORMALIZED_RESIDUAL:.3f}"
        )
    if side_applicable:
        if side_level is None:
            failures.append("не удалось оценить stereo-side source coefficient")
        elif not (
            float(levels["minimum_allowed_side_level"])
            <= side_level
            <= float(levels["maximum_allowed_side_level"])
        ):
            failures.append(
                f"side={side_level:.5f}; нужен {expected_side:.5f}"
                f"±{spatial_bed_contract.SIDE_ABSOLUTE_TOLERANCE:.3f}"
            )

    window = max(1, int(sample_rate * WINDOW_SECONDS))
    available = max(1, len(source) // window)
    required = min(MIN_LOCAL_WINDOWS, available)
    report["local_required_windows"] = required
    local_center: list[float] = []
    local_side: list[float] = []
    side_eligible = 0
    for start in range(0, len(source) - window + 1, window):
        stop = start + window
        solved_window = _joint_solve(
            source_mid[start:stop],
            russian_mid[start:stop],
            mixed_mid[start:stop],
        )
        if solved_window is not None and solved_window[2] >= 0.002:
            local_center.append(float(solved_window[0]))
        if side_applicable:
            local_source_side = _rms(source_side[start:stop])
            local_source_mid = _rms(source_mid[start:stop])
            if local_source_side < local_source_mid * math.sqrt(
                spatial_bed_contract.MIN_SIDE_TO_MID_ENERGY_RATIO
            ):
                continue
            side_eligible += 1
            local_side_value = _projection(
                source_side[start:stop],
                residual_side[start:stop],
            )
            if local_side_value is not None:
                local_side.append(float(local_side_value))
    report["local_center_window_count"] = len(local_center)
    report["local_side_window_count"] = len(local_side)
    report["local_side_eligible_window_count"] = side_eligible
    if len(local_center) < required:
        failures.append(
            f"локальных center-окон={len(local_center)}; нужно {required} из {available}"
        )
    else:
        values = np.asarray(local_center, dtype=np.float64)
        median = float(np.median(values))
        p90 = float(np.percentile(values, 90))
        report.update(
            local_center_median=median,
            local_center_p10=float(np.percentile(values, 10)),
            local_center_p90=p90,
        )
        if median > float(levels["maximum_allowed_center_level"]):
            failures.append(
                f"локальный median center={median:.5f} сохраняет исходную речь"
            )
        if p90 > float(levels["maximum_allowed_center_level"]) + 0.015:
            failures.append(
                f"локальный p90 center={p90:.5f} превышает suppression ceiling"
            )
    if side_applicable:
        required_side = min(required, side_eligible)
        if required_side < 1:
            failures.append("stereo side глобально есть, но нет пригодных локальных side-окон")
        elif len(local_side) < required_side:
            failures.append(
                f"локальных side-окон={len(local_side)}; нужно {required_side} из {side_eligible}"
            )
        else:
            values = np.asarray(local_side, dtype=np.float64)
            median = float(np.median(values))
            report.update(
                local_side_median=median,
                local_side_p10=float(np.percentile(values, 10)),
                local_side_p90=float(np.percentile(values, 90)),
            )
            if abs(median - expected_side) > spatial_bed_contract.SIDE_ABSOLUTE_TOLERANCE:
                failures.append(
                    f"локальный median side={median:.5f}; нужен {expected_side:.5f}"
                )

    report["passed"] = not failures
    return report


def verify_spatial_bed_files(
    *,
    source: Path,
    mixed_video: Path,
    russian_only_video: Path,
    source_duration: float,
    expected_level: float,
) -> dict[str, Any]:
    report = estimate_spatial_bed(
        decode_audio_stereo(source, duration=source_duration),
        decode_audio_stereo(mixed_video, duration=source_duration),
        decode_audio_stereo(russian_only_video, duration=source_duration),
        expected_level=expected_level,
        sample_rate=SAMPLE_RATE,
    )
    report.update(
        source_path=str(Path(source)),
        mixed_video=str(Path(mixed_video)),
        russian_only_video=str(Path(russian_only_video)),
    )
    return report


__all__ = [
    "ALIGNMENT_MAX_SECONDS",
    "ALIGNMENT_PROBE_SECONDS",
    "MAX_NORMALIZED_RESIDUAL",
    "MIN_LOCAL_WINDOWS",
    "POLICY",
    "SAMPLE_RATE",
    "WINDOW_SECONDS",
    "decode_audio_stereo",
    "estimate_spatial_bed",
    "verify_spatial_bed_files",
]
