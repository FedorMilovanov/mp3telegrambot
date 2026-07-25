#!/usr/bin/env python3
"""Run a compact CPU-only VoxCPM2 quality sweep.

This tool is deliberately separate from full video rendering. It generates the
same ending-sensitive phrase across a small CFG/LocDiT grid, records objective
endpoint metrics, and leaves speaker similarity/prosody selection to listening.

It adapts to the installed ``voxcpm`` API by inspecting ``model.generate`` and
only passing optional arguments such as ``seed`` when they are supported.
"""
from __future__ import annotations

import argparse
import gc
import inspect
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_csv_floats(value: str) -> list[float]:
    result = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not result:
        raise argparse.ArgumentTypeError("expected at least one numeric value")
    return result


def parse_csv_ints(value: str) -> list[int]:
    result = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not result or any(item < 1 for item in result):
        raise argparse.ArgumentTypeError("expected positive integer values")
    return result


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


def newest_snapshot(path: Path) -> Path | None:
    snapshots = path / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [item for item in snapshots.iterdir() if looks_like_model_dir(item)]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def discover_model(archive_root: Path, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if looks_like_model_dir(path):
            return path
        snapshot = newest_snapshot(path)
        if snapshot:
            return snapshot
        raise RuntimeError(f"model path is not a VoxCPM2 snapshot: {path}")

    candidates = [
        archive_root
        / "models"
        / "voxcpm2-model-cache"
        / "models--openbmb--VoxCPM2",
        archive_root
        / "models"
        / "voxcpm2-model-cache"
        / "models--OpenBMB--VoxCPM2",
    ]
    for candidate in candidates:
        if looks_like_model_dir(candidate):
            return candidate
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot

    for candidate in archive_root.rglob("models--openbmb--VoxCPM2"):
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot
    raise RuntimeError("local VoxCPM2 snapshot was not found")


def frame_db(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: float = 20.0,
    hop_ms: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    mono = np.asarray(samples, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    hop = max(1, int(sample_rate * hop_ms / 1000.0))
    levels: list[float] = []
    centers: list[float] = []
    for start in range(0, max(1, len(mono) - frame + 1), hop):
        chunk = mono[start : start + frame]
        if len(chunk) < frame:
            break
        rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float64))) + 1e-12))
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
        centers.append((start + frame / 2) / sample_rate)
    return np.asarray(levels), np.asarray(centers)


def edge_silence(
    levels: np.ndarray,
    *,
    threshold_db: float = -55.0,
    hop_seconds: float = 0.01,
) -> tuple[float, float]:
    leading = 0
    for value in levels:
        if value < threshold_db:
            leading += 1
        else:
            break
    trailing = 0
    for value in levels[::-1]:
        if value < threshold_db:
            trailing += 1
        else:
            break
    return leading * hop_seconds, trailing * hop_seconds


def detect_pause_restart(
    levels: np.ndarray,
    centers: np.ndarray,
    duration: float,
) -> dict[str, Any]:
    if len(levels) < 20:
        return {"suspicious": False}

    peak = float(np.percentile(levels, 95))
    active_threshold = max(-48.0, peak - 28.0)
    silence_threshold = min(-46.0, peak - 36.0)
    active = levels > active_threshold
    silent = levels < silence_threshold

    minimum_silence_frames = 24
    search_start = int(len(levels) * 0.55)
    run_start: int | None = None

    for index in range(search_start, len(levels)):
        if silent[index] and run_start is None:
            run_start = index
            continue
        if silent[index] or run_start is None:
            continue

        if index - run_start >= minimum_silence_frames:
            later = np.where(active[index:])[0]
            if len(later):
                resume_start_index = index + int(later[0])
                active_after = np.where(active[resume_start_index:])[0]
                resume_end_index = resume_start_index + int(active_after[-1])
                resume_start = float(centers[resume_start_index] - 0.01)
                resume_end = float(centers[resume_end_index] + 0.01)
                resumed_duration = max(0.0, resume_end - resume_start)
                if resume_start > duration * 0.62 and resumed_duration <= 1.55:
                    return {
                        "suspicious": True,
                        "silence_start": round(float(centers[run_start] - 0.01), 4),
                        "resume_start": round(resume_start, 4),
                        "resume_end": round(min(duration, resume_end), 4),
                        "resumed_duration": round(resumed_duration, 4),
                    }
        run_start = None

    return {"suspicious": False}


def analyze_wave(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    mono = np.asarray(samples, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    duration = len(mono) / sample_rate
    levels, centers = frame_db(mono, sample_rate)
    leading, trailing = edge_silence(levels)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64))))) if len(mono) else 0.0
    clipping_ratio = float(np.mean(np.abs(mono) >= 0.999)) if len(mono) else 0.0
    restart = detect_pause_restart(levels, centers, duration)
    artifact_score = (
        (20.0 if restart.get("suspicious") else 0.0)
        + max(0.0, leading - 0.35) * 3.0
        + max(0.0, trailing - 0.60) * 2.0
        + clipping_ratio * 1000.0
    )
    return {
        "duration": round(duration, 4),
        "leading_silence": round(leading, 4),
        "trailing_silence": round(trailing, 4),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "clipping_ratio": round(clipping_ratio, 8),
        "pause_restart": restart,
        "artifact_score": round(artifact_score, 4),
    }


def main() -> int:
    configure_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--model-path")
    parser.add_argument("--reference-wav", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--text",
        default=(
            "Это движение ничего к нему не добавляет. "
            "Оно лишь умаляет истину и вносит путаницу."
        ),
    )
    parser.add_argument("--cfg-values", type=parse_csv_floats, default=[1.55, 1.75, 1.95])
    parser.add_argument("--steps-values", type=parse_csv_ints, default=[10])
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--retry-badcase", action="store_true")
    args = parser.parse_args()

    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["OMP_NUM_THREADS"] = str(max(1, args.threads))
    os.environ["MKL_NUM_THREADS"] = str(max(1, args.threads))
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    import soundfile as sf
    import torch
    from voxcpm import VoxCPM

    torch.set_num_threads(max(1, args.threads))
    try:
        torch.set_num_interop_threads(2)
    except RuntimeError:
        pass

    reference = Path(args.reference_wav).expanduser().resolve()
    if not reference.is_file():
        raise RuntimeError(f"reference WAV does not exist: {reference}")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = discover_model(Path(args.archive_root).resolve(), args.model_path)

    print(f"model: {model_path}", flush=True)
    print(f"reference: {reference}", flush=True)
    print(f"CUDA available: {torch.cuda.is_available()} (must be False)", flush=True)

    loaded_at = time.perf_counter()
    model = VoxCPM.from_pretrained(
        str(model_path),
        device="cpu",
        optimize=False,
        load_denoiser=False,
    )
    load_seconds = time.perf_counter() - loaded_at
    signature = inspect.signature(model.generate)
    supported = set(signature.parameters)
    print(f"generate args: {sorted(supported)}", flush=True)

    report: dict[str, Any] = {
        "schema_version": 1,
        "model_path": str(model_path),
        "reference_wav": str(reference),
        "text": args.text,
        "torch": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "threads": int(args.threads),
        "model_load_seconds": round(load_seconds, 3),
        "supported_generate_arguments": sorted(supported),
        "variants": [],
    }

    for cfg in args.cfg_values:
        for steps in args.steps_values:
            name = f"cfg_{cfg:.2f}_steps_{steps}"
            output_path = output_dir / f"{name}.wav"
            kwargs: dict[str, Any] = {
                "text": args.text,
                "reference_wav_path": str(reference),
                "cfg_value": float(cfg),
                "inference_timesteps": int(steps),
                "normalize": True,
                "denoise": False,
            }
            optional = {
                "min_len": 2,
                "retry_badcase": bool(args.retry_badcase),
                "retry_badcase_max_times": 2,
                "retry_badcase_ratio_threshold": 3.0,
                "seed": int(args.seed),
            }
            for key, value in optional.items():
                if key in supported:
                    kwargs[key] = value

            print(f"generating {name}: {kwargs}", flush=True)
            started = time.perf_counter()
            with torch.inference_mode():
                wav = model.generate(**kwargs)
            synthesis_seconds = time.perf_counter() - started
            samples = np.asarray(wav, dtype=np.float32)
            sample_rate = int(model.tts_model.sample_rate)
            sf.write(str(output_path), samples, sample_rate, subtype="PCM_16")
            metrics = analyze_wave(samples, sample_rate)
            report["variants"].append(
                {
                    "name": name,
                    "cfg": float(cfg),
                    "steps": int(steps),
                    "output": str(output_path),
                    "synthesis_seconds": round(synthesis_seconds, 3),
                    "parameters": kwargs,
                    "metrics": metrics,
                }
            )
            print(
                f"{name}: duration={metrics['duration']:.2f}, "
                f"restart={metrics['pause_restart'].get('suspicious')}, "
                f"score={metrics['artifact_score']:.2f}",
                flush=True,
            )
            del wav, samples
            gc.collect()

    report["variants"].sort(
        key=lambda item: (
            item["metrics"]["artifact_score"],
            item["synthesis_seconds"],
        )
    )
    report_path = output_dir / "quality_sweep.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {report_path}", flush=True)
    print("Objective ranking does not replace listening for timbre and cadence.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
