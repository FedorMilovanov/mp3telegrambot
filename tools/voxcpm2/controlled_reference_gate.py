#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transactional release gate for the controlled expressive voice reference."""
from __future__ import annotations

import json
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


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(str(path), dtype="float32")
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1), int(sample_rate)


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
    reported_duration = float(payload.get("duration_seconds") or 0.0)
    if abs(reported_duration - duration) > REPORT_DURATION_TOLERANCE:
        return False, (
            f"expressive report duration={reported_duration:.3f}s, "
            f"WAV={duration:.3f}s"
        )

    identity_similarity: float | None = None
    if identity_reference is not None:
        if not identity_reference.is_file():
            return False, "не найден calm identity-reference"
        try:
            expressive_audio, expressive_sr = _read_mono(output)
            identity_audio, identity_sr = _read_mono(identity_reference)
            identity_similarity = spectral_similarity(
                spectral_envelope(expressive_audio, expressive_sr),
                spectral_envelope(identity_audio, identity_sr),
            )
        except Exception as exc:
            return False, f"не рассчитано identity-сходство: {exc}"
        if identity_similarity < MIN_IDENTITY_SPECTRAL_SIMILARITY:
            return False, (
                f"identity spectral similarity={identity_similarity:.4f} < "
                f"{MIN_IDENTITY_SPECTRAL_SIMILARITY:.2f}"
            )
        payload["identity_reference"] = str(identity_reference)
        payload["identity_spectral_similarity"] = round(identity_similarity, 6)
        payload["identity_spectral_floor"] = MIN_IDENTITY_SPECTRAL_SIMILARITY
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    detail = f"controlled expressive {duration:.3f}s"
    if identity_similarity is not None:
        detail += f"; identity similarity={identity_similarity:.4f}"
    return True, detail


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
    """Replace calm composite only when the expressive result is release-safe."""
    report_path = output.with_suffix(".selection.json")
    if not output.is_file() or not report_path.is_file():
        raise RuntimeError(
            "Перед expressive-отбором отсутствует транзакционный calm composite."
        )

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
            return False, "safe calm-reference fallback: expressive windows not found"
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
    "MAX_REFERENCE_SECONDS",
    "MIN_IDENTITY_SPECTRAL_SIMILARITY",
    "MIN_REFERENCE_SECONDS",
    "REPORT_DURATION_TOLERANCE",
    "build_or_keep_calm",
]
