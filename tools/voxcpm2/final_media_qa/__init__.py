#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-safe facade for the final Dub Studio media QA.

The historical implementation remains in ``final_media_qa.py``. A package wins
module resolution over a sibling ``.py`` file, so production imports enter here.
We load the proven implementation under a private name and replace only the
original-bed regression edge cases: an explicit zero/near-zero source bed and
clips with fewer than three complete two-second windows.
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "final_media_qa.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._final_media_qa_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый final media QA: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

# Preserve the complete historical module surface, including private helpers
# used by focused tests and diagnostics. Overridden names are assigned below.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_verify_final_file = _legacy.verify_final_file
_legacy_verify_original_bed = _legacy.verify_original_bed
_legacy_verify_final_outputs = _legacy.verify_final_outputs

LEGACY_ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v1"
ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v2"
ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)
ORIGINAL_LOCAL_ABSOLUTE_SPREAD = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)
REPORT_SCHEMA = "dub-final-media-qa-v6"


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
    """Estimate a constant source bed without rejecting valid zero-bed output."""
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
            source[start : start + window],
            russian[start : start + window],
            mixed[start : start + window],
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


def verify_final_file(
    path: Path,
    *,
    source_duration: float,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    """Delegate while preserving public monkeypatch hooks."""
    _legacy.probe_media = probe_media
    _legacy.measure_loudness = measure_loudness
    return _legacy_verify_final_file(
        path,
        source_duration=source_duration,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )


def verify_original_bed(
    *,
    source_duration: float,
    mixed_video: Path,
    russian_only_video: Path,
) -> dict[str, Any]:
    _legacy.ORIGINAL_BED_POLICY = ORIGINAL_BED_POLICY
    _legacy.estimate_original_bed = estimate_original_bed
    return _legacy_verify_original_bed(
        source_duration=source_duration,
        mixed_video=mixed_video,
        russian_only_video=russian_only_video,
    )


def _upgrade_report(path: Path, report: dict[str, Any] | None = None) -> dict[str, Any] | None:
    payload = report
    if payload is None:
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        payload = loaded if isinstance(loaded, dict) else None
    if not isinstance(payload, dict):
        return None
    payload["schema_version"] = REPORT_SCHEMA
    payload["measurement"] = (
        "FFmpeg loudnorm / ITU-R BS.1770 / EBU R128 + aligned post-AAC "
        "zero-safe two-branch regression"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return payload


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
    # Keep legacy orchestration intact, but route all public hooks through this
    # facade so tests and diagnostics can still monkeypatch the documented API.
    _legacy.ORIGINAL_BED_POLICY = ORIGINAL_BED_POLICY
    _legacy.estimate_original_bed = estimate_original_bed
    _legacy.verify_final_file = verify_final_file
    _legacy.verify_original_bed = verify_original_bed
    try:
        report = _legacy_verify_final_outputs(
            source_duration=source_duration,
            mixed_video=mixed_video,
            russian_only_video=russian_only_video,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
            report_path=report_path,
        )
    except RuntimeError:
        _upgrade_report(report_path)
        raise
    upgraded = _upgrade_report(report_path, report)
    if upgraded is None:
        raise RuntimeError("Не удалось обновить schema финального media-QA.")
    return upgraded


# Ensure direct callers of legacy verification resolve the new estimator.
_legacy.ORIGINAL_BED_POLICY = ORIGINAL_BED_POLICY
_legacy.estimate_original_bed = estimate_original_bed

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "LEGACY_ORIGINAL_BED_POLICY",
        "ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL",
        "ORIGINAL_BED_POLICY",
        "ORIGINAL_LOCAL_ABSOLUTE_SPREAD",
        "REPORT_SCHEMA",
        "estimate_original_bed",
        "verify_final_file",
        "verify_final_outputs",
        "verify_original_bed",
    }
)
