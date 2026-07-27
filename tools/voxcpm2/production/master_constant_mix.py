#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = ""
        if capture:
            tail = (proc.stderr or proc.stdout or "")[-6000:]
        raise RuntimeError(
            "Команда завершилась с ошибкой:\n"
            + " ".join(command)
            + ("\n\n" + tail if tail else "")
        )
    return proc


def parse_loudnorm(stderr: str) -> dict[str, str]:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.S)
    if not matches:
        raise RuntimeError("FFmpeg не вернул JSON loudnorm.")
    payload = json.loads(matches[-1])
    required = {
        "input_i",
        "input_tp",
        "input_lra",
        "input_thresh",
        "target_offset",
    }
    missing = required.difference(payload)
    if missing:
        raise RuntimeError(
            "Неполный loudnorm JSON: " + ", ".join(sorted(missing))
        )
    return {key: str(value) for key, value in payload.items()}


def two_pass_master(
    input_wav: Path,
    output_wav: Path,
    *,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    first_filter = (
        f"loudnorm=I={target_i}:LRA={target_lra}:"
        f"TP={target_tp}:print_format=json"
    )
    first = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-y",
            "-i",
            str(input_wav),
            "-af",
            first_filter,
            "-f",
            "null",
            "-",
        ],
        capture=True,
    )
    measured = parse_loudnorm(first.stderr or "")

    second_filter = (
        f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:"
        f"measured_I={measured['input_i']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:"
        "linear=true:print_format=summary,"
        "alimiter=limit=0.985"
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_wav),
            "-af",
            second_filter,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(output_wav),
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
    parser = argparse.ArgumentParser(
        description="Constant-level English/Russian mix and two-pass master."
    )
    parser.add_argument("--source-video", required=True)
    parser.add_argument("--russian-wav", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--mixed-video", required=True)
    parser.add_argument("--russian-only-video", required=True)
    parser.add_argument("--original-level", type=float, default=0.25)
    parser.add_argument("--target-i", type=float, default=-14.0)
    parser.add_argument("--target-lra", type=float, default=9.0)
    parser.add_argument("--target-tp", type=float, default=-1.0)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg не найден в PATH.")

    source = Path(args.source_video).resolve()
    russian = Path(args.russian_wav).resolve()
    work_dir = Path(args.work_dir).resolve()
    mixed_video = Path(args.mixed_video).resolve()
    russian_only_video = Path(args.russian_only_video).resolve()

    if not source.is_file():
        raise RuntimeError(f"Не найден source video: {source}")
    if not russian.is_file():
        raise RuntimeError(f"Не найден Russian WAV: {russian}")
    if not 0.0 <= args.original_level <= 1.0:
        raise RuntimeError("original-level должен быть в диапазоне 0..1.")

    work_dir.mkdir(parents=True, exist_ok=True)
    mixed_video.parent.mkdir(parents=True, exist_ok=True)
    russian_only_video.parent.mkdir(parents=True, exist_ok=True)

    raw_mix = work_dir / "constant_mix_unmastered.wav"
    mastered_mix = work_dir / "constant_mix_mastered.wav"
    mastered_russian = work_dir / "russian_only_mastered.wav"

    original_level = f"{args.original_level:.6f}"

    # No sidechain, no speech-triggered ducking:
    # the original remains at one constant percentage for the whole Short.
    mix_filter = (
        f"[0:a]volume={original_level}[original];"
        "[1:a]volume=1.0[russian];"
        "[original][russian]"
        "amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "highpass=f=35,"
        "alimiter=limit=0.985[mix]"
    )

    print(
        f"Создаю постоянный микс: оригинал = "
        f"{args.original_level * 100:.1f}%..."
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-i",
            str(russian),
            "-filter_complex",
            mix_filter,
            "-map",
            "[mix]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(raw_mix),
        ]
    )

    print("Двухпроходный loudness-master mixed версии...")
    mixed_master = two_pass_master(
        raw_mix,
        mastered_mix,
        target_i=float(args.target_i),
        target_lra=float(args.target_lra),
        target_tp=float(args.target_tp),
    )

    print("Двухпроходный loudness-master Russian-only версии...")
    russian_master = two_pass_master(
        russian,
        mastered_russian,
        target_i=float(args.target_i),
        target_lra=float(args.target_lra),
        target_tp=float(args.target_tp),
    )

    print("Собираю upload-ready MP4...")
    for audio, output in (
        (mastered_mix, mixed_video),
        (mastered_russian, russian_only_video),
    ):
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "320k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-metadata:s:a:0",
                "language=rus",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output),
            ]
        )

    report = {
        "original_level": float(args.original_level),
        "sidechain": False,
        "target": {
            "integrated_lufs": float(args.target_i),
            "lra": float(args.target_lra),
            "true_peak_db": float(args.target_tp),
        },
        "mixed_master": mixed_master,
        "russian_master": russian_master,
        "mixed_video": str(mixed_video),
        "russian_only_video": str(russian_only_video),
    }
    report_path = mixed_video.with_suffix(".master.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("")
    print("=== MASTER ГОТОВ ===")
    print(f"Mixed: {mixed_video}")
    print(f"Russian-only: {russian_only_video}")
    print(f"Отчёт: {report_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
