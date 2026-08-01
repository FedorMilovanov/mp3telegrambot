#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable-identity retries and monolithic timeline assembly."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from services.speech_backends import BackendGenerationProfileRequest, default_backend
from tools.voxcpm2 import direct_monolith_contract
from tools.voxcpm2 import direct_timeline_delivery_qa
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
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

ADAPTIVE_RETRY_POLICY = "stable-identity-candidate-retry-v3"
GENERATION_PROFILE_DELEGATION_POLICY = "backend-owned-attempt-profile-v1"
TIMELINE_COMPACTION_POLICY = "no-late-shift-monolithic-assembly-v2"
FADE_POLICY = "cadence-aware-short-boundary-envelope-v1"
HOOK_SYNC_POLICY = "facade-runtime-hook-sync-v2"
_legacy_build_timeline = _legacy.build_timeline
_DEFAULT_PROBE_DURATION = probe_duration
_DEFAULT_RUN_CHECKED = run_checked
_DEFAULT_TIMELINE_QA = direct_timeline_delivery_qa


def _sync_legacy_hooks() -> None:
    """Expose explicit facade injections without erasing direct legacy patches.

    Tests and clean runtimes use both supported seams: assigning a hook on this
    facade and assigning it on ``_legacy``.  A default facade binding must not
    overwrite a deliberate legacy replacement immediately before execution.
    """
    facade_probe = globals().get("probe_duration")
    if callable(facade_probe) and facade_probe is not _DEFAULT_PROBE_DURATION:
        _legacy.probe_duration = facade_probe
    facade_runner = globals().get("run_checked")
    if callable(facade_runner) and facade_runner is not _DEFAULT_RUN_CHECKED:
        _legacy.run_checked = facade_runner
    facade_qa = globals().get("direct_timeline_delivery_qa")
    if facade_qa is not None and facade_qa is not _DEFAULT_TIMELINE_QA:
        _legacy.direct_timeline_delivery_qa = facade_qa


def _generation_profile(attempt: int, base_cfg: float, base_steps: int) -> tuple[float, int]:
    """Compatibility wrapper around the selected backend's typed planner."""
    plan = default_backend().plan_generation_profile(
        BackendGenerationProfileRequest(
            attempt=attempt,
            base_backend_options={"cfg": base_cfg, "steps": base_steps},
            metadata={"policy": GENERATION_PROFILE_DELEGATION_POLICY},
        )
    )
    try:
        cfg = float(plan.backend_options["cfg"])
        steps = int(plan.backend_options["steps"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            "Backend generation profile не содержит совместимые cfg/steps."
        ) from exc
    return cfg, steps


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
    output_sample_rate: int = EXPECTED_OUTPUT_SR,
) -> dict[str, Any]:
    output_sample_rate = int(output_sample_rate)
    if output_sample_rate <= 0:
        raise ValueError("output_sample_rate должен быть > 0.")
    _sync_legacy_hooks()
    clean_duration = _legacy.probe_duration(Path(clean_path))
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
    _legacy.run_checked(
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
            str(output_sample_rate),
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
        "fitted_duration": _legacy.probe_duration(Path(fitted_path)),
    }


def build_timeline(
    fitted_segments: list[tuple[dict[str, Any], Path]],
    output: Path,
    total_duration: float,
    output_sample_rate: int = EXPECTED_OUTPUT_SR,
) -> None:
    output_sample_rate = int(output_sample_rate)
    if output_sample_rate <= 0:
        raise ValueError("output_sample_rate должен быть > 0.")
    print(
        "🧩 Monolithic timeline: authored starts preserved; linked phrases must pass "
        "duration/gap QA without late compaction",
        flush=True,
    )
    _sync_legacy_hooks()
    _legacy_build_timeline(
        fitted_segments,
        output,
        total_duration,
        output_sample_rate=output_sample_rate,
    )


_sync_legacy_hooks()
_legacy._generation_profile = _generation_profile
_legacy.fit_without_slowdown = fit_without_slowdown
_legacy.build_timeline = build_timeline

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "ADAPTIVE_RETRY_POLICY",
        "FADE_POLICY",
        "GENERATION_PROFILE_DELEGATION_POLICY",
        "HOOK_SYNC_POLICY",
        "TIMELINE_COMPACTION_POLICY",
        "_generation_profile",
        "_sync_legacy_hooks",
        "build_timeline",
        "fit_without_slowdown",
    }
)
