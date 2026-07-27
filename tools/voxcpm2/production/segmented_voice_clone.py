#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal checkpointed VoxCPM2 CPU segment renderer.

The approved Russian text and segment plan are immutable inputs. The renderer:
- hides CUDA before importing torch;
- generates one candidate by default;
- retries only when automatic audio QA flags the candidate or the segment
  explicitly requests more candidates;
- never slows down a short successful candidate;
- lets start_delay_ms consume the segment slot instead of extending beyond it;
- validates checkpoint signatures and fitted-audio hashes before reuse.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np


ENGINE_SCHEMA_VERSION = "5.0-universal"
CHECKPOINT_SCHEMA_VERSION = 2
MAX_CANDIDATES = 3
DEFAULT_MAX_SAFE_TEMPO = 1.12


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
        tail = (proc.stderr or proc.stdout or "")[-6000:]
        raise RuntimeError(
            "Команда завершилась с ошибкой:\n"
            + " ".join(command)
            + "\n\n"
            + tail
        )


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        raise RuntimeError(f"ffprobe не смог прочитать: {path}")
    try:
        value = float(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe вернул неверную длительность: {path}") from exc
    if value <= 0:
        raise RuntimeError(f"Нулевая длительность: {path}")
    return value


def atempo_chain(factor: float) -> list[str]:
    if factor <= 0:
        raise ValueError("atempo factor должен быть > 0")
    result: list[str] = []
    remaining = factor
    while remaining < 0.5:
        result.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        result.append("atempo=2.0")
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-8:
        result.append(f"atempo={remaining:.8f}")
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


def discover_model(archive_root: Path) -> Path:
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

    raise RuntimeError("Локальный snapshot VoxCPM2 не найден.")


def model_identity(model_path: Path) -> dict[str, Any]:
    weight_files = sorted(
        [
            *model_path.glob("*.safetensors"),
            *model_path.glob("*.bin"),
        ],
        key=lambda path: path.name.lower(),
    )
    return {
        "path": str(model_path),
        "config_sha256": sha256_file(model_path / "config.json"),
        "weights": [
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in weight_files
        ],
    }


def read_segments(
    path: Path,
    *,
    default_max_safe_tempo: float,
) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments JSON должен содержать непустой список.")

    result: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    previous_end = 0.0

    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Сегмент #{index} должен быть объектом.")
        segment = dict(item)
        segment_id = int(segment.get("id", index))
        start = float(segment["start"])
        end = float(segment["end"])
        text = str(segment.get("text") or "").strip()
        delay_ms = int(segment.get("start_delay_ms", 0))
        tail_guard = float(segment.get("tail_guard", 0.32))
        profile = str(segment.get("reference_profile", "extended")).strip()
        minimum_candidates = int(segment.get("minimum_candidates", 1))
        max_safe_tempo = float(
            segment.get("max_safe_tempo", default_max_safe_tempo)
        )

        if segment_id <= 0 or segment_id in seen_ids:
            raise RuntimeError(f"ID сегмента должен быть уникальным и положительным: {segment_id}.")
        if start < 0 or end <= start:
            raise RuntimeError(f"Некорректное окно сегмента #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Пересечение у сегмента #{segment_id}.")
        if not text:
            raise RuntimeError(f"Пустой текст сегмента #{segment_id}.")
        if profile not in {"extended", "composite"}:
            raise RuntimeError(
                f"Неизвестный reference_profile у #{segment_id}: {profile}."
            )
        if delay_ms < 0:
            raise RuntimeError(f"start_delay_ms не может быть отрицательным у #{segment_id}.")
        window_duration = end - start
        placement_duration = window_duration - delay_ms / 1000.0
        if placement_duration <= 0.20:
            raise RuntimeError(
                f"Задержка съедает всё окно сегмента #{segment_id}: "
                f"{delay_ms} ms / {window_duration:.3f} sec."
            )
        if tail_guard < 0 or tail_guard >= placement_duration:
            raise RuntimeError(f"Некорректный tail_guard у #{segment_id}.")
        if minimum_candidates < 1 or minimum_candidates > MAX_CANDIDATES:
            raise RuntimeError(
                f"minimum_candidates у #{segment_id} должен быть 1..{MAX_CANDIDATES}."
            )
        if max_safe_tempo < 1.0 or max_safe_tempo > 1.50:
            raise RuntimeError(f"max_safe_tempo у #{segment_id} вне диапазона 1.0..1.50.")

        segment.update(
            {
                "id": segment_id,
                "start": start,
                "end": end,
                "text": text,
                "tail_guard": tail_guard,
                "start_delay_ms": delay_ms,
                "reference_profile": profile,
                "minimum_candidates": minimum_candidates,
                "max_safe_tempo": max_safe_tempo,
                "window_duration": window_duration,
                "placement_duration": placement_duration,
            }
        )
        result.append(segment)
        seen_ids.add(segment_id)
        previous_end = end

    return result


def frame_levels(
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
        rms = float(
            np.sqrt(np.mean(np.square(chunk.astype(np.float64))) + 1e-12)
        )
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
        centers.append((start + frame / 2) / sample_rate)

    return np.asarray(levels), np.asarray(centers)


def edge_silence(
    samples: np.ndarray,
    sample_rate: int,
    *,
    threshold_db: float = -52.0,
) -> tuple[float, float]:
    levels, _ = frame_levels(samples, sample_rate)
    if not len(levels):
        return 0.0, 0.0

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

    return leading * 0.01, trailing * 0.01


def detect_tail_restart(
    samples: np.ndarray,
    sample_rate: int,
) -> dict[str, Any]:
    levels, centers = frame_levels(samples, sample_rate)
    duration = len(samples) / sample_rate

    if len(levels) < 20:
        return {"suspicious": False}

    peak = float(np.percentile(levels, 95))
    active_threshold = max(-48.0, peak - 28.0)
    silence_threshold = min(-46.0, peak - 36.0)
    active = levels > active_threshold
    silent = levels < silence_threshold

    run_start: int | None = None
    minimum_silence_frames = 24
    search_start = int(len(levels) * 0.55)

    for index in range(search_start, len(levels)):
        if silent[index] and run_start is None:
            run_start = index
        elif not silent[index] and run_start is not None:
            if index - run_start >= minimum_silence_frames:
                resumed = np.where(active[index:])[0]
                if len(resumed):
                    start_index = index + int(resumed[0])
                    later = np.where(active[start_index:])[0]
                    end_index = start_index + int(later[-1])
                    resume_start = float(centers[start_index] - 0.01)
                    resume_end = float(centers[end_index] + 0.01)
                    resumed_duration = resume_end - resume_start
                    if resume_start > duration * 0.62 and resumed_duration <= 1.60:
                        return {
                            "suspicious": True,
                            "silence_start": max(
                                0.0, float(centers[run_start] - 0.01)
                            ),
                            "resume_start": max(0.0, resume_start),
                            "resume_end": min(duration, resume_end),
                            "resumed_duration": resumed_duration,
                        }
            run_start = None

    return {"suspicious": False}


def clipping_ratio(samples: np.ndarray) -> float:
    mono = np.asarray(samples, dtype=np.float32)
    return float(np.mean(np.abs(mono) >= 0.995))


def clean_tail_restart(
    samples: np.ndarray,
    sample_rate: int,
    info: dict[str, Any],
) -> tuple[np.ndarray, bool, float | None]:
    if not info.get("suspicious"):
        return samples, False, None

    trim_time = float(info["silence_start"]) + 0.03
    trim_sample = min(len(samples), max(1, int(trim_time * sample_rate)))
    cleaned = np.asarray(samples[:trim_sample], dtype=np.float32).copy()

    fade = min(len(cleaned), max(1, int(0.018 * sample_rate)))
    if fade > 1:
        cleaned[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

    return cleaned, True, trim_time


def candidate_flags(
    candidate: dict[str, Any],
    speech_slot: float,
) -> list[str]:
    flags: list[str] = []
    duration = float(candidate["duration"])
    ratio = duration / max(0.1, speech_slot)
    if candidate["tail_info"].get("suspicious"):
        flags.append("tail_restart")
    if float(candidate["clipping_ratio"]) > 0.0005:
        flags.append("clipping")
    if ratio < 0.48:
        flags.append("too_short")
    if ratio > 1.35:
        flags.append("too_long")
    if float(candidate["leading_silence"]) > 0.80:
        flags.append("leading_silence")
    return flags


def candidate_has_blocking_failure(
    candidate: dict[str, Any],
    speech_slot: float,
) -> bool:
    duration = float(candidate["duration"])
    ratio = duration / max(0.1, speech_slot)
    return (
        float(candidate["clipping_ratio"]) > 0.002
        or ratio < 0.30
        or ratio > 1.70
    )


def candidate_score(
    candidate: dict[str, Any],
    speech_slot: float,
) -> float:
    duration = float(candidate["duration"])
    score = 0.0
    flags = set(candidate.get("flags") or candidate_flags(candidate, speech_slot))
    weights = {
        "tail_restart": 100.0,
        "clipping": 80.0,
        "too_short": 65.0,
        "too_long": 45.0,
        "leading_silence": 15.0,
    }
    score += sum(weights.get(flag, 0.0) for flag in flags)
    score += float(candidate["clipping_ratio"]) * 5000.0

    ratio = duration / max(0.1, speech_slot)
    if ratio < 1.0:
        score += (1.0 - ratio) * 4.0
    else:
        score += (ratio - 1.0) * 8.0
    return score


def fit_without_slowdown(
    clean_path: Path,
    fitted_path: Path,
    placement_duration: float,
    tail_guard: float,
    max_safe_tempo: float,
) -> dict[str, Any]:
    clean_duration = probe_duration(clean_path)
    speech_slot = max(0.20, placement_duration - tail_guard)

    if clean_duration > speech_slot:
        tempo = clean_duration / speech_slot
        if tempo > max_safe_tempo + 1e-8:
            raise RuntimeError(
                "Утверждённый текст не помещается в безопасный речевой слот: "
                f"нужно atempo={tempo:.3f}, разрешено не более "
                f"{max_safe_tempo:.3f}."
            )
        tempo_filters = atempo_chain(tempo)
    else:
        tempo = 1.0
        tempo_filters = []

    filters = tempo_filters + [
        "highpass=f=55",
        "afade=t=in:st=0:d=0.008",
        f"apad=pad_dur={placement_duration:.6f}",
        f"atrim=duration={placement_duration:.6f}",
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
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(fitted_path),
        ]
    )

    return {
        "clean_duration": clean_duration,
        "placement_duration": placement_duration,
        "speech_slot": speech_slot,
        "tail_guard": tail_guard,
        "tempo": tempo,
        "max_safe_tempo": max_safe_tempo,
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
        placement_start_ms = (
            int(round(float(segment["start"]) * 1000.0))
            + int(segment.get("start_delay_ms", 0))
        )
        label = f"s{index}"
        filters.append(
            f"[{index}:a]adelay={placement_start_ms}:all=1[{label}]"
        )
        labels.append(f"[{label}]")

    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:"
        + "duration=longest:dropout_transition=0:normalize=0,"
        + f"apad=pad_dur={total_duration:.6f},"
        + f"atrim=duration={total_duration:.6f},"
        + "highpass=f=45,"
        + "alimiter=limit=0.985[out]"
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
            "pcm_s24le",
            str(output),
        ]
    )
    run_checked(command)


def set_seed(seed: int, torch_module: Any) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch_module.manual_seed(seed)


def checkpoint_signature(
    *,
    segment: dict[str, Any],
    steps: int,
    cfg: float,
    base_seed: int,
    reference_sha256: str,
    model_fingerprint: str,
) -> dict[str, Any]:
    return {
        "engine_schema": ENGINE_SCHEMA_VERSION,
        "segment": {
            "id": int(segment["id"]),
            "text": str(segment["text"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "tail_guard": float(segment["tail_guard"]),
            "start_delay_ms": int(segment["start_delay_ms"]),
            "reference_profile": str(segment["reference_profile"]),
            "minimum_candidates": int(segment["minimum_candidates"]),
            "max_safe_tempo": float(segment["max_safe_tempo"]),
            "placement_duration": float(segment["placement_duration"]),
        },
        "steps": int(steps),
        "cfg": float(cfg),
        "base_seed": int(base_seed),
        "reference_sha256": reference_sha256,
        "model_fingerprint": model_fingerprint,
    }


def load_valid_checkpoint(
    checkpoint_path: Path,
    fitted_path: Path,
    *,
    signature: dict[str, Any],
    expected_duration: float,
) -> dict[str, Any] | None:
    if not checkpoint_path.is_file() or not fitted_path.is_file():
        return None
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict):
        return None
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        return None
    if checkpoint.get("signature") != signature:
        return None
    if checkpoint.get("signature_sha256") != canonical_sha256(signature):
        return None
    artifact = checkpoint.get("artifact")
    report = checkpoint.get("report")
    if not isinstance(artifact, dict) or not isinstance(report, dict):
        return None
    try:
        if sha256_file(fitted_path) != artifact.get("sha256"):
            return None
        actual_duration = probe_duration(fitted_path)
    except (OSError, RuntimeError):
        return None
    if abs(actual_duration - expected_duration) > 0.06:
        return None
    if abs(actual_duration - float(artifact.get("duration", -1))) > 0.06:
        return None
    return report


def write_checkpoint(
    checkpoint_path: Path,
    fitted_path: Path,
    *,
    signature: dict[str, Any],
    report: dict[str, Any],
) -> None:
    atomic_write_json(
        checkpoint_path,
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "signature": signature,
            "signature_sha256": canonical_sha256(signature),
            "artifact": {
                "path": str(fitted_path),
                "sha256": sha256_file(fitted_path),
                "duration": round(probe_duration(fitted_path), 6),
            },
            "report": report,
        },
    )


def main() -> None:
    configure_utf8()

    parser = argparse.ArgumentParser(
        description="Universal adaptive VoxCPM2 CPU production renderer."
    )
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--extended-reference", required=True)
    parser.add_argument("--composite-reference", required=True)
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--steps", type=int, default=16)
    parser.add_argument("--cfg", type=float, default=1.80)
    parser.add_argument("--cache-length", type=int, default=4096)
    parser.add_argument("--video-duration", type=float, required=True)
    parser.add_argument("--base-seed", type=int, default=2026072600)
    parser.add_argument(
        "--max-safe-tempo",
        type=float,
        default=DEFAULT_MAX_SAFE_TEMPO,
    )
    parser.add_argument(
        "--force-segments",
        action="store_true",
        help="Ignore valid segment checkpoints and regenerate every segment.",
    )
    args = parser.parse_args()

    if not 1.0 <= float(args.max_safe_tempo) <= 1.50:
        raise RuntimeError("--max-safe-tempo должен быть в диапазоне 1.0..1.50.")

    # Must happen before torch or voxcpm imports.
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
        raise RuntimeError("FFmpeg/ffprobe не найдены в PATH.")

    import soundfile as sf
    import torch
    from voxcpm import VoxCPM

    if torch.cuda.is_available():
        raise RuntimeError(
            "CPU production environment unexpectedly exposes CUDA."
        )

    torch.set_num_threads(max(1, int(args.threads)))
    try:
        torch.set_num_interop_threads(2)
    except RuntimeError:
        pass

    references = {
        "extended": Path(args.extended_reference).resolve(),
        "composite": Path(args.composite_reference).resolve(),
    }
    for name, path in references.items():
        if not path.is_file():
            raise RuntimeError(f"Не найден {name}-референс: {path}")
    reference_hashes = {
        name: sha256_file(path) for name, path in references.items()
    }

    segments = read_segments(
        Path(args.segments_json).resolve(),
        default_max_safe_tempo=float(args.max_safe_tempo),
    )
    work_dir = Path(args.work_dir).resolve()
    output = Path(args.output).resolve()

    attempts_dir = work_dir / "attempts"
    clean_dir = work_dir / "segments_clean"
    fitted_dir = work_dir / "segments_fitted"
    checkpoints_dir = work_dir / "checkpoints"
    for directory in (
        attempts_dir,
        clean_dir,
        fitted_dir,
        checkpoints_dir,
        output.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    model_path = discover_model(Path(args.archive_root).resolve())
    model_info = model_identity(model_path)
    model_fingerprint = canonical_sha256(model_info)

    log("=== VOXCPM2 UNIVERSAL CPU PRODUCTION ===")
    log(f"Engine: {ENGINE_SCHEMA_VERSION}")
    log(f"PyTorch: {torch.__version__}")
    log("CUDA: hidden and unavailable")
    log(f"Steps: {args.steps}; CFG: {args.cfg}")
    log(
        "Candidates: one by default; retries only after QA flag "
        "or explicit minimum_candidates"
    )
    log(
        f"Safe tempo ceiling: {float(args.max_safe_tempo):.3f}; "
        "short successful speech is never slowed down"
    )

    load_started = time.perf_counter()
    model = VoxCPM.from_pretrained(
        str(model_path),
        device="cpu",
        optimize=False,
        load_denoiser=False,
    )

    cache_length = max(2048, int(args.cache_length))
    cache_dtype = next(model.tts_model.parameters()).dtype
    cache_device = model.tts_model.device
    model.tts_model.base_lm.setup_cache(
        1, cache_length, cache_device, cache_dtype
    )
    model.tts_model.residual_lm.setup_cache(
        1, cache_length, cache_device, cache_dtype
    )

    encode_sr = int(model.tts_model._encode_sample_rate)
    seconds_per_step = (
        int(model.tts_model.patch_size)
        * int(model.tts_model.chunk_size)
        / encode_sr
    )

    log(f"KV cache: {cache_length}")
    log(f"Шаг ≈ {seconds_per_step:.3f} сек.")
    log(f"Модель загружена за {time.perf_counter()-load_started:.1f} сек.")

    fitted_segments: list[tuple[dict[str, Any], Path]] = []
    report_segments: list[dict[str, Any]] = []
    total_synthesis = 0.0
    checkpoint_hits = 0

    for position, segment in enumerate(segments, start=1):
        segment_id = int(segment["id"])
        placement_duration = float(segment["placement_duration"])
        tail_guard = float(segment["tail_guard"])
        speech_slot = max(0.20, placement_duration - tail_guard)
        desired_steps = speech_slot / seconds_per_step
        max_len = max(24, int(math.ceil(desired_steps * 1.40)))
        profile = str(segment["reference_profile"])
        reference = references[profile]
        clean_path = clean_dir / f"{segment_id:04d}_{profile}_clean.wav"
        fitted_path = fitted_dir / f"{segment_id:04d}_{profile}_fitted.wav"
        checkpoint_path = checkpoints_dir / f"segment_{segment_id:04d}.json"

        signature = checkpoint_signature(
            segment=segment,
            steps=int(args.steps),
            cfg=float(args.cfg),
            base_seed=int(args.base_seed),
            reference_sha256=reference_hashes[profile],
            model_fingerprint=model_fingerprint,
        )

        if not args.force_segments:
            saved_report = load_valid_checkpoint(
                checkpoint_path,
                fitted_path,
                signature=signature,
                expected_duration=placement_duration,
            )
            if saved_report is not None:
                fitted_segments.append((segment, fitted_path))
                report_segments.append(saved_report)
                checkpoint_hits += 1
                log("")
                log(
                    f"[{position}/{len(segments)}] #{segment_id} "
                    "восстановлен из проверенного checkpoint"
                )
                continue

        log("")
        log(
            f"[{position}/{len(segments)}] #{segment_id} "
            f"{profile.upper()} / slot={placement_duration:.2f} сек. / "
            f"delay={int(segment['start_delay_ms'])} ms"
        )
        log(f"Текст: {segment['text']}")

        candidates: list[dict[str, Any]] = []
        minimum_candidates = int(segment["minimum_candidates"])

        for attempt_index in range(1, MAX_CANDIDATES + 1):
            if attempt_index > minimum_candidates and candidates:
                best_so_far = min(
                    candidates,
                    key=lambda item: candidate_score(item, speech_slot),
                )
                if not candidate_flags(best_so_far, speech_slot):
                    break
                if attempt_index == MAX_CANDIDATES and not any(
                    candidate_flags(item, speech_slot)
                    for item in candidates
                ):
                    break

            min_len = 2
            if attempt_index == MAX_CANDIDATES and candidates:
                if all(
                    float(item["duration"]) < speech_slot * 0.48
                    for item in candidates
                ):
                    min_len = max(
                        4,
                        int(math.floor(desired_steps * 0.42)),
                    )

            seed = int(args.base_seed) + segment_id * 100 + attempt_index
            set_seed(seed, torch)
            raw_path = (
                attempts_dir
                / f"{segment_id:04d}_{profile}_attempt{attempt_index}.wav"
            )

            started = time.perf_counter()
            with torch.inference_mode():
                wav = model.generate(
                    text=str(segment["text"]),
                    reference_wav_path=str(reference),
                    cfg_value=float(args.cfg),
                    inference_timesteps=max(1, int(args.steps)),
                    min_len=min_len,
                    max_len=max_len,
                    normalize=True,
                    denoise=False,
                    retry_badcase=False,
                )
            elapsed = time.perf_counter() - started
            total_synthesis += elapsed

            wav_np = np.asarray(wav, dtype=np.float32)
            sample_rate = int(model.tts_model.sample_rate)
            sf.write(str(raw_path), wav_np, sample_rate, subtype="PCM_24")

            leading, trailing = edge_silence(wav_np, sample_rate)
            tail_info = detect_tail_restart(wav_np, sample_rate)
            candidate: dict[str, Any] = {
                "attempt": attempt_index,
                "seed": seed,
                "path": str(raw_path),
                "samples": wav_np,
                "sample_rate": sample_rate,
                "duration": len(wav_np) / sample_rate,
                "min_len": min_len,
                "max_len": max_len,
                "tail_info": tail_info,
                "leading_silence": leading,
                "trailing_silence": trailing,
                "clipping_ratio": clipping_ratio(wav_np),
                "synthesis_seconds": elapsed,
            }
            candidate["flags"] = candidate_flags(candidate, speech_slot)
            candidate["score"] = candidate_score(candidate, speech_slot)
            candidates.append(candidate)

            log(
                f"attempt {attempt_index}: "
                f"{candidate['duration']:.2f} сек.; "
                f"score={candidate['score']:.2f}; "
                f"flags={candidate['flags'] or ['ok']}; "
                f"seed={seed}; CPU={elapsed:.1f}"
            )
            del wav
            gc.collect()

        selected = min(candidates, key=lambda item: float(item["score"]))
        if candidate_has_blocking_failure(selected, speech_slot):
            raise RuntimeError(
                f"Сегмент #{segment_id}: все кандидаты имеют критический "
                f"audio-QA fail; лучший flags={selected['flags']}."
            )

        selected_samples = np.asarray(selected["samples"], dtype=np.float32)
        clean_samples, tail_trimmed, trim_time = clean_tail_restart(
            selected_samples,
            int(selected["sample_rate"]),
            selected["tail_info"],
        )
        sf.write(
            str(clean_path),
            clean_samples,
            int(selected["sample_rate"]),
            subtype="PCM_24",
        )

        fit = fit_without_slowdown(
            clean_path,
            fitted_path,
            placement_duration,
            tail_guard,
            float(segment["max_safe_tempo"]),
        )

        fitted_segments.append((segment, fitted_path))
        segment_report = {
            **{
                key: value
                for key, value in segment.items()
                if key
                not in {
                    "window_duration",
                    "placement_duration",
                }
            },
            "window_duration": round(float(segment["window_duration"]), 6),
            "placement_duration": round(placement_duration, 6),
            "reference_path": str(reference),
            "reference_sha256": reference_hashes[profile],
            "selected_attempt": int(selected["attempt"]),
            "selected_seed": int(selected["seed"]),
            "selected_score": round(float(selected["score"]), 6),
            "selected_flags": list(selected["flags"]),
            "tail_trimmed": bool(tail_trimmed),
            "tail_trim_time": (
                round(float(trim_time), 6)
                if trim_time is not None
                else None
            ),
            "attempts": [
                {
                    "attempt": int(item["attempt"]),
                    "seed": int(item["seed"]),
                    "path": str(item["path"]),
                    "duration": round(float(item["duration"]), 6),
                    "min_len": int(item["min_len"]),
                    "max_len": int(item["max_len"]),
                    "tail_info": item["tail_info"],
                    "leading_silence": round(
                        float(item["leading_silence"]), 6
                    ),
                    "trailing_silence": round(
                        float(item["trailing_silence"]), 6
                    ),
                    "clipping_ratio": round(
                        float(item["clipping_ratio"]), 9
                    ),
                    "flags": list(item["flags"]),
                    "score": round(float(item["score"]), 6),
                    "synthesis_seconds": round(
                        float(item["synthesis_seconds"]), 3
                    ),
                }
                for item in candidates
            ],
            **{
                key: (
                    round(float(value), 6)
                    if isinstance(value, (float, np.floating))
                    else value
                )
                for key, value in fit.items()
            },
            "clean_path": str(clean_path),
            "fitted_path": str(fitted_path),
        }
        report_segments.append(segment_report)
        write_checkpoint(
            checkpoint_path,
            fitted_path,
            signature=signature,
            report=segment_report,
        )

        log(
            f"Выбран attempt {selected['attempt']} / seed {selected['seed']}; "
            f"tail_trimmed={tail_trimmed}; "
            f"atempo={fit['tempo']:.3f}; slowdown=False"
        )

        for item in candidates:
            item.pop("samples", None)
        del selected_samples, clean_samples
        gc.collect()

    build_timeline(fitted_segments, output, float(args.video_duration))
    final_duration = probe_duration(output)

    report = {
        "schema_version": ENGINE_SCHEMA_VERSION,
        "strategy": (
            "reference-only; one candidate by default; adaptive QA retries; "
            "no slowdown; start delay consumes the original segment slot; "
            "hash-validated checkpoints"
        ),
        "model": model_info,
        "model_fingerprint": model_fingerprint,
        "references": {
            key: {
                "path": str(value),
                "sha256": reference_hashes[key],
            }
            for key, value in references.items()
        },
        "output": str(output),
        "output_sha256": sha256_file(output),
        "video_duration": float(args.video_duration),
        "final_audio_duration": round(final_duration, 3),
        "total_synthesis_seconds": round(total_synthesis, 3),
        "checkpoint_hits": checkpoint_hits,
        "threads": int(args.threads),
        "steps": int(args.steps),
        "cfg": float(args.cfg),
        "cache_length": cache_length,
        "base_seed": int(args.base_seed),
        "default_max_safe_tempo": float(args.max_safe_tempo),
        "cuda_available": bool(torch.cuda.is_available()),
        "segments": report_segments,
    }

    report_path = output.with_suffix(".json")
    atomic_write_json(report_path, report)

    log("")
    log("=== UNIVERSAL SYNTHESIS ГОТОВ ===")
    log(f"WAV: {output}")
    log(f"JSON: {report_path}")
    log(f"Длительность: {final_duration:.2f} сек.")
    log(f"Checkpoint hits: {checkpoint_hits}/{len(segments)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        import traceback

        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
