#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Timing and timeline utilities for direct speech-backend production.

The low-level model call is owned by the selected backend session; the legacy
``_generate`` and ``_generation_profile`` helpers remain only as compatibility
seams for older facades.
"""
from __future__ import annotations

import inspect
import math
import random
from pathlib import Path
from typing import Any

import numpy as np

from services.speech_backends import BackendGenerationProfileRequest, default_backend
from tools.voxcpm2 import direct_timeline_delivery_qa
from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_OUTPUT_SR,
    SPEECH_SLOT_POLICY,
    atempo_chain,
    probe_duration,
    run_checked,
    speech_slot_seconds,
)


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
    clean_duration = probe_duration(clean_path)
    speech_slot = speech_slot_seconds(target_duration, tail_guard)
    if clean_duration > speech_slot:
        tempo = clean_duration / speech_slot
        tempo_filters = atempo_chain(tempo)
    else:
        tempo = 1.0
        tempo_filters = []

    # Fade around the real spoken material, before padding. The previous 8 ms
    # one-sided fade behaved almost like a hard edit: Russian phrases appeared
    # abruptly and the fixed English bed seemed to jump between segments. A
    # short equal-purpose in/out envelope keeps consonants intact while making
    # phrase boundaries perceptually continuous.
    rendered_speech_duration = min(clean_duration / max(tempo, 1e-9), speech_slot)
    fade_in = min(0.032, max(0.010, rendered_speech_duration * 0.10))
    fade_out = min(0.060, max(0.018, rendered_speech_duration * 0.16))
    fade_out_start = max(fade_in, rendered_speech_duration - fade_out)
    fade_out = max(0.008, rendered_speech_duration - fade_out_start)

    filters = tempo_filters + [
        f"afade=t=in:st=0:d={fade_in:.6f}",
        f"afade=t=out:st={fade_out_start:.6f}:d={fade_out:.6f}",
        f"apad=pad_dur={target_duration:.6f}",
        f"atrim=duration={target_duration:.6f}",
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
            str(output_sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(fitted_path),
        ]
    )
    return {
        "clean_duration": clean_duration,
        "target_duration": target_duration,
        "speech_slot": speech_slot,
        "speech_slot_policy": SPEECH_SLOT_POLICY,
        "tail_guard": tail_guard,
        "tempo": tempo,
        "slowed_down": False,
        "rendered_speech_duration": rendered_speech_duration,
        "fade_in_seconds": fade_in,
        "fade_out_start_seconds": fade_out_start,
        "fade_out_seconds": fade_out,
        "fitted_duration": probe_duration(fitted_path),
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
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for _, path in fitted_segments:
        command.extend(["-i", str(path)])
    filters: list[str] = []
    labels: list[str] = []
    for index, (segment, _) in enumerate(fitted_segments):
        delay_ms = int(round(float(segment["start"]) * 1000.0)) + int(
            segment.get("start_delay_ms", 0)
        )
        label = f"s{index}"
        filters.append(f"[{index}:a]adelay={delay_ms}:all=1[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,"
        + f"apad=pad_dur={total_duration:.6f},"
        + f"atrim=duration={total_duration:.6f},"
        + "highpass=f=35,"
        + "alimiter=limit=0.985:level=false:latency=true[out]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            str(output_sample_rate),
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(output),
        ]
    )
    run_checked(command)
    direct_timeline_delivery_qa.verify_timeline_delivery(output, fitted_segments)


def set_seed(seed: int, torch_module: Any) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch_module.manual_seed(seed)


def _generation_profile(
    attempt: int,
    base_cfg: float,
    base_steps: int,
) -> tuple[float, int]:
    """Compatibility wrapper around the backend-owned profile planner."""
    plan = default_backend().plan_generation_profile(
        BackendGenerationProfileRequest(
            attempt=attempt,
            base_backend_options={"cfg": base_cfg, "steps": base_steps},
            metadata={"compatibility_seam": "direct_max_quality_render"},
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


def _needs_normalization(text: str) -> bool:
    return bool(__import__("re").search(r"\d|[%№$€£]", text))


def _generate(
    model: Any,
    *,
    text: str,
    reference: Path,
    cfg: float,
    steps: int,
    min_len: int,
    max_len: int,
    seed: int,
    continuation_reference: Path | None = None,
    continuation_text: str = "",
) -> Any:
    parameters = inspect.signature(model.generate).parameters
    accepts_keyword_options = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    generation_max_len = min(
        512,
        max(int(max_len), int(math.ceil(max_len * 1.45))),
    )
    kwargs: dict[str, Any] = {
        "text": text,
        "reference_wav_path": str(reference),
        "cfg_value": float(cfg),
        "inference_timesteps": int(steps),
        "min_len": int(min_len),
        "max_len": generation_max_len,
        "normalize": _needs_normalization(text),
        "denoise": False,
    }
    optional = {
        "retry_badcase": True,
        "retry_badcase_max_times": 2,
        "retry_badcase_ratio_threshold": 6.0,
        "seed": int(seed),
    }
    if continuation_reference is not None and continuation_reference.is_file():
        if accepts_keyword_options or "prompt_wav_path" in parameters:
            kwargs["prompt_wav_path"] = str(continuation_reference)
        if (
            accepts_keyword_options or "prompt_text" in parameters
        ) and str(continuation_text or "").strip():
            kwargs["prompt_text"] = str(continuation_text).strip()
        elif "reference_text" in parameters and str(continuation_text or "").strip():
            kwargs["reference_text"] = str(continuation_text).strip()
    for name, value in optional.items():
        if accepts_keyword_options or name in parameters:
            kwargs[name] = value
    return model.generate(**kwargs)

_BASE_ALL = tuple(globals().get('__all__', ()))




from tools.voxcpm2 import direct_monolith_contract


from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_OUTPUT_SR,
)

ADAPTIVE_RETRY_POLICY = "stable-identity-candidate-retry-v2"

GENERATION_PROFILE_DELEGATION_POLICY = "backend-owned-attempt-profile-v1"

TIMELINE_COMPACTION_POLICY = "no-late-shift-monolithic-assembly-v2"

FADE_POLICY = "cadence-aware-short-boundary-envelope-v1"

HOOK_SYNC_POLICY = "facade-runtime-hook-sync-v2"

_legacy_build_timeline = build_timeline

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
        probe_duration = facade_probe
    facade_runner = globals().get("run_checked")
    if callable(facade_runner) and facade_runner is not _DEFAULT_RUN_CHECKED:
        run_checked = facade_runner
    facade_qa = globals().get("direct_timeline_delivery_qa")
    if facade_qa is not None and facade_qa is not _DEFAULT_TIMELINE_QA:
        direct_timeline_delivery_qa = facade_qa

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
        "fitted_duration": probe_duration(Path(fitted_path)),
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

_generation_profile = _generation_profile

fit_without_slowdown = fit_without_slowdown

build_timeline = build_timeline

__all__ = sorted(
    set(_BASE_ALL)
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
