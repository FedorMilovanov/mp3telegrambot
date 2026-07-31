#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transactional release gate for calm and controlled expressive references."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2.direct_timbre_analysis import spectral_envelope, spectral_similarity

MIN_REFERENCE_SECONDS = 5.0
MAX_REFERENCE_SECONDS = 30.0
REPORT_DURATION_TOLERANCE = 0.08
MIN_IDENTITY_SPECTRAL_SIMILARITY = 0.55
IDENTITY_POLICY = "calm-and-expressive-identity-v2"


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(str(path), dtype="float32")
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.reshape(-1)
    if sample_rate <= 0 or not len(audio) or not np.isfinite(audio).all():
        raise RuntimeError(f"Некорректный voice reference: {path}")
    return audio, int(sample_rate)


def _identity_similarity(
    candidate: Path,
    identity_reference: Path | None,
) -> tuple[bool, str, float | None]:
    if identity_reference is None:
        return True, "identity-reference не задан", None
    if not identity_reference.is_file():
        return False, "не найден calm identity-reference", None
    try:
        candidate_audio, candidate_sr = _read_mono(candidate)
        identity_audio, identity_sr = _read_mono(identity_reference)
        similarity = float(
            spectral_similarity(
                spectral_envelope(candidate_audio, candidate_sr),
                spectral_envelope(identity_audio, identity_sr),
            )
        )
    except Exception as exc:
        return False, f"не рассчитано identity-сходство: {exc}", None
    if not math.isfinite(similarity):
        return False, "identity-сходство не является конечным числом", None
    if similarity < MIN_IDENTITY_SPECTRAL_SIMILARITY:
        return False, (
            f"identity spectral similarity={similarity:.4f} < "
            f"{MIN_IDENTITY_SPECTRAL_SIMILARITY:.2f}"
        ), similarity
    return True, f"identity similarity={similarity:.4f}", similarity


def _stamp_identity(
    report_path: Path,
    payload: dict[str, Any],
    *,
    identity_reference: Path | None,
    similarity: float | None,
) -> None:
    if identity_reference is None and similarity is None:
        return
    payload["identity_policy"] = IDENTITY_POLICY
    if identity_reference is not None:
        payload["identity_reference"] = str(identity_reference)
    if similarity is not None:
        payload["identity_spectral_similarity"] = round(similarity, 6)
        payload["identity_spectral_floor"] = MIN_IDENTITY_SPECTRAL_SIMILARITY
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _valid_calm_reference(
    output: Path,
    *,
    identity_reference: Path | None,
) -> tuple[bool, str]:
    report_path = output.with_suffix(".selection.json")
    if not output.is_file() or output.stat().st_size <= 0:
        return False, "calm composite WAV не создан"
    if not report_path.is_file():
        return False, "нет calm composite selection report"
    try:
        info = sf.info(str(output))
        duration = float(info.duration)
        payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return False, f"не читается calm composite: {exc}"
    if not math.isfinite(duration):
        return False, "calm composite duration не является конечным числом"
    if not MIN_REFERENCE_SECONDS <= duration <= MAX_REFERENCE_SECONDS:
        return False, (
            f"calm duration={duration:.3f}s вне "
            f"{MIN_REFERENCE_SECONDS:.1f}..{MAX_REFERENCE_SECONDS:.1f}s"
        )
    if not isinstance(payload, dict):
        return False, "calm report не является объектом"
    selected = payload.get("selected")
    if not isinstance(selected, list) or not selected:
        return False, "calm report не содержит selected windows"

    identity_ok, identity_detail, similarity = _identity_similarity(
        output,
        identity_reference,
    )
    if not identity_ok:
        return False, identity_detail
    _stamp_identity(
        report_path,
        payload,
        identity_reference=identity_reference,
        similarity=similarity,
    )
    return True, f"calm composite {duration:.3f}s; {identity_detail}"


def _valid_expressive_reference(
    output: Path,
    *,
    identity_reference: Path | None,
) -> tuple[bool, str]:
    report_path = output.with_suffix(".selection.json")
    if not output.is_file() or output.stat().st_size <= 0:
        return False, "expressive WAV не создан"
    if not report_path.is_file():
        return False, "нет expressive selection report"
    try:
        info = sf.info(str(output))
        duration = float(info.duration)
        payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return False, f"не читается expressive reference: {exc}"
    if not math.isfinite(duration):
        return False, "expressive duration не является конечным числом"
    if not MIN_REFERENCE_SECONDS <= duration <= MAX_REFERENCE_SECONDS:
        return False, (
            f"expressive duration={duration:.3f}s вне "
            f"{MIN_REFERENCE_SECONDS:.1f}..{MAX_REFERENCE_SECONDS:.1f}s"
        )
    if not isinstance(payload, dict):
        return False, "expressive report не является объектом"
    if str(payload.get("profile") or "") != "controlled_expressive":
        return False, "expressive report содержит другой profile"
    selected = payload.get("selected")
    if not isinstance(selected, list) or not selected:
        return False, "expressive report не содержит selected windows"
    try:
        reported_duration = float(payload.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        return False, "expressive report содержит некорректную duration"
    if not math.isfinite(reported_duration):
        return False, "expressive report duration не является конечным числом"
    if abs(reported_duration - duration) > REPORT_DURATION_TOLERANCE:
        return False, (
            f"expressive report duration={reported_duration:.3f}s, "
            f"WAV={duration:.3f}s"
        )

    identity_ok, identity_detail, similarity = _identity_similarity(
        output,
        identity_reference,
    )
    if not identity_ok:
        return False, identity_detail
    _stamp_identity(
        report_path,
        payload,
        identity_reference=identity_reference,
        similarity=similarity,
    )
    return True, f"controlled expressive {duration:.3f}s; {identity_detail}"


def _restore(
    *,
    backup_wav: Path,
    backup_report: Path,
    output: Path,
    report_path: Path,
) -> None:
    shutil.copy2(backup_wav, output)
    shutil.copy2(backup_report, report_path)


def build_or_keep_calm(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    output: Path,
    identity_reference: Path | None = None,
    target_seconds: float = 7.0,
) -> tuple[bool, str]:
    """Replace calm composite only when both calm and expressive identity are safe."""
    report_path = output.with_suffix(".selection.json")
    valid_calm, calm_detail = _valid_calm_reference(
        output,
        identity_reference=identity_reference,
    )
    if not valid_calm:
        raise RuntimeError("Calm composite identity gate не принят: " + calm_detail)

    backup_wav = output.with_suffix(output.suffix + ".calm-backup")
    backup_report = report_path.with_suffix(report_path.suffix + ".calm-backup")
    shutil.copy2(output, backup_wav)
    shutil.copy2(report_path, backup_report)
    try:
        built = expressive_continuity.build_controlled_expressive_reference(
            source=source,
            segments=segments,
            output=output,
            target_seconds=target_seconds,
        )
        if not built:
            _restore(
                backup_wav=backup_wav,
                backup_report=backup_report,
                output=output,
                report_path=report_path,
            )
            return False, "safe calm-reference fallback: " + calm_detail
        valid, detail = _valid_expressive_reference(
            output,
            identity_reference=identity_reference,
        )
        if valid:
            return True, detail
        _restore(
            backup_wav=backup_wav,
            backup_report=backup_report,
            output=output,
            report_path=report_path,
        )
        return False, "safe calm-reference fallback: " + detail
    except Exception:
        _restore(
            backup_wav=backup_wav,
            backup_report=backup_report,
            output=output,
            report_path=report_path,
        )
        raise
    finally:
        backup_wav.unlink(missing_ok=True)
        backup_report.unlink(missing_ok=True)


__all__ = [
    "IDENTITY_POLICY",
    "MAX_REFERENCE_SECONDS",
    "MIN_IDENTITY_SPECTRAL_SIMILARITY",
    "MIN_REFERENCE_SECONDS",
    "REPORT_DURATION_TOLERANCE",
    "build_or_keep_calm",
]
