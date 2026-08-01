#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mode-aware final Dub Studio media QA.

The sibling module remains the codec, duration, loudness and A/V authority. This
facade preserves the historical zero-safe two-branch regression for legacy and
Gemini modes. Ready-SRT direct mode uses a stereo post-AAC contract: the source
center/dialogue must stay at a bounded floor, source side/space is preserved when
it actually exists, and the encoded Russian branch must remain stable.
"""
from __future__ import annotations

import importlib.util
import sys
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2 import final_media_spatial_bed

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "final_media_qa.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._final_media_qa_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый final media QA: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_verify_final_file = _legacy.verify_final_file
# Explicit aliases keep the dynamic facade's runtime monkeypatch seams visible
# to static analysis as well as to verify_final_file().
probe_media = _legacy.probe_media
measure_loudness = _legacy.measure_loudness

ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v2"
SPATIAL_BED_POLICY = final_media_spatial_bed.POLICY
# Public compatibility constant used by existing health/report readers.
REPORT_SCHEMA = "dub-final-media-qa-v6"
CURRENT_REPORT_SCHEMA = "dub-final-media-qa-v7"
ORIGINAL_ABSOLUTE_MODE_MAX_LEVEL = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)
ORIGINAL_LOCAL_ABSOLUTE_SPREAD = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)


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
    """Backward-compatible full-source regression, including expected level 0."""
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
    window = max(1, int(sample_rate * float(_legacy.ORIGINAL_WINDOW_SECONDS)))
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
    lag, correlation = _legacy._estimate_alignment_lag(
        alignment_reference,
        mixed,
        sample_rate=sample_rate,
    )
    source, mixed, russian = _legacy._align_three(source, mixed, russian, lag)
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

    solved = _legacy._solve_two_branch(source, russian, mixed)
    if solved is None:
        failures.append("глобальная двухветочная регрессия вырождена")
        return result
    original, russian_gain, condition = solved
    tolerance = float(_legacy.ORIGINAL_LEVEL_TOLERANCE)
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
    required = min(int(_legacy.ORIGINAL_MIN_LOCAL_WINDOWS), available)
    result.update(
        local_available_full_windows=available,
        local_required_windows=required,
    )
    result["limits"]["minimum_local_windows_required"] = required
    result["limits"]["available_full_windows"] = available
    local: list[float] = []
    for start in range(0, length - window + 1, window):
        stop = start + window
        solved_window = _legacy._solve_two_branch(
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
        elif spread_db is not None and spread_db > float(_legacy.ORIGINAL_LOCAL_SPREAD_DB):
            failures.append(
                f"локальный разброс={spread_db:.3f} dB "
                f"> {float(_legacy.ORIGINAL_LOCAL_SPREAD_DB):.2f} dB"
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


_legacy.ORIGINAL_BED_POLICY = ORIGINAL_BED_POLICY
_legacy.estimate_original_bed = estimate_original_bed
_legacy.verify_final_file = verify_final_file
_legacy.verify_original_bed = verify_original_bed
_legacy.verify_final_outputs = verify_final_outputs

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
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
