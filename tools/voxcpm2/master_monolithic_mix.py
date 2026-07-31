#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dialogue-suppressed spatial-bed master for monolithic Russian dubbing.

The established constant-mix implementation remains the loudness, AAC and media
QA authority. This executable replaces only the unsafe source branch: the
requested original level controls the stereo side/ambience component, while the
full center channel (where a talking-head voice normally lives) is kept at a
very low floor. A mono sermon source therefore contributes almost no English
speech under the Russian voice, while real stereo ambience remains available.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from tools.voxcpm2.spatial_bed_contract import (
    CENTER_FLOOR_RATIO,
    MAX_CENTER_FLOOR,
    POLICY,
    SIDE_BED_RATIO,
    source_bed_levels,
)

_LEGACY_PATH = (
    Path(__file__).resolve().parent
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "master_constant_mix.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._master_constant_mix_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить production master: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_calibrate_russian_gain = _legacy.calibrate_russian_gain


def build_dialogue_suppressed_mix(
    *,
    source: Path,
    mastered_russian: Path,
    output: Path,
    source_duration: float,
    original_level: float,
    russian_gain: float,
) -> str:
    """Preserve source space without carrying an audible center dialogue bed."""
    levels = source_bed_levels(original_level)
    russian_gain = _legacy._finite(russian_gain, field="russian_gain")
    if not 0.0 < russian_gain <= 2.0:
        raise RuntimeError("russian_gain должен быть в диапазоне 0..2")
    source_duration = _legacy._finite(source_duration, field="source_duration")
    center = float(levels["center_full_mix_level"])
    side = float(levels["spatial_side_level"])
    mix_filter = (
        "[0:a]aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,asplit=2"
        "[source_full][source_side];"
        f"[source_full]volume={center:.9f}[center_floor];"
        "[source_side]pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0,"
        f"volume={side:.9f}[spatial_bed];"
        "[center_floor][spatial_bed]amix=inputs=2:duration=longest:"
        "dropout_transition=0:normalize=0[original_bed];"
        f"[1:a]asetpts=PTS-STARTPTS,highpass=f=35,volume={russian_gain:.9f}[russian];"
        "[original_bed][russian]amix=inputs=2:duration=longest:"
        "dropout_transition=0:normalize=0,"
        f"apad=pad_dur={source_duration:.6f},"
        f"atrim=duration={source_duration:.6f},"
        "asetpts=N/SR/TB[mix]"
    )
    _legacy.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-i",
            str(mastered_russian),
            "-filter_complex",
            mix_filter,
            "-map",
            "[mix]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_f32le",
            str(output),
        ]
    )
    return mix_filter


def calibrate_russian_gain(**kwargs: Any) -> dict[str, Any]:
    levels = source_bed_levels(kwargs.get("original_level"))
    report = dict(_legacy_calibrate_russian_gain(**kwargs))
    report.update(
        policy=POLICY,
        dialogue_suppression="stereo-side bed plus bounded full-center floor",
        requested_original_level=levels["requested_original_level"],
        requested_original_level_percent=levels["requested_original_level"] * 100.0,
        center_full_mix_level=levels["center_full_mix_level"],
        center_full_mix_percent=levels["center_full_mix_level"] * 100.0,
        spatial_side_level=levels["spatial_side_level"],
        spatial_side_percent=levels["spatial_side_level"] * 100.0,
        expected_total_side_level=levels["expected_total_side_level"],
        center_floor_ratio=CENTER_FLOOR_RATIO,
        max_center_floor=MAX_CENTER_FLOOR,
        mono_source_behavior="side cancels; only bounded center floor remains",
        original_dialogue_preserved_at_requested_level=False,
    )
    return report


def install() -> None:
    _legacy.build_constant_mix = build_dialogue_suppressed_mix
    _legacy.calibrate_russian_gain = calibrate_russian_gain


def main() -> None:
    install()
    _legacy.main()


__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "CENTER_FLOOR_RATIO",
        "MAX_CENTER_FLOOR",
        "POLICY",
        "SIDE_BED_RATIO",
        "build_dialogue_suppressed_mix",
        "calibrate_russian_gain",
        "install",
        "main",
        "source_bed_levels",
    }
)


if __name__ == "__main__":
    main()
