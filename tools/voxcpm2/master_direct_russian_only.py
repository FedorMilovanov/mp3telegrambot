#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-owned Russian-only master for direct ready-SRT dubbing.

Direct dubbing must never mix the speech-bearing original soundtrack under the
Russian voice. The source video is used only for duration/video muxing and audit
metadata. Common loudness, AAC and final-media QA primitives remain owned by the
established constant-mix master module and are called explicitly; no imported
module is patched or rebound.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.voxcpm2 import spatial_bed_contract
from tools.voxcpm2.examples.john_piper_z20py4yqhyq import master_constant_mix as base

POLICY = spatial_bed_contract.POLICY
SOURCE_BED_POLICY = spatial_bed_contract.SOURCE_BED_POLICY


def build_russian_only_mix(
    *,
    source: Path,
    mastered_russian: Path,
    output: Path,
    source_duration: float,
    original_level: float,
    russian_gain: float,
) -> str:
    """Build PCM from Russian speech only; the source audio is never an input."""
    del source
    levels = spatial_bed_contract.source_bed_levels(original_level)
    if float(levels["applied_original_level"]) != 0.0:
        raise RuntimeError("Russian-only direct master received a non-zero source bed.")
    gain = base._finite(russian_gain, field="russian_gain")
    if not 0.0 < gain <= spatial_bed_contract.MAX_RUSSIAN_GAIN:
        raise RuntimeError("russian_gain is outside the direct-master contract.")
    duration = base._finite(source_duration, field="source_duration")
    if duration <= 0.0:
        raise RuntimeError("source_duration must be > 0.")
    audio_filter = (
        "asetpts=PTS-STARTPTS,highpass=f=35,"
        f"volume={gain:.9f},"
        f"apad=pad_dur={duration:.6f},"
        f"atrim=duration={duration:.6f},"
        "asetpts=N/SR/TB"
    )
    base.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(mastered_russian),
            "-af",
            audio_filter,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_f32le",
            str(output),
        ]
    )
    return audio_filter


def calibrate_russian_only_gain(
    *,
    source: Path,
    mastered_russian: Path,
    output: Path,
    work_dir: Path,
    source_duration: float,
    original_level: float,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    """Calibrate only Russian gain while preserving the zero-source contract."""
    levels = spatial_bed_contract.source_bed_levels(original_level)
    safe_peak = min(float(target_tp) - 0.20, -1.20)
    low = max(0.05, spatial_bed_contract.MIN_RUSSIAN_GAIN)
    high = min(1.35, spatial_bed_contract.MAX_RUSSIAN_GAIN)
    best: tuple[float, dict[str, float], str] | None = None
    attempts: list[dict[str, Any]] = []
    candidate = work_dir / "direct_russian_candidate.wav"

    for _index in range(11):
        gain = (low + high) / 2.0
        graph = build_russian_only_mix(
            source=source,
            mastered_russian=mastered_russian,
            output=candidate,
            source_duration=source_duration,
            original_level=original_level,
            russian_gain=gain,
        )
        measured = base.measure_loudness(
            candidate,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        )
        loudness_ok = measured["integrated_lufs"] <= target_i + 0.05
        peak_ok = measured["true_peak_dbtp"] <= safe_peak
        attempts.append(
            {
                "russian_gain": round(gain, 8),
                **{key: round(value, 5) for key, value in measured.items()},
                "loudness_ok": loudness_ok,
                "peak_ok": peak_ok,
            }
        )
        if loudness_ok and peak_ok:
            best = (gain, measured, graph)
            low = gain
        else:
            high = gain

    if best is None:
        gain = max(0.05, spatial_bed_contract.MIN_RUSSIAN_GAIN)
        graph = build_russian_only_mix(
            source=source,
            mastered_russian=mastered_russian,
            output=candidate,
            source_duration=source_duration,
            original_level=original_level,
            russian_gain=gain,
        )
        measured = base.measure_loudness(
            candidate,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        )
        if (
            measured["integrated_lufs"] > target_i + base.LOUDNESS_TOLERANCE_LU
            or measured["true_peak_dbtp"] > -1.0
        ):
            raise RuntimeError(
                "Russian-only direct master cannot meet final loudness/peak QA: "
                f"{measured}"
            )
        best = (gain, measured, graph)

    gain, _measured, graph = best
    final_graph = build_russian_only_mix(
        source=source,
        mastered_russian=mastered_russian,
        output=output,
        source_duration=source_duration,
        original_level=original_level,
        russian_gain=gain,
    )
    final_measured = base.measure_loudness(
        output,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    candidate.unlink(missing_ok=True)
    loudness_error = abs(final_measured["integrated_lufs"] - target_i)
    if loudness_error > base.LOUDNESS_TOLERANCE_LU:
        raise RuntimeError(
            "Russian-only direct master missed loudness tolerance: "
            f"{final_measured['integrated_lufs']:.2f} LUFS vs {target_i:.2f}."
        )
    if final_measured["true_peak_dbtp"] > -1.0:
        raise RuntimeError(
            "Russian-only direct master exceeds safe true peak: "
            f"{final_measured['true_peak_dbtp']:.2f} dBTP."
        )
    return {
        "policy": POLICY,
        "source_bed_policy": SOURCE_BED_POLICY,
        "source_bed_applied": False,
        "source_bed_disabled_reason": levels["source_bed_disabled_reason"],
        "requested_original_level": float(levels["requested_original_level"]),
        "applied_original_level": 0.0,
        "russian_gain": gain,
        "safe_peak_target_dbtp": safe_peak,
        "loudness_tolerance_lu": base.LOUDNESS_TOLERANCE_LU,
        "measurement": final_measured,
        "attempts": attempts,
        "filter": final_graph or graph,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Russian-only direct Dub master with final AAC QA.")
    parser.add_argument("--source-video", required=True)
    parser.add_argument("--russian-wav", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--mixed-video", required=True)
    parser.add_argument("--russian-only-video", required=True)
    parser.add_argument("--original-level", type=float, default=0.18)
    parser.add_argument("--target-i", type=float, default=-16.0)
    parser.add_argument("--target-lra", type=float, default=8.0)
    parser.add_argument("--target-tp", type=float, default=-1.5)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe not found in PATH.")

    source = Path(args.source_video).resolve()
    russian = Path(args.russian_wav).resolve()
    work_dir = Path(args.work_dir).resolve()
    mixed_video = Path(args.mixed_video).resolve()
    russian_only_video = Path(args.russian_only_video).resolve()
    if not source.is_file():
        raise RuntimeError(f"Source video not found: {source}")
    if not russian.is_file():
        raise RuntimeError(f"Russian WAV not found: {russian}")

    original_level = base._finite(args.original_level, field="original_level")
    if not 0.0 <= original_level <= 1.0:
        raise RuntimeError("original-level must be in 0..1.")
    target_i = base._bounded(args.target_i, field="target_i", limits=base.TARGET_I_RANGE)
    target_lra = base._bounded(args.target_lra, field="target_lra", limits=base.TARGET_LRA_RANGE)
    target_tp = base._bounded(args.target_tp, field="target_tp", limits=base.TARGET_TP_RANGE)

    work_dir.mkdir(parents=True, exist_ok=True)
    mixed_video.parent.mkdir(parents=True, exist_ok=True)
    russian_only_video.parent.mkdir(parents=True, exist_ok=True)
    source_duration = base.probe_duration(source)
    mastered_russian = work_dir / "russian_only_mastered.wav"
    direct_master = work_dir / "direct_russian_master.wav"

    russian_master = base.two_pass_master(
        russian,
        mastered_russian,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    direct_report = calibrate_russian_only_gain(
        source=source,
        mastered_russian=mastered_russian,
        output=direct_master,
        work_dir=work_dir,
        source_duration=source_duration,
        original_level=original_level,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )

    base.encode_upload_mp4(
        source=source,
        audio=direct_master,
        output=mixed_video,
        source_duration=source_duration,
    )
    base.encode_upload_mp4(
        source=source,
        audio=mastered_russian,
        output=russian_only_video,
        source_duration=source_duration,
    )
    verification_path = work_dir / "final_media_verification.json"
    final_verification = base.verify_final_outputs(
        source_duration=source_duration,
        mixed_video=mixed_video,
        russian_only_video=russian_only_video,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
        report_path=verification_path,
    )
    report = {
        "schema_version": "russian-only-direct-master-v1",
        "policy": POLICY,
        "source_bed_policy": SOURCE_BED_POLICY,
        "requested_original_level": original_level,
        "applied_original_level": 0.0,
        "source_duration": source_duration,
        "target": {
            "integrated_lufs": target_i,
            "lra": target_lra,
            "true_peak_db": target_tp,
        },
        "russian_master": russian_master,
        "direct_master": direct_report,
        "mixed_video": str(mixed_video),
        "russian_only_video": str(russian_only_video),
        "final_media_verification": final_verification,
        "final_media_verification_path": str(verification_path),
    }
    mixed_video.with_suffix(".master.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"DIRECT MASTER ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
