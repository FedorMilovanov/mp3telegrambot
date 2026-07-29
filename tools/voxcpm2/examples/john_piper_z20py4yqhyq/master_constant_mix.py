#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct constant-level Dub master with post-AAC delivery verification."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.voxcpm2.final_media_qa import verify_final_outputs


def run(
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        tail = (process.stderr or process.stdout or "")[-6000:] if capture else ""
        raise RuntimeError(
            "Команда завершилась с ошибкой:\n"
            + " ".join(command)
            + ("\n\n" + tail if tail else "")
        )
    return process


def probe_duration(path: Path) -> float:
    process = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    duration = float((process.stdout or "").strip())
    if duration <= 0:
        raise RuntimeError(f"Нулевая длительность: {path}")
    return duration


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
        "aresample=48000,"
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


def encode_upload_mp4(
    *,
    source: Path,
    audio: Path,
    output: Path,
    source_duration: float,
) -> None:
    # Explicit duration avoids -shortest trimming the last video frames because
    # AAC packet duration/priming differs slightly from the PCM master.
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
            "-af",
            "apad=pad_dur=2",
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
            "-t",
            f"{source_duration:.6f}",
            str(output),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Constant-level English/Russian mix, two-pass master and final AAC QA."
        )
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

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe не найдены в PATH.")

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
    source_duration = probe_duration(source)

    raw_mix = work_dir / "constant_mix_unmastered.wav"
    mastered_mix = work_dir / "constant_mix_mastered.wav"
    mastered_russian = work_dir / "russian_only_mastered.wav"
    original_level = f"{args.original_level:.6f}"

    # The original remains at one constant percentage; no pumping or sidechain.
    mix_filter = (
        f"[0:a]volume={original_level}[original];"
        "[1:a]volume=1.0[russian];"
        "[original][russian]"
        "amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "highpass=f=35,"
        "alimiter=limit=0.985[mix]"
    )
    print(f"Создаю постоянный микс: оригинал = {args.original_level * 100:.1f}%...")
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
    encode_upload_mp4(
        source=source,
        audio=mastered_mix,
        output=mixed_video,
        source_duration=source_duration,
    )
    encode_upload_mp4(
        source=source,
        audio=mastered_russian,
        output=russian_only_video,
        source_duration=source_duration,
    )

    print("Проверяю уже закодированные AAC-дорожки...")
    verification_path = work_dir / "final_media_verification.json"
    final_verification = verify_final_outputs(
        source_duration=source_duration,
        mixed_video=mixed_video,
        russian_only_video=russian_only_video,
        target_i=float(args.target_i),
        target_lra=float(args.target_lra),
        target_tp=float(args.target_tp),
        report_path=verification_path,
    )

    report = {
        "schema_version": "direct-master-with-final-aac-qa-v2",
        "original_level": float(args.original_level),
        "sidechain": False,
        "source_duration": source_duration,
        "target": {
            "integrated_lufs": float(args.target_i),
            "lra": float(args.target_lra),
            "true_peak_db": float(args.target_tp),
        },
        "mixed_master": mixed_master,
        "russian_master": russian_master,
        "mixed_video": str(mixed_video),
        "russian_only_video": str(russian_only_video),
        "final_media_verification": final_verification,
        "final_media_verification_path": str(verification_path),
    }
    report_path = mixed_video.with_suffix(".master.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("")
    print("=== MASTER И FINAL AAC-QA ГОТОВЫ ===")
    print(f"Mixed: {mixed_video}")
    print(f"Russian-only: {russian_only_video}")
    print(f"Отчёт: {report_path}")
    print(f"AAC-QA: {verification_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
