#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality v4 master: normalize Russian once, then mix the source at exact gain.

Unlike the legacy master, the completed mixed bus is not loudness-normalized a
second time.  That prevents the requested 18% source bed and both noise floors
from being raised unpredictably after mixing.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-7000:] if capture else ""
        raise RuntimeError("Команда завершилась с ошибкой:\n" + " ".join(command) + ("\n\n" + tail if tail else ""))
    return proc


def parse_loudnorm(stderr: str) -> dict[str, str]:
    matches = re.findall(r'\{\s*"input_i".*?\}', stderr, flags=re.S)
    if not matches:
        raise RuntimeError("FFmpeg не вернул JSON loudnorm.")
    payload = json.loads(matches[-1])
    required = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    missing = required.difference(payload)
    if missing:
        raise RuntimeError("Неполный loudnorm JSON: " + ", ".join(sorted(missing)))
    return {key: str(value) for key, value in payload.items()}


def master_russian(
    input_wav: Path,
    output_wav: Path,
    *,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    # High-pass only the synthetic voice. Never filter or normalize the source bed.
    first_filter = (
        f"highpass=f=45,loudnorm=I={target_i}:LRA={target_lra}:"
        f"TP={target_tp}:print_format=json"
    )
    first = run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(input_wav),
            "-af", first_filter, "-f", "null", "-",
        ],
        capture=True,
    )
    measured = parse_loudnorm(first.stderr or "")
    second_filter = (
        f"highpass=f=45,loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:"
        f"measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}:"
        f"measured_TP={measured['input_tp']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary,"
        "alimiter=limit=0.985:level=false"
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(input_wav), "-af", second_filter,
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(output_wav),
        ]
    )
    return {
        "target_i": target_i,
        "target_lra": target_lra,
        "target_tp": target_tp,
        "first_pass": measured,
        "filter": second_filter,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Quality v4 constant source-bed master")
    parser.add_argument("--source-video", required=True)
    parser.add_argument("--russian-wav", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--mixed-video", required=True)
    parser.add_argument("--russian-only-video", required=True)
    parser.add_argument("--original-level", type=float, default=0.18)
    parser.add_argument("--target-i", type=float, default=-14.0)
    parser.add_argument("--target-lra", type=float, default=9.0)
    parser.add_argument("--target-tp", type=float, default=-1.0)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg не найден в PATH.")
    if not 0.0 <= args.original_level <= 1.0:
        raise RuntimeError("original-level должен быть в диапазоне 0..1.")

    source = Path(args.source_video).resolve()
    russian = Path(args.russian_wav).resolve()
    work_dir = Path(args.work_dir).resolve()
    mixed_video = Path(args.mixed_video).resolve()
    russian_only_video = Path(args.russian_only_video).resolve()
    if not source.is_file() or not russian.is_file():
        raise RuntimeError("Не найден source video или Russian WAV.")
    work_dir.mkdir(parents=True, exist_ok=True)
    mixed_video.parent.mkdir(parents=True, exist_ok=True)
    russian_only_video.parent.mkdir(parents=True, exist_ok=True)

    mastered_russian = work_dir / "russian_quality_v4_mastered.wav"
    mixed_wav = work_dir / "quality_v4_constant_mix.wav"
    russian_report = master_russian(
        russian,
        mastered_russian,
        target_i=float(args.target_i),
        target_lra=float(args.target_lra),
        target_tp=float(args.target_tp),
    )

    original_level = f"{float(args.original_level):.8f}"
    mix_filter = (
        f"[0:a]aresample=48000,volume={original_level}[original];"
        "[1:a]aresample=48000[russian];"
        "[original][russian]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.985:level=false[mix]"
    )
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-i", str(mastered_russian),
            "-filter_complex", mix_filter, "-map", "[mix]",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(mixed_wav),
        ]
    )

    for audio, output in ((mixed_wav, mixed_video), (mastered_russian, russian_only_video)):
        run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
                "-metadata:s:a:0", "language=rus", "-movflags", "+faststart",
                "-shortest", str(output),
            ]
        )

    report = {
        "schema_version": 4,
        "strategy": "master Russian once, then exact constant source gain; no whole-mix loudnorm",
        "original_level": float(args.original_level),
        "whole_mix_loudnorm": False,
        "limiter_auto_level": False,
        "russian_master": russian_report,
        "mix_filter": mix_filter,
        "mixed_video": str(mixed_video),
        "russian_only_video": str(russian_only_video),
    }
    mixed_video.with_suffix(".master.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Quality v4 mixed: {mixed_video}")
    print(f"Quality v4 Russian-only: {russian_only_video}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"ОШИБКА MASTER QUALITY V4: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
