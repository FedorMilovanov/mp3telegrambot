#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the single direct VoxCPM2 max-quality renderer."""
from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2.direct_max_quality_io import (
    POLICY,
    EXPECTED_ENCODE_SR,
    EXPECTED_OUTPUT_SR,
    REFERENCE_TAIL_SILENCE,
    MAX_TEMPO,
    configure_utf8,
    log,
    probe_duration,
    sha256_file,
    discover_model,
    read_segments,
)
from tools.voxcpm2.direct_max_quality_analysis import (
    _mono,
    activity_stats,
    candidate_hard_ok,
    candidate_score,
    clean_tail_restart,
    clipping_ratio,
    detect_tail_restart,
    edge_silence,
    pitch_profile,
    prepare_reference,
)
from tools.voxcpm2.direct_max_quality_render import (
    _generate,
    _generation_profile,
    build_timeline,
    fit_without_slowdown,
    set_seed,
)


def _candidate_failure_summary(candidates: list[dict[str, Any]], speech_slot: float) -> str:
    parts: list[str] = []
    for item in candidates:
        voice = item.get("voice_match") or {}
        duration_ratio = float(item.get("duration") or 0.0) / max(0.1, speech_slot)
        parts.append(
            "attempt {attempt}: score={score:.2f}, duration×={duration:.3f}, "
            "voiced={voiced:.3f}, active={active:.3f}, gap={gap:.3f}, "
            "F0×={median:.3f}/{p90:.3f}, clip={clip:.6f}, tail_restart={tail}".format(
                attempt=int(item.get("attempt") or 0),
                score=float(item.get("score") or 0.0),
                duration=duration_ratio,
                voiced=float((item.get("pitch") or {}).get("voiced_ratio") or 0.0),
                active=float((item.get("activity") or {}).get("active_ratio") or 0.0),
                gap=float((item.get("activity") or {}).get("max_internal_gap") or 0.0),
                median=float(voice.get("f0_median_ratio") or 0.0),
                p90=float(voice.get("f0_p90_ratio") or 0.0),
                clip=float(item.get("clipping_ratio") or 0.0),
                tail=bool((item.get("tail_info") or {}).get("suspicious")),
            )
        )
    return "; ".join(parts)


def main() -> None:
    configure_utf8()
    parser = argparse.ArgumentParser(
        description="Direct maximum-quality VoxCPM2 CPU renderer."
    )
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--extended-reference", required=True)
    parser.add_argument("--composite-reference", required=True)
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--steps", type=int, default=16)
    parser.add_argument("--cfg", type=float, default=1.90)
    parser.add_argument("--cache-length", type=int, default=4096)
    parser.add_argument("--video-duration", type=float, required=True)
    parser.add_argument("--base-seed", type=int, default=2026072900)
    parser.add_argument("--force-segments", action="store_true")
    args = parser.parse_args()

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

    torch.set_num_threads(max(1, int(args.threads)))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    source_references = {
        "extended": Path(args.extended_reference).resolve(),
        "composite": Path(args.composite_reference).resolve(),
    }
    for name, path in source_references.items():
        if not path.is_file():
            raise RuntimeError(f"Не найден {name}-референс: {path}")

    segments = read_segments(Path(args.segments_json).resolve())
    work_dir = Path(args.work_dir).resolve()
    output = Path(args.output).resolve()
    attempts_dir = work_dir / "attempts"
    clean_dir = work_dir / "segments_clean"
    fitted_dir = work_dir / "segments_fitted"
    checkpoints_dir = work_dir / "checkpoints"
    guarded_dir = work_dir / "references_guarded"
    for directory in (
        attempts_dir,
        clean_dir,
        fitted_dir,
        checkpoints_dir,
        guarded_dir,
        output.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    references: dict[str, Path] = {}
    reference_reports: dict[str, dict[str, Any]] = {}
    for name, source in source_references.items():
        guarded = guarded_dir / f"{name}.wav"
        report = prepare_reference(source, guarded, sf)
        references[name] = guarded
        reference_reports[name] = report
    (guarded_dir / "references.json").write_text(
        json.dumps(reference_reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    model_path = discover_model(Path(args.archive_root).resolve())
    config_path = model_path / "config.json"
    config_sha = sha256_file(config_path)

    log("=== VOXCPM2 DIRECT MAX-QUALITY CPU RENDER ===")
    log(f"Policy: {POLICY}")
    log(f"PyTorch: {torch.__version__}")
    try:
        log(f"voxcpm package: {importlib.metadata.version('voxcpm')}")
    except importlib.metadata.PackageNotFoundError:
        log("voxcpm package version: unknown")
    log(f"Model: {model_path}")
    log(f"Model config SHA256: {config_sha}")
    log(f"CUDA доступна: {torch.cuda.is_available()} (должно быть False)")
    log(f"Base Steps: {args.steps}; Base CFG: {args.cfg}")
    log("Reference-only cloning; 2 candidates always, 3rd on quality warning")
    log("Candidate selection uses duration + artifacts + voiced ratio + F0 match")
    log("Best-of-bad candidates are forbidden")
    log("Official retry_badcase enabled when supported")

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
    model.tts_model.base_lm.setup_cache(1, cache_length, cache_device, cache_dtype)
    model.tts_model.residual_lm.setup_cache(1, cache_length, cache_device, cache_dtype)

    encode_sr = int(model.tts_model._encode_sample_rate)
    output_sr = int(model.tts_model.sample_rate)
    if encode_sr != EXPECTED_ENCODE_SR or output_sr != EXPECTED_OUTPUT_SR:
        raise RuntimeError(
            "Неожиданный аудиотракт VoxCPM2: "
            f"encoder={encode_sr}, decoder={output_sr}; "
            f"ожидалось {EXPECTED_ENCODE_SR}->{EXPECTED_OUTPUT_SR}."
        )
    seconds_per_step = (
        int(model.tts_model.patch_size)
        * int(model.tts_model.chunk_size)
        / encode_sr
    )
    log(f"AudioVAE: {encode_sr} Hz encode -> {output_sr} Hz decode")
    log(f"KV cache: {cache_length}; model step ≈ {seconds_per_step:.3f} сек.")
    log(f"Модель загружена за {time.perf_counter() - load_started:.1f} сек.")

    fitted_segments: list[tuple[dict[str, Any], Path]] = []
    report_segments: list[dict[str, Any]] = []
    total_synthesis = 0.0

    for position, segment in enumerate(segments, start=1):
        segment_id = int(segment["id"])
        target_duration = float(segment["end"]) - float(segment["start"])
        tail_guard = float(segment["tail_guard"])
        speech_slot = max(1.0, target_duration - tail_guard)
        desired_steps = speech_slot / seconds_per_step
        max_len = max(24, int(math.ceil(desired_steps * 1.40)))
        profile = str(segment["reference_profile"])
        reference = references[profile]
        reference_report = reference_reports[profile]
        clean_path = clean_dir / f"{segment_id:02d}_{profile}_clean.wav"
        fitted_path = fitted_dir / f"{segment_id:02d}_{profile}_fitted.wav"
        checkpoint_path = checkpoints_dir / f"segment_{segment_id:02d}.json"
        signature = {
            "policy": POLICY,
            "model_config_sha256": config_sha,
            "reference_sha256": reference_report["sha256"],
            "text": str(segment["text"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "tail_guard": tail_guard,
            "start_delay_ms": int(segment.get("start_delay_ms", 0)),
            "reference_profile": profile,
            "steps": int(args.steps),
            "cfg": float(args.cfg),
            "base_seed": int(args.base_seed),
        }

        if (
            not args.force_segments
            and checkpoint_path.is_file()
            and fitted_path.is_file()
        ):
            try:
                checkpoint = json.loads(
                    checkpoint_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                checkpoint = None
            if (
                isinstance(checkpoint, dict)
                and checkpoint.get("signature") == signature
            ):
                saved_report = checkpoint.get("report")
                if isinstance(saved_report, dict):
                    fitted_segments.append((segment, fitted_path))
                    report_segments.append(saved_report)
                    total_synthesis += sum(
                        float(item.get("synthesis_seconds", 0.0))
                        for item in saved_report.get("attempts", [])
                        if isinstance(item, dict)
                    )
                    log(
                        f"[{position}/{len(segments)}] #{segment_id} "
                        "восстановлен из fingerprinted checkpoint"
                    )
                    continue

        log("")
        log(
            f"[{position}/{len(segments)}] #{segment_id} {profile.upper()} / "
            f"{target_duration:.2f} сек. / "
            f"delay={int(segment.get('start_delay_ms', 0))} ms"
        )
        log(f"Текст: {segment['text']}")

        candidates: list[dict[str, Any]] = []
        for attempt_index in range(1, 4):
            if attempt_index == 3:
                best_so_far = min(
                    candidates,
                    key=lambda item: float(item["score"]),
                )
                if (
                    candidate_hard_ok(best_so_far, speech_slot)
                    and float(best_so_far["score"]) < 85.0
                ):
                    break
            min_len = 2
            if (
                attempt_index == 3
                and all(
                    float(item["duration"]) < speech_slot * 0.48
                    for item in candidates
                )
            ):
                min_len = max(4, int(math.floor(desired_steps * 0.42)))

            cfg_value, step_count = _generation_profile(
                attempt_index,
                float(args.cfg),
                max(1, int(args.steps)),
            )
            seed = int(args.base_seed) + segment_id * 100 + attempt_index
            set_seed(seed, torch)
            raw_path = (
                attempts_dir
                / f"{segment_id:02d}_{profile}_attempt{attempt_index}.wav"
            )

            started = time.perf_counter()
            with torch.inference_mode():
                wav = _generate(
                    model,
                    text=str(segment["text"]),
                    reference=reference,
                    cfg=cfg_value,
                    steps=step_count,
                    min_len=min_len,
                    max_len=max_len,
                    seed=seed,
                )
            elapsed = time.perf_counter() - started
            total_synthesis += elapsed

            wav_np = _mono(np.asarray(wav, dtype=np.float32))
            sample_rate = int(model.tts_model.sample_rate)
            sf.write(str(raw_path), wav_np, sample_rate, subtype="PCM_24")
            leading, trailing = edge_silence(wav_np, sample_rate)
            tail_info = detect_tail_restart(wav_np, sample_rate)
            candidate = {
                "attempt": attempt_index,
                "seed": seed,
                "cfg": cfg_value,
                "steps": step_count,
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
                "pitch": pitch_profile(wav_np, sample_rate),
                "activity": activity_stats(wav_np, sample_rate),
                "synthesis_seconds": elapsed,
            }
            candidate["score"] = candidate_score(
                candidate,
                speech_slot,
                reference_report,
            )
            candidates.append(candidate)
            voice = candidate["voice_match"]
            log(
                f"attempt {attempt_index}: {candidate['duration']:.2f} сек.; "
                f"score={candidate['score']:.2f}; "
                f"voiced={candidate['pitch']['voiced_ratio']:.3f}; "
                f"F0×={voice['f0_median_ratio']:.3f}/"
                f"{voice['f0_p90_ratio']:.3f}; "
                f"gap={candidate['activity']['max_internal_gap']:.3f}; "
                f"cfg={cfg_value:.2f}; steps={step_count}; "
                f"seed={seed}; CPU={elapsed:.1f}"
            )
            del wav
            gc.collect()

        acceptable = [
            item
            for item in candidates
            if candidate_hard_ok(item, speech_slot)
        ]
        if not acceptable:
            diagnostics = _candidate_failure_summary(candidates, speech_slot)
            for item in candidates:
                item.pop("samples", None)
            raise RuntimeError(
                f"Сегмент #{segment_id}: после {len(candidates)} прямых попыток "
                "нет ни одного hard-quality кандидата; best-of-bad запрещён. "
                f"{diagnostics}"
            )
        selected = min(acceptable, key=lambda item: float(item["score"]))

        clean_samples, tail_trimmed, trim_time = clean_tail_restart(
            selected["samples"],
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
            target_duration,
            tail_guard,
        )
        if float(fit["tempo"]) > MAX_TEMPO:
            raise RuntimeError(
                f"Сегмент #{segment_id}: для естественной речи требуется слишком "
                f"сильное ускорение atempo={fit['tempo']:.3f}; "
                f"предел={MAX_TEMPO:.2f}."
            )

        fitted_segments.append((segment, fitted_path))
        segment_report = {
            **segment,
            "renderer_policy": POLICY,
            "reference_path": str(reference),
            "reference_sha256": reference_report["sha256"],
            "selected_attempt": int(selected["attempt"]),
            "selected_seed": int(selected["seed"]),
            "selected_score": round(float(selected["score"]), 6),
            "selected_voice_match": {
                key: round(float(value), 6)
                for key, value in selected["voice_match"].items()
            },
            "tail_trimmed": bool(tail_trimmed),
            "tail_trim_time": (
                round(float(trim_time), 6)
                if trim_time is not None
                else None
            ),
            "fit": {
                key: (
                    round(float(value), 6)
                    if isinstance(value, (int, float))
                    else value
                )
                for key, value in fit.items()
            },
            "attempts": [
                {
                    key: (
                        round(float(value), 6)
                        if isinstance(value, (int, float))
                        else value
                    )
                    for key, value in {
                        "attempt": item["attempt"],
                        "seed": item["seed"],
                        "cfg": item["cfg"],
                        "steps": item["steps"],
                        "duration": item["duration"],
                        "score": item["score"],
                        "leading_silence": item["leading_silence"],
                        "trailing_silence": item["trailing_silence"],
                        "clipping_ratio": item["clipping_ratio"],
                        "synthesis_seconds": item["synthesis_seconds"],
                        **item["pitch"],
                        **item["activity"],
                        **item["voice_match"],
                        "tail_restart": bool(
                            item["tail_info"].get("suspicious")
                        ),
                    }.items()
                }
                for item in candidates
            ],
        }
        report_segments.append(segment_report)
        checkpoint_path.write_text(
            json.dumps(
                {"signature": signature, "report": segment_report},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        for item in candidates:
            item.pop("samples", None)
        del candidates, selected, clean_samples
        gc.collect()

    build_timeline(fitted_segments, output, float(args.video_duration))
    final_duration = probe_duration(output)
    report = {
        "schema_version": "5.0-direct-max-quality",
        "policy": POLICY,
        "strategy": (
            "direct reference-only VoxCPM2 + guarded references + "
            "multi-profile candidates + voiced/F0/artifact selection + "
            "no best-of-bad fallback"
        ),
        "model_path": str(model_path),
        "model_config_sha256": config_sha,
        "encode_sample_rate": encode_sr,
        "output_sample_rate": output_sr,
        "base_cfg": float(args.cfg),
        "base_steps": int(args.steps),
        "cache_length": cache_length,
        "reference_tail_silence": REFERENCE_TAIL_SILENCE,
        "max_tempo": MAX_TEMPO,
        "references": reference_reports,
        "segments": report_segments,
        "total_synthesis_seconds": round(total_synthesis, 3),
        "references_paths": {
            key: str(value)
            for key, value in references.items()
        },
        "output": str(output),
        "video_duration": float(args.video_duration),
        "final_audio_duration": final_duration,
        "timeline": str(output),
        "timeline_duration": final_duration,
        "threads": int(args.threads),
        "steps": int(args.steps),
        "cfg": float(args.cfg),
        "base_seed": int(args.base_seed),
        "cuda_available": bool(torch.cuda.is_available()),
    }
    report_path = output.with_suffix(".json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log("")
    log("=== DIRECT MAX-QUALITY RENDER COMPLETE ===")
    log(f"Timeline: {output}")
    log(f"Report: {report_path}")
