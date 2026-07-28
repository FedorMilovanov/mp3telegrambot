#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Candidate-quality adapter loaded underneath the proven v4 renderer."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

from tools.voxcpm2 import professional_audio_v45 as professional


def _flag(name: str) -> str:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return ""
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else ""


def _required_callable(namespace: dict[str, Any], name: str) -> Callable[..., Any]:
    """Resolve the stable renderer hook with a useful contract error."""
    value = namespace.get(name)
    if not callable(value):
        raise RuntimeError(
            f"Legacy VoxCPM2 renderer contract is missing callable {name!r}."
        )
    return value


def _profile(path: str) -> dict[str, float]:
    samples, sr = sf.read(path, dtype="float32")
    if np.asarray(samples).ndim > 1:
        samples = np.asarray(samples, np.float32).mean(axis=1)
    return professional.pitch_profile(np.asarray(samples), int(sr))


def _edge(samples: np.ndarray, sr: int) -> dict[str, Any]:
    audio = np.asarray(samples, np.float32).reshape(-1)
    frame, hop = max(160, int(sr * 0.02)), max(80, int(sr * 0.01))
    if len(audio) < frame:
        return {"trailing_ms": 0.0, "cut_risk": True}
    levels = np.array(
        [
            np.sqrt(np.mean(audio[pos : pos + frame] ** 2) + 1e-12)
            for pos in range(0, len(audio) - frame + 1, hop)
        ]
    )
    active = levels >= max(10 ** (-42 / 20), float(levels.max()) * 0.055)
    ids = np.where(active)[0]
    if not len(ids):
        return {"trailing_ms": 0.0, "cut_risk": True}
    trailing = max(0.0, (len(levels) - 1 - int(ids[-1])) * hop * 1000 / sr)
    tail = audio[max(0, len(audio) - int(sr * 0.03)) :]
    peak = float(np.max(np.abs(tail))) if len(tail) else 0.0
    return {"trailing_ms": trailing, "cut_risk": trailing < 35 and peak > 0.018}


legacy_path = Path(os.environ.get("VOXCPM_LEGACY_RENDERER", "")).resolve()
if not legacy_path.is_file():
    raise RuntimeError(f"Legacy renderer не найден: {legacy_path}")
legacy = runpy.run_path(str(legacy_path), run_name="voxcpm2_professional_legacy")
legacy_score = _required_callable(legacy, "candidate_score")
legacy_fit = _required_callable(legacy, "fit_without_slowdown")
legacy_main = _required_callable(legacy, "main")

# Quality v4 wraps these hooks before calling our main(). They must therefore be
# present in this adapter namespace and later forwarded into the true renderer.
log = _required_callable(legacy, "log")
set_seed = _required_callable(legacy, "set_seed")

references = {
    "extended": _profile(_flag("--extended-reference")),
    "composite": _profile(_flag("--composite-reference")),
}


def candidate_score(candidate: dict[str, Any], speech_slot: float) -> float:
    samples = np.asarray(candidate.get("samples"), np.float32).reshape(-1)
    sr = int(candidate.get("sample_rate") or 0)
    activity = professional.activity_stats(samples, sr)
    pitch = professional.pitch_profile(samples, sr)
    edge = _edge(samples, sr)
    profile_name = (
        "composite"
        if "composite" in Path(str(candidate.get("path") or "")).name.casefold()
        else "extended"
    )
    reference = references[profile_name]
    median_ratio = pitch["f0_median"] / max(
        1.0, reference.get("f0_median", 0.0)
    )
    p90_ratio = pitch["f0_p90"] / max(1.0, reference.get("f0_p90", 0.0))
    gap = activity["max_internal_gap"]
    penalty = 0.0
    if gap > 0.72:
        penalty += 130 + (gap - 0.72) * 110
    elif gap > 0.46:
        penalty += 35 + (gap - 0.46) * 90
    if median_ratio > 1.28:
        penalty += 150 + (median_ratio - 1.28) * 180
    elif median_ratio > 1.14:
        penalty += 35 + (median_ratio - 1.14) * 120
    if p90_ratio > 1.30:
        penalty += 90 + (p90_ratio - 1.30) * 100
    if edge["cut_risk"]:
        penalty += 180
    if pitch["voiced_ratio"] < 0.12:
        penalty += 80
    rejected = bool(
        gap > 0.72
        or median_ratio > 1.28
        or p90_ratio > 1.35
        or edge["cut_risk"]
    )
    measured_clipping = float(candidate.get("clipping_ratio") or 0.0)
    candidate["professional_quality"] = {
        **activity,
        **pitch,
        **edge,
        "reference_profile": profile_name,
        "reference_f0_median": reference.get("f0_median", 0.0),
        "f0_median_ratio": median_ratio,
        "f0_p90_ratio": p90_ratio,
        "measured_clipping_ratio": measured_clipping,
        "professional_penalty": penalty,
        "professional_rejected": rejected,
    }
    if rejected:
        # The proven renderer makes a third candidate when clipping_ratio exceeds
        # 0.0005. Reuse that retry signal without pretending this is tail_restart:
        # tail_restart is later destructively trimmed and requires silence_start.
        candidate["clipping_ratio"] = max(measured_clipping, 0.000501)
    return float(legacy_score(candidate, speech_slot)) + penalty


def fit_without_slowdown(
    clean_path: Path,
    fitted_path: Path,
    target_duration: float,
    tail_guard: float,
) -> dict[str, Any]:
    report = dict(
        legacy_fit(clean_path, fitted_path, target_duration, tail_guard)
    )
    samples, sr = sf.read(fitted_path, dtype="float32")
    audio = np.asarray(samples, np.float32)
    fade = min(int(sr * 0.03), len(audio) // 10)
    if fade > 1:
        ramp = np.linspace(1, 0, fade, dtype=np.float32)
        if audio.ndim == 1:
            audio[-fade:] *= ramp
        else:
            audio[-fade:] *= ramp[:, None]
        sf.write(fitted_path, audio, int(sr), subtype="PCM_24")
    report.update(professional.activity_stats(audio, int(sr)))
    report.update(
        quality_version="voxcpm2-quality-v4.5",
        edge_fade_ms=30,
    )
    return report


def main() -> None:
    """Forward every hook patched by Quality v4 into the true renderer."""
    legacy["candidate_score"] = globals()["candidate_score"]
    legacy["fit_without_slowdown"] = globals()["fit_without_slowdown"]
    legacy["log"] = globals()["log"]
    legacy["set_seed"] = globals()["set_seed"]
    legacy_main()


if __name__ == "__main__":
    main()
