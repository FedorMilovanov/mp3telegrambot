#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constant-original Dub master with post-AAC delivery verification.

The English branch is always mixed at the requested linear coefficient. It is
never raised by a loudness normalizer after the mix. Russian speech is mastered
first and its gain is then calibrated around the fixed English branch so the
final mix still meets the delivery loudness and true-peak contract.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# This file is an executable entrypoint as well as an importable module. When
# Python executes a file by absolute path, sys.path[0] is the file's directory,
# not the repository root. Establish the package root explicitly before any
# project import so the exact bot command works from every cwd and virtualenv.
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.voxcpm2.final_media_qa import (
    LOUDNESS_TOLERANCE_LU,
    TARGET_I_RANGE,
    TARGET_LRA_RANGE,
    TARGET_TP_RANGE,
    verify_final_outputs,
)


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


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"Нефинитное значение {field}: {value!r}")
    return result


def _bounded(value: Any, *, field: str, limits: tuple[float, float]) -> float:
    result = _finite(value, field=field)
    if not limits[0] <= result <= limits[1]:
        raise RuntimeError(f"{field}={result} вне диапазона {limits[0]}..{limits[1]}")
    return result


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
    duration = _finite((process.stdout or "").strip(), field=f"duration:{path}")
    if duration <= 0:
        raise RuntimeError(f"Некорректная длительность: {path}: {duration!r}")
    return duration


def _last_loudnorm_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    source = str(text or "")
    for index, char in enumerate(source):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(source[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "input_i" in payload:
            candidates.append(payload)
    if not candidates:
        raise RuntimeError("FFmpeg не вернул JSON loudnorm.")
    return candidates[-1]


def parse_loudnorm(stderr: str) -> dict[str, str]:
    payload = _last_loudnorm_json(stderr)
    required = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    missing = required.difference(payload)
    if missing:
        raise RuntimeError("Неполный loudnorm JSON: " + ", ".join(sorted(missing)))
    result: dict[str, str] = {}
    for key in required:
        result[key] = str(_finite(payload.get(key), field=f"loudnorm.{key}"))
    return result


def measure_loudness(
    path: Path,
    *,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, float]:
    process = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture=True,
    )
    payload = _last_loudnorm_json(process.stderr or process.stdout or "")
    return {
        "integrated_lufs": _finite(payload.get("input_i"), field="input_i"),
        "true_peak_dbtp": _finite(payload.get("input_tp"), field="input_tp"),
        "lra_lu": _finite(payload.get("input_lra"), field="input_lra"),
        "threshold_lufs": _finite(payload.get("input_thresh"), field="input_thresh"),
    }


def two_pass_master(
    input_wav: Path,
    output_wav: Path,
    *,
    target_i: float,
    target_lra: float,
    target_tp: float,
) -> dict[str, Any]:
    target_i = _bounded(target_i, field="target_i", limits=TARGET_I_RANGE)
    target_lra = _bounded(target_lra, field="target_lra", limits=TARGET_LRA_RANGE)
    target_tp = _bounded(target_tp, field="target_tp", limits=TARGET_TP_RANGE)
    first_filter = f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:print_format=json"
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
    limiter_linear = 10.0 ** (float(target_tp) / 20.0)
    second_filter = (
        f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:"
        f"measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}:"
        f"measured_TP={measured['input_tp']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary,"
        "aresample=48000,"
        f"alimiter=limit={limiter_linear:.8f}:level=false:latency=true"
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
        "limiter_linear": limiter_linear,
        "limiter_auto_level": False,
        "limiter_latency_compensated": True,
        "first_pass": measured,
        "filter": second_filter,
    }


def build_constant_mix(
    *,
    source: Path,
    mastered_russian: Path,
    output: Path,
    source_duration: float,
    original_level: float,
    russian_gain: float,
) -> str:
    """Build a mix with no gain-changing filter after the branches are summed."""
    original_level = _finite(original_level, field="original_level")
    russian_gain = _finite(russian_gain, field="russian_gain")
    if not 0.0 <= original_level <= 1.0:
        raise RuntimeError("original_level должен быть в диапазоне 0..1")
    if not 0.0 < russian_gain <= 2.0:
        raise RuntimeError("russian_gain должен быть в диапазоне 0..2")
    source_duration = _finite(source_duration, field="source_duration")
    mix_filter = (
        f"[0:a]asetpts=PTS-STARTPTS,volume={original_level:.9f}[original];"
        f"[1:a]asetpts=PTS-STARTPTS,highpass=f=35,volume={russian_gain:.9f}[russian];"
        "[original][russian]amix=inputs=2:duration=longest:"
        "dropout_transition=0:normalize=0,"
        f"apad=pad_dur={source_duration:.6f},"
        f"atrim=duration={source_duration:.6f},"
        "asetpts=N/SR/TB[mix]"
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


def calibrate_russian_gain(
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
    """Tune only the Russian branch while the English branch stays exactly fixed."""
    safe_peak = min(float(target_tp) - 0.20, -1.20)
    low = 0.05
    high = 1.35
    best: tuple[float, dict[str, float], str] | None = None
    attempts: list[dict[str, Any]] = []
    candidate = work_dir / "constant_mix_candidate.wav"

    for _index in range(11):
        gain = (low + high) / 2.0
        graph = build_constant_mix(
            source=source,
            mastered_russian=mastered_russian,
            output=candidate,
            source_duration=source_duration,
            original_level=original_level,
            russian_gain=gain,
        )
        measured = measure_loudness(
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
        gain = 0.05
        graph = build_constant_mix(
            source=source,
            mastered_russian=mastered_russian,
            output=candidate,
            source_duration=source_duration,
            original_level=original_level,
            russian_gain=gain,
        )
        measured = measure_loudness(
            candidate,
            target_i=target_i,
            target_lra=target_lra,
            target_tp=target_tp,
        )
        if (
            measured["integrated_lufs"] > target_i + LOUDNESS_TOLERANCE_LU
            or measured["true_peak_dbtp"] > -1.0
        ):
            raise RuntimeError(
                "Даже минимальная русская громкость не позволяет сохранить постоянный "
                f"оригинал {original_level * 100:.1f}% и пройти master-QA: {measured}"
            )
        best = (gain, measured, graph)

    gain, _measured, graph = best
    final_graph = build_constant_mix(
        source=source,
        mastered_russian=mastered_russian,
        output=output,
        source_duration=source_duration,
        original_level=original_level,
        russian_gain=gain,
    )
    final_measured = measure_loudness(
        output,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    candidate.unlink(missing_ok=True)
    loudness_error = abs(final_measured["integrated_lufs"] - target_i)
    if loudness_error > LOUDNESS_TOLERANCE_LU:
        raise RuntimeError(
            "Постоянный микс не попал в допуск loudness без изменения английских 18%: "
            f"{final_measured['integrated_lufs']:.2f} LUFS при цели {target_i:.2f} "
            f"(отклонение {loudness_error:.2f} LU, допуск ±{LOUDNESS_TOLERANCE_LU:.2f})."
        )
    if final_measured["true_peak_dbtp"] > -1.0:
        raise RuntimeError(
            "Постоянный микс превышает безопасный true peak без прозрачного запаса: "
            f"{final_measured['true_peak_dbtp']:.2f} dBTP."
        )
    return {
        "policy": "fixed-original-post-russian-master-v1",
        "original_level": original_level,
        "original_level_percent": original_level * 100.0,
        "original_gain_changes_after_sum": False,
        "post_mix_loudnorm": False,
        "post_mix_limiter": False,
        "russian_gain": gain,
        "safe_peak_target_dbtp": safe_peak,
        "loudness_tolerance_lu": LOUDNESS_TOLERANCE_LU,
        "measurement": final_measured,
        "attempts": attempts,
        "filter": final_graph or graph,
    }


def encode_upload_mp4(
    *,
    source: Path,
    audio: Path,
    output: Path,
    source_duration: float,
) -> None:
    source_duration = _finite(source_duration, field="source_duration")
    if source_duration <= 0:
        raise RuntimeError("source_duration должен быть > 0")
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
        description="Fixed-English-level Russian Dub master with final AAC QA."
    )
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

    original_level = _finite(args.original_level, field="original_level")
    if not 0.0 <= original_level <= 1.0:
        raise RuntimeError("original-level должен быть в диапазоне 0..1.")
    target_i = _bounded(args.target_i, field="target_i", limits=TARGET_I_RANGE)
    target_lra = _bounded(args.target_lra, field="target_lra", limits=TARGET_LRA_RANGE)
    target_tp = _bounded(args.target_tp, field="target_tp", limits=TARGET_TP_RANGE)

    work_dir.mkdir(parents=True, exist_ok=True)
    mixed_video.parent.mkdir(parents=True, exist_ok=True)
    russian_only_video.parent.mkdir(parents=True, exist_ok=True)
    source_duration = probe_duration(source)
    mastered_mix = work_dir / "constant_mix_mastered.wav"
    mastered_russian = work_dir / "russian_only_mastered.wav"

    print("Сначала мастерю только русскую дорожку...")
    russian_master = two_pass_master(
        russian,
        mastered_russian,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
    )
    print(
        f"Добавляю оригинал строго на {original_level * 100:.1f}% после русского мастера; "
        "общего loudnorm после суммы нет..."
    )
    mixed_master = calibrate_russian_gain(
        source=source,
        mastered_russian=mastered_russian,
        output=mastered_mix,
        work_dir=work_dir,
        source_duration=source_duration,
        original_level=original_level,
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
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
        target_i=target_i,
        target_lra=target_lra,
        target_tp=target_tp,
        report_path=verification_path,
    )
    report = {
        "schema_version": "fixed-original-master-with-final-aac-qa-v4",
        "original_level": original_level,
        "sidechain": False,
        "source_duration": source_duration,
        "target": {
            "integrated_lufs": target_i,
            "lra": target_lra,
            "true_peak_db": target_tp,
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
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    print("")
    print("=== FIXED-ORIGINAL MASTER И FINAL AAC-QA ГОТОВЫ ===")
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
