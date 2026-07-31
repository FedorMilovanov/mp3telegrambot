#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mode-aware final Dub Studio media QA.

The sibling module remains the codec, duration, loudness and A/V authority. This
facade preserves the zero-safe full-source regression for legacy/Gemini modes and
adds a stereo post-AAC contract for ready-SRT monolithic dubbing: source center
(dialogue) must stay at the bounded floor, source side/space must remain at the
requested level when the source contains meaningful stereo information, and the
Russian branch must remain stable after encoding.
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2 import spatial_bed_contract

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "final_media_qa.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._final_media_qa_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый final media QA: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_verify_final_file = _legacy.verify_final_file
_legacy_verify_final_outputs = _legacy.verify_final_outputs

LEGACY_ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v1"
ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v2"
SPATIAL_BED_POLICY = spatial_bed_contract.QA_POLICY
ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)
ORIGINAL_LOCAL_ABSOLUTE_SPREAD = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)
REPORT_SCHEMA = "dub-final-media-qa-v7"
SPATIAL_SAMPLE_RATE = 8_000
SPATIAL_WINDOW_SECONDS = 2.0
SPATIAL_MIN_LOCAL_WINDOWS = 3
SPATIAL_MAX_NORMALIZED_RESIDUAL = 0.22


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


def _empty_original_report(
    *,
    expected: float,
    sample_rate: int,
    raw_length: int,
) -> dict[str, Any]:
    failures: list[str] = []
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
        "absolute_level_mode": expected <= ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL,
        "limits": {
            "absolute_level_tolerance": float(_legacy.ORIGINAL_LEVEL_TOLERANCE),
            "absolute_mode_max_level": ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL,
            "local_spread_db": float(_legacy.ORIGINAL_LOCAL_SPREAD_DB),
            "local_spread_absolute": ORIGINAL_LOCAL_ABSOLUTE_SPREAD,
            "minimum_local_windows_configured": int(_legacy.ORIGINAL_MIN_LOCAL_WINDOWS),
            "minimum_local_windows_required": 0,
            "available_full_windows": 0,
            "window_seconds": float(_legacy.ORIGINAL_WINDOW_SECONDS),
            "alignment_max_seconds": float(_legacy.ORIGINAL_ALIGNMENT_MAX_SECONDS),
        },
        "failures": failures,
    }


def estimate_original_bed(
    source: np.ndarray,
    mixed: np.ndarray,
    russian_only: np.ndarray,
    *,
    expected_level: float,
    sample_rate: int = _legacy.ORIGINAL_BED_SAMPLE_RATE,
) -> dict[str, Any]:
    """Backward-compatible zero-safe full-source regression."""
    expected = _legacy._range(
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
    minimum = max(1, int(sample_rate * float(_legacy.ORIGINAL_WINDOW_SECONDS)))
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

    lag_samples, correlation = _legacy._estimate_alignment_lag(
        source,
        mixed,
        sample_rate=sample_rate,
    )
    source, mixed, russian = _legacy._align_three(
        source,
        mixed,
        russian,
        lag_samples,
    )
    length = min(len(source), len(mixed), len(russian))
    result.update(
        aligned_sample_count=int(length),
        alignment_lag_samples=int(lag_samples),
        alignment_lag_ms=float(lag_samples) * 1000.0 / sample_rate,
        alignment_correlation=float(correlation),
    )
    if length < minimum:
        failures.append("после alignment осталось недостаточно samples")
        return result

    solved = _legacy._solve_two_branch(source, russian, mixed)
    if solved is None:
        failures.append("глобальная двухветочная регрессия вырождена")
        return result
    original, russian_gain, condition = solved
    absolute_error = abs(original - expected)
    absolute_mode = expected <= ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL
    lower_bound = -float(_legacy.ORIGINAL_LEVEL_TOLERANCE) if absolute_mode else 0.0
    result.update(
        estimated_original_level=original,
        estimated_russian_gain=russian_gain,
        absolute_error=absolute_error,
        regression_condition=condition,
    )
    if original < lower_bound or original > 1.0:
        failures.append(
            f"оценка original level вне допустимого диапазона "
            f"{lower_bound:.3f}..1: {original:.5f}"
        )
    if absolute_error > float(_legacy.ORIGINAL_LEVEL_TOLERANCE):
        failures.append(
            f"original level={original:.5f}; нужен {expected:.5f}"
            f"±{float(_legacy.ORIGINAL_LEVEL_TOLERANCE):.3f}"
        )

    window = max(1, int(sample_rate * float(_legacy.ORIGINAL_WINDOW_SECONDS)))
    available_windows = max(1, length // window)
    required_windows = min(int(_legacy.ORIGINAL_MIN_LOCAL_WINDOWS), available_windows)
    result["local_available_full_windows"] = available_windows
    result["local_required_windows"] = required_windows
    result["limits"]["minimum_local_windows_required"] = required_windows
    result["limits"]["available_full_windows"] = available_windows

    local: list[float] = []
    for start in range(0, length - window + 1, window):
        solved_window = _legacy._solve_two_branch(
            source[start:start + window],
            russian[start:start + window],
            mixed[start:start + window],
        )
        if solved_window is None:
            continue
        local_original, _local_russian, local_condition = solved_window
        if local_condition < 0.01:
            continue
        if local_original < lower_bound or local_original > 1.0:
            continue
        local.append(float(local_original))

    result["local_window_count"] = len(local)
    if len(local) < required_windows:
        failures.append(
            f"локальных окон={len(local)}; нужно {required_windows} "
            f"из {available_windows} доступных"
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
        if abs(median - expected) > float(_legacy.ORIGINAL_LEVEL_TOLERANCE):
            failures.append(
                f"локальный median original={median:.5f}; нужен {expected:.5f}"
                f"±{float(_legacy.ORIGINAL_LEVEL_TOLERANCE):.3f}"
            )
        if absolute_mode:
            if absolute_spread > ORIGINAL_LOCAL_ABSOLUTE_SPREAD:
                failures.append(
                    f"локальный абсолютный разброс original={absolute_spread:.5f} "
                    f"> {ORIGINAL_LOCAL_ABSOLUTE_SPREAD:.3f}"
                )
        elif spread_db is not None and spread_db > float(_legacy.ORIGINAL_LOCAL_SPREAD_DB):
            failures.append(
                f"локальный разброс original={spread_db:.3f} dB "
                f"> {float(_legacy.ORIGINAL_LOCAL_SPREAD_DB):.2f} dB"
            )

    result["passed"] = not failures
    return result


def _decode_audio_stereo(path: Path, *, duration: float) -> np.ndarray:
    raw = _legacy._run_audio_bytes(
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
            "2",
            "-ar",
            str(SPATIAL_SAMPLE_RATE),
            "-t",
            f"{float(duration):.6f}",
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=min(1800.0, max(120.0, float(duration) * 3.0 + 60.0)),
    )
    audio = np.frombuffer(raw, dtype="<f4").astype(np.float64, copy=True)
    if len(audio) % 2:
        audio = audio[:-1]
    if len(audio) < SPATIAL_SAMPLE_RATE * 4:
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
    solved = _legacy._solve_two_branch(source, russian, mixed)
    if solved is not None:
        return solved
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
    center, russian_gain = (float(solution[0]), float(solution[1]))
    if not all(math.isfinite(value) for value in (center, russian_gain, condition)):
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
    sample_rate: int = SPATIAL_SAMPLE_RATE,
) -> dict[str, Any]:
    """Measure post-AAC center suppression and stereo-side preservation."""
    levels = spatial_bed_contract.source_bed_levels(expected_level)
    source = np.asarray(source_stereo, dtype=np.float64)
    mixed = np.asarray(mixed_stereo, dtype=np.float64)
    russian = np.asarray(russian_stereo, dtype=np.float64)
    raw_length = min(len(source), len(mixed), len(russian))
    failures: list[str] = []
    report: dict[str, Any] = {
        "policy": SPATIAL_BED_POLICY,
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
            "normalized_residual": SPATIAL_MAX_NORMALIZED_RESIDUAL,
            "window_seconds": SPATIAL_WINDOW_SECONDS,
        },
    }
    minimum = max(1, int(sample_rate * SPATIAL_WINDOW_SECONDS))
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

    _source_mid, _source_side = _mid_side(source)
    mixed_mid, _mixed_side = _mid_side(mixed)
    russian_mid, _russian_side = _mid_side(russian)
    lag, correlation = _legacy._estimate_alignment_lag(
        russian_mid,
        mixed_mid,
        sample_rate=int(sample_rate),
    )
    source, mixed, russian = _align_stereo(source, mixed, russian, lag)
    source_mid, source_side = _mid_side(source)
    mixed_mid, mixed_side = _mid_side(mixed)
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
    if normalized_residual > SPATIAL_MAX_NORMALIZED_RESIDUAL:
        failures.append(
            f"post-AAC spatial mix residual={normalized_residual:.4f} "
            f"> {SPATIAL_MAX_NORMALIZED_RESIDUAL:.3f}"
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

    window = max(1, int(sample_rate * SPATIAL_WINDOW_SECONDS))
    available = max(1, len(source) // window)
    required = min(SPATIAL_MIN_LOCAL_WINDOWS, available)
    report["local_required_windows"] = required
    local_center: list[float] = []
    local_side: list[float] = []
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
            local_side_value = _projection(
                source_side[start:stop],
                residual_side[start:stop],
            )
            local_source_side = _rms(source_side[start:stop])
            local_source_mid = _rms(source_mid[start:stop])
            if (
                local_side_value is not None
                and local_source_side
                >= local_source_mid
                * math.sqrt(spatial_bed_contract.MIN_SIDE_TO_MID_ENERGY_RATIO)
            ):
                local_side.append(float(local_side_value))
    report["local_center_window_count"] = len(local_center)
    report["local_side_window_count"] = len(local_side)
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
        required_side = min(required, max(1, len(local_side)))
        if len(local_side) < required_side:
            failures.append(
                f"локальных side-окон={len(local_side)}; нужно {required_side}"
            )
        elif local_side:
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


def _project_contract(mixed_video: Path) -> dict[str, Any]:
    output_dir = Path(mixed_video).resolve().parent
    if output_dir.name.casefold() != "output":
        return {
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
            expected = _legacy._range(
                request.get("original_level")
                if request.get("original_level") is not None
                else 0.18,
                field="request.original_level",
                limits=(0.0, 1.0),
            )
        except Exception as exc:
            failures.append(f"request original_level: {exc}")
    return {
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
            measured = estimate_spatial_bed(
                _decode_audio_stereo(source, duration=source_duration),
                _decode_audio_stereo(mixed_video, duration=source_duration),
                _decode_audio_stereo(russian_only_video, duration=source_duration),
                expected_level=expected,
                sample_rate=SPATIAL_SAMPLE_RATE,
            )
            measured["mode"] = "direct-monolithic-spatial-bed"
        else:
            measured = estimate_original_bed(
                _legacy._decode_audio_mono(source, duration=source_duration),
                _legacy._decode_audio_mono(mixed_video, duration=source_duration),
                _legacy._decode_audio_mono(russian_only_video, duration=source_duration),
                expected_level=expected,
                sample_rate=_legacy.ORIGINAL_BED_SAMPLE_RATE,
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
    _legacy.probe_media = probe_media
    _legacy.measure_loudness = measure_loudness
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
        "schema_version": REPORT_SCHEMA,
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
            summaries.append(_failure_summary("source-bed", original_bed))
        raise RuntimeError(
            "Конечный media-QA не принят. "
            + " | ".join(summaries)
            + f". Отчёт сохранён: {report_path}"
        )
    return report


_legacy.ORIGINAL_BED_POLICY = ORIGINAL_BED_POLICY
_legacy.estimate_original_bed = estimate_original_bed
_legacy.verify_final_file = verify_final_file
_legacy.verify_original_bed = verify_original_bed
_legacy.verify_final_outputs = verify_final_outputs

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "LEGACY_ORIGINAL_BED_POLICY",
        "ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL",
        "ORIGINAL_BED_POLICY",
        "ORIGINAL_LOCAL_ABSOLUTE_SPREAD",
        "REPORT_SCHEMA",
        "SPATIAL_BED_POLICY",
        "SPATIAL_MAX_NORMALIZED_RESIDUAL",
        "SPATIAL_SAMPLE_RATE",
        "SPATIAL_WINDOW_SECONDS",
        "estimate_original_bed",
        "estimate_spatial_bed",
        "verify_final_file",
        "verify_final_outputs",
        "verify_original_bed",
    }
)
