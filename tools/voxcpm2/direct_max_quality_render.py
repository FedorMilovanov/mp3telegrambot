#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Timing, timeline and model-call utilities for direct VoxCPM2 production."""
from __future__ import annotations

import inspect
import math
import random
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_OUTPUT_SR,
    atempo_chain,
    probe_duration,
    run_checked,
)


def fit_without_slowdown(
    clean_path: Path,
    fitted_path: Path,
    target_duration: float,
    tail_guard: float,
) -> dict[str, Any]:
    clean_duration = probe_duration(clean_path)
    speech_slot = max(1.0, target_duration - tail_guard)
    if clean_duration > speech_slot:
        tempo = clean_duration / speech_slot
        tempo_filters = atempo_chain(tempo)
    else:
        tempo = 1.0
        tempo_filters = []
    filters = tempo_filters + [
        "afade=t=in:st=0:d=0.008",
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
            str(EXPECTED_OUTPUT_SR),
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
        "tail_guard": tail_guard,
        "tempo": tempo,
        "slowed_down": False,
        "fitted_duration": probe_duration(fitted_path),
    }


def build_timeline(
    fitted_segments: list[tuple[dict[str, Any], Path]],
    output: Path,
    total_duration: float,
) -> None:
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
            str(EXPECTED_OUTPUT_SR),
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(output),
        ]
    )
    run_checked(command)


def set_seed(seed: int, torch_module: Any) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch_module.manual_seed(seed)


def _generation_profile(
    attempt: int,
    base_cfg: float,
    base_steps: int,
) -> tuple[float, int]:
    if attempt == 1:
        return base_cfg, base_steps
    if attempt == 2:
        return min(2.20, base_cfg + 0.20), min(30, base_steps + 6)
    return max(1.50, base_cfg - 0.15), min(30, base_steps + 10)


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
) -> Any:
    parameters = inspect.signature(model.generate).parameters
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
    for name, value in optional.items():
        if name in parameters:
            kwargs[name] = value
    return model.generate(**kwargs)
