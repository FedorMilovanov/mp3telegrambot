#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Segmented, CPU-only VoxCPM2 dubbing.

Loads VoxCPM2 once, synthesizes each source-aligned segment independently,
fits every segment to its target time window with FFmpeg, then builds one
complete timeline WAV. CUDA is hidden before importing torch.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(message, flush=True)


def run_checked(command: list[str]) -> None:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-4000:]
        raise RuntimeError(
            "Command failed:\n" + " ".join(command) + "\n\n" + tail
        )


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe could not read: {path}")
    try:
        duration = float(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Invalid duration: {path}") from exc
    if duration <= 0:
        raise RuntimeError(f"Zero duration: {path}")
    return duration


def atempo_chain(factor: float) -> list[str]:
    """Split an arbitrary tempo factor into FFmpeg's supported 0.5..2.0 steps."""
    if factor <= 0:
        raise ValueError("atempo factor must be positive")

    filters: list[str] = []
    remaining = factor

    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5

    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0

    filters.append(f"atempo={remaining:.8f}")
    return filters


def looks_like_model_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "config.json").exists()
        and (
            (path / "model.safetensors").exists()
            or any(path.glob("*.safetensors"))
            or any(path.glob("*.bin"))
        )
    )


def newest_snapshot(model_cache: Path) -> Path | None:
    snapshots = model_cache / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [p for p in snapshots.iterdir() if looks_like_model_dir(p)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def discover_model(archive_root: Path, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if looks_like_model_dir(path):
            return path
        snapshot = newest_snapshot(path)
        if snapshot:
            return snapshot
        raise RuntimeError(f"ModelPath is not a VoxCPM2 directory: {path}")

    candidates = [
        archive_root
        / "models"
        / "voxcpm2-model-cache"
        / "models--openbmb--VoxCPM2",
        archive_root
        / "models"
        / "voxcpm2-model-cache"
        / "models--OpenBMB--VoxCPM2",
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--openbmb--VoxCPM2",
    ]

    for candidate in candidates:
        if looks_like_model_dir(candidate):
            return candidate
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot

    for path in archive_root.rglob("models--openbmb--VoxCPM2"):
        snapshot = newest_snapshot(path)
        if snapshot:
            return snapshot

    raise RuntimeError("Saved VoxCPM2 snapshot was not found")


def read_segments(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments JSON must contain a non-empty list")

    result: list[dict[str, Any]] = []
    previous_end = 0.0

    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Segment #{index} must be an object")

        start = float(item["start"])
        end = float(item["end"])
        text = str(item["text"]).strip()
        segment_id = int(item.get("id", index))

        if start < 0 or end <= start:
            raise RuntimeError(f"Invalid timings for segment #{segment_id}")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Segments overlap at #{segment_id}")
        if not text:
            raise RuntimeError(f"Empty text for segment #{segment_id}")

        result.append(
            {"id": segment_id, "start": start, "end": end, "text": text}
        )
        previous_end = end

    return result


def fit_segment(
    raw_path: Path,
    fitted_path: Path,
    target_duration: float,
) -> dict[str, float]:
    raw_duration = probe_duration(raw_path)
    tempo = raw_duration / target_duration

    filters = atempo_chain(tempo)
    filters.extend(
        [
            "highpass=f=70",
            "lowpass=f=15500",
            "equalizer=f=260:t=q:w=1.2:g=-1.8",
            "equalizer=f=520:t=q:w=1.0:g=-0.8",
            "acompressor=threshold=0.09:ratio=2.0:attack=15:release=180:makeup=1.15",
            "alimiter=limit=0.95",
            "afade=t=in:st=0:d=0.018",
            f"afade=t=out:st={max(0.0, target_duration - 0.025):.6f}:d=0.025",
            f"apad=pad_dur={target_duration:.6f}",
            f"atrim=duration={target_duration:.6f}",
            "asetpts=N/SR/TB",
        ]
    )

    run_checked(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw_path),
            "-af",
            ",".join(filters),
            "-ar",
            "48000",
            "-ac",
            "1",
            str(fitted_path),
        ]
    )

    fitted_duration = probe_duration(fitted_path)
    return {
        "raw_duration": raw_duration,
        "target_duration": target_duration,
        "tempo": tempo,
        "fitted_duration": fitted_duration,
    }


def build_timeline(
    fitted_segments: list[tuple[dict[str, Any], Path]],
    output: Path,
    total_duration: float,
) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

    for _, segment_path in fitted_segments:
        command.extend(["-i", str(segment_path)])

    filters: list[str] = []
    mix_inputs: list[str] = []

    for input_index, (segment, _) in enumerate(fitted_segments):
        delay_ms = int(round(float(segment["start"]) * 1000.0))
        label = f"s{input_index}"
        filters.append(f"[{input_index}:a]adelay={delay_ms}:all=1[{label}]")
        mix_inputs.append(f"[{label}]")

    filters.append(
        "".join(mix_inputs)
        + f"amix=inputs={len(mix_inputs)}:"
        + "duration=longest:dropout_transition=0:normalize=0,"
        + f"apad=pad_dur={total_duration:.6f},"
        + f"atrim=duration={total_duration:.6f},"
        + "loudnorm=I=-16:LRA=7:TP=-1.5,"
        + "alimiter=limit=0.97[out]"
    )

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    run_checked(command)


def main() -> None:
    configure_utf8()

    parser = argparse.ArgumentParser(
        description="Segmented CPU-only VoxCPM2 dubbing"
    )
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--model-path")
    parser.add_argument("--reference-wav", required=True)
    parser.add_argument("--prompt-text-file")
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--cfg", type=float, default=2.0)
    parser.add_argument(
        "--clone-mode",
        choices=("reference", "ultimate", "continuation"),
        default="reference",
    )
    parser.add_argument("--cache-length", type=int, default=2048)
    parser.add_argument("--video-duration", type=float, required=True)
    args = parser.parse_args()

    # Must happen before importing torch or voxcpm.
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["OMP_NUM_THREADS"] = str(max(1, args.threads))
    os.environ["MKL_NUM_THREADS"] = str(max(1, args.threads))
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe not found in PATH")

    import numpy as np
    import soundfile as sf
    import torch
    from voxcpm import VoxCPM

    threads = max(1, int(args.threads))
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(2)
    except RuntimeError:
        pass

    archive_root = Path(args.archive_root).expanduser().resolve()
    reference = Path(args.reference_wav).expanduser().resolve()
    segments_path = Path(args.segments_json).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    if not reference.exists():
        raise RuntimeError(f"Reference not found: {reference}")

    segments = read_segments(segments_path)
    prompt_text = ""

    if args.clone_mode in {"ultimate", "continuation"}:
        if not args.prompt_text_file:
            raise RuntimeError(
                "ultimate/continuation requires --prompt-text-file"
            )
        prompt_text = (
            Path(args.prompt_text_file)
            .expanduser()
            .resolve()
            .read_text(encoding="utf-8-sig")
            .strip()
        )
        if not prompt_text:
            raise RuntimeError("Reference transcript is empty")

    model_path = discover_model(archive_root, args.model_path)

    log("=== VOXCPM2 SEGMENTED CPU DUB ===")
    log(f"PyTorch: {torch.__version__}")
    log(f"CUDA available: {torch.cuda.is_available()} (must be False)")
    log(f"Clone mode: {args.clone_mode}")
    log(f"Segments: {len(segments)}")
    log(f"CPU threads: {threads}")
    log(f"LocDiT steps: {args.steps}")
    log(f"Model: {model_path}")
    log(f"Reference: {reference}")

    load_started = time.perf_counter()
    model = VoxCPM.from_pretrained(
        str(model_path),
        device="cpu",
        optimize=False,
        load_denoiser=False,
    )

    cache_length = max(1024, int(args.cache_length))
    cache_dtype = next(model.tts_model.parameters()).dtype
    cache_device = model.tts_model.device

    model.tts_model.base_lm.setup_cache(
        1, cache_length, cache_device, cache_dtype
    )
    model.tts_model.residual_lm.setup_cache(
        1, cache_length, cache_device, cache_dtype
    )

    load_seconds = time.perf_counter() - load_started
    log(f"KV cache: {cache_length}")
    log(f"Model loaded in {load_seconds:.1f} sec")

    work_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = work_dir / "segments_raw"
    fitted_dir = work_dir / "segments_fitted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fitted_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    encode_sr = int(model.tts_model._encode_sample_rate)
    patch_size = int(model.tts_model.patch_size)
    chunk_size = int(model.tts_model.chunk_size)
    seconds_per_step = patch_size * chunk_size / encode_sr

    log(f"One autoregressive step ~= {seconds_per_step:.3f} sec")

    report_segments: list[dict[str, Any]] = []
    fitted_segments: list[tuple[dict[str, Any], Path]] = []
    total_synth_seconds = 0.0

    for position, segment in enumerate(segments, start=1):
        segment_id = int(segment["id"])
        target_duration = float(segment["end"]) - float(segment["start"])
        text = str(segment["text"])

        desired_steps = target_duration / seconds_per_step
        min_len = max(2, int(math.floor(desired_steps * 0.88)))
        max_len = max(min_len + 6, int(math.ceil(desired_steps * 1.35)))

        raw_path = raw_dir / f"{segment_id:02d}_raw.wav"
        fitted_path = fitted_dir / f"{segment_id:02d}_fitted.wav"

        log("")
        log(
            f"[{position}/{len(segments)}] Segment {segment_id}: "
            f"{target_duration:.2f} sec"
        )
        log(f"Text: {text}")
        log(f"min_len={min_len}; max_len={max_len}")

        generate_kwargs: dict[str, Any] = {
            "text": text,
            "cfg_value": float(args.cfg),
            "inference_timesteps": max(1, int(args.steps)),
            "min_len": min_len,
            "max_len": max_len,
            "normalize": True,
            "denoise": False,
            "retry_badcase": False,
        }

        if args.clone_mode == "reference":
            generate_kwargs["reference_wav_path"] = str(reference)
        elif args.clone_mode == "ultimate":
            generate_kwargs["reference_wav_path"] = str(reference)
            generate_kwargs["prompt_wav_path"] = str(reference)
            generate_kwargs["prompt_text"] = prompt_text
        else:
            generate_kwargs["prompt_wav_path"] = str(reference)
            generate_kwargs["prompt_text"] = prompt_text

        started = time.perf_counter()
        with torch.inference_mode():
            wav = model.generate(**generate_kwargs)
        synth_seconds = time.perf_counter() - started
        total_synth_seconds += synth_seconds

        wav_np = np.asarray(wav, dtype=np.float32)
        sample_rate = int(model.tts_model.sample_rate)
        sf.write(str(raw_path), wav_np, sample_rate, subtype="PCM_16")

        fit_info = fit_segment(raw_path, fitted_path, target_duration)
        raw_ratio = fit_info["raw_duration"] / target_duration
        if raw_ratio < 0.65 or raw_ratio > 1.65:
            raise RuntimeError(
                f"Segment {segment_id} is too far from its target window: "
                f"{fit_info['raw_duration']:.2f} vs {target_duration:.2f} sec"
            )

        fitted_segments.append((segment, fitted_path))
        report_segments.append(
            {
                **segment,
                "target_duration": round(target_duration, 3),
                "min_len": min_len,
                "max_len": max_len,
                "synthesis_seconds": round(synth_seconds, 3),
                **{key: round(value, 6) for key, value in fit_info.items()},
                "raw_path": str(raw_path),
                "fitted_path": str(fitted_path),
            }
        )

        log(
            f"Done: raw={fit_info['raw_duration']:.2f} sec; "
            f"atempo={fit_info['tempo']:.3f}; CPU={synth_seconds:.1f} sec"
        )

        del wav, wav_np
        gc.collect()

    log("")
    log("Building full timeline...")
    build_timeline(fitted_segments, output, float(args.video_duration))

    final_duration = probe_duration(output)
    report = {
        "model_path": str(model_path),
        "reference_wav": str(reference),
        "clone_mode": args.clone_mode,
        "output": str(output),
        "video_duration": float(args.video_duration),
        "final_audio_duration": round(final_duration, 3),
        "load_seconds": round(load_seconds, 3),
        "total_synthesis_seconds": round(total_synth_seconds, 3),
        "threads": threads,
        "steps": int(args.steps),
        "cfg": float(args.cfg),
        "cache_length": cache_length,
        "seconds_per_step": round(seconds_per_step, 6),
        "cuda_available": bool(torch.cuda.is_available()),
        "segments": report_segments,
    }

    report_path = output.with_suffix(".json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("")
    log("=== SEGMENTED SYNTHESIS COMPLETE ===")
    log(f"WAV: {output}")
    log(f"Report: {report_path}")
    log(f"Duration: {final_duration:.2f} sec")
    log(f"Total CPU synthesis: {total_synth_seconds:.1f} sec")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        import traceback

        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
