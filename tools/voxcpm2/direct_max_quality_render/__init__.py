#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable-identity retries and monolithic timeline assembly."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from tools.voxcpm2 import direct_monolith_contract
from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_OUTPUT_SR,
    SPEECH_SLOT_POLICY,
    atempo_chain,
    probe_duration,
    run_checked,
    speech_slot_seconds,
)

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_max_quality_render.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_max_quality_render_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить direct render utilities: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

ADAPTIVE_RETRY_POLICY = "stable-identity-candidate-retry-v2"
TIMELINE_COMPACTION_POLICY = "no-late-shift-monolithic-assembly-v2"
FADE_POLICY = "cadence-aware-short-boundary-envelope-v1"
_legacy_build_timeline = _legacy.build_timeline


def _generation_profile(attempt: int, base_cfg: float, base_steps: int) -> tuple[float, int]:
    """Keep rescue trajectories close enough to one speaker identity."""
    attempt = int(attempt)
    base_cfg = float(base_cfg)
    base_steps = max(1, int(base_steps))
    if attempt == 1:
        return base_cfg, base_steps
    if attempt == 2:
        return min(2.15, base_cfg + 0.08), min(30, base_steps + 6)
    if attempt == 3:
        return max(1.50, base_cfg - 0.08), min(32, base_steps + 10)
    if attempt == 4:
        return min(2.15, max(1.50, base_cfg + 0.03)), min(36, base_steps + 14)
    if attempt == 5:
        return max(1.45, base_cfg - 0.12), min(40, base_steps + 18)
    raise ValueError(f"Неподдерживаемая попытка VoxCPM: {attempt}")


def _fade_contract(rendered_speech_duration: float) -> tuple[float, float]:
    segment = direct_monolith_contract.current_segment() or {}
    cadence = str(segment.get("cadence_type") or "")
    duration = max(0.05, float(rendered_speech_duration))
    if cadence in {"linked", "continuation"}:
        fade_in = min(0.016, max(0.006, duration * 0.025))
        fade_out = min(0.014, max(0.006, duration * 0.020))
    elif cadence == "question":
        fade_in = min(0.022, max(0.009, duration * 0.035))
        fade_out = min(0.026, max(0.010, duration * 0.045))
    else:
        fade_in = min(0.022, max(0.009, duration * 0.035))
        fade_out = min(0.036, max(0.012, duration * 0.060))
    return fade_in, fade_out


def fit_without_slowdown(
    clean_path: Path,
    fitted_path: Path,
    target_duration: float,
    tail_guard: float,
) -> dict[str, Any]:
    clean_duration = probe_duration(Path(clean_path))
    speech_slot = speech_slot_seconds(target_duration, tail_guard)
    if clean_duration > speech_slot:
        tempo = clean_duration / speech_slot
        tempo_filters = atempo_chain(tempo)
    else:
        tempo = 1.0
        tempo_filters = []

    rendered = min(clean_duration / max(tempo, 1e-9), speech_slot)
    fade_in, fade_out = _fade_contract(rendered)
    fade_out_start = max(fade_in, rendered - fade_out)
    fade_out = max(0.004, rendered - fade_out_start)
    filters = tempo_filters + [
        f"afade=t=in:st=0:d={fade_in:.6f}",
        f"afade=t=out:st={fade_out_start:.6f}:d={fade_out:.6f}",
        f"apad=pad_dur={float(target_duration):.6f}",
        f"atrim=duration={float(target_duration):.6f}",
        "asetpts=N/SR/TB",
    ]
    run_checked(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(clean_path),
            "-af",
            ",".join(filters),
            "-ar",
            str(EXPECTED_OUTPUT_SR),
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(fitted_path),
        ]
    )
    segment = direct_monolith_contract.current_segment() or {}
    return {
        "clean_duration": clean_duration,
        "target_duration": float(target_duration),
        "speech_slot": speech_slot,
        "speech_slot_policy": SPEECH_SLOT_POLICY,
        "tail_guard": float(tail_guard),
        "tempo": tempo,
        "slowed_down": False,
        "rendered_speech_duration": rendered,
        "fade_policy": FADE_POLICY,
        "cadence_type": str(segment.get("cadence_type") or ""),
        "fade_in_seconds": fade_in,
        "fade_out_start_seconds": fade_out_start,
        "fade_out_seconds": fade_out,
        "fitted_duration": probe_duration(Path(fitted_path)),
    }


def build_timeline(
    fitted_segments: list[tuple[dict[str, Any], Path]],
    output: Path,
    total_duration: float,
) -> None:
    """Assemble at authored cue starts; never disguise short speech by late shifting."""
    print(
        "🧩 Monolithic timeline: authored starts preserved; linked phrases must pass "
        "duration/gap QA without late compaction",
        flush=True,
    )
    _legacy_build_timeline(fitted_segments, output, total_duration)


_legacy._generation_profile = _generation_profile
_legacy.fit_without_slowdown = fit_without_slowdown
_legacy.build_timeline = build_timeline

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "ADAPTIVE_RETRY_POLICY",
        "FADE_POLICY",
        "TIMELINE_COMPACTION_POLICY",
        "_generation_profile",
        "build_timeline",
        "fit_without_slowdown",
    }
)
