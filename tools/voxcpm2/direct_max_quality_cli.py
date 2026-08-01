#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the direct max-quality speech-backend renderer."""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from services.speech_backends import BackendSessionConfig, get_backend
from tools.voxcpm2.direct_max_quality_io import (
    POLICY,
    EXPECTED_ENCODE_SR,
    EXPECTED_OUTPUT_SR,
    REFERENCE_TAIL_SILENCE,
    MAX_TEMPO,
    SPEECH_SLOT_POLICY,
    configure_utf8,
    log,
    probe_duration,
    sha256_file,
    read_segments,
    speech_slot_seconds,
)
from tools.voxcpm2.direct_max_quality_analysis import (
    FIT_TEMPO_POLICY,
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
    required_tempo,
)
from tools.voxcpm2.direct_max_quality_render import (
    ADAPTIVE_RETRY_POLICY,
    _generate,  # noqa: F401 - legacy facade compatibility; production uses backend session
    _generation_profile,
    build_timeline,
    fit_without_slowdown,
    set_seed,
)
from tools.voxcpm2.direct_retry_epoch import (
    POLICY as RETRY_EPOCH_POLICY,
    invalidate_segment_for_retry,
    load_retry_epoch,
    seed_for_attempt,
)
from tools.voxcpm2.direct_source_prosody import (
    candidate_pitch_evidence_ok,
    source_prosody_penalty,
)

BASE_CANDIDATE_ATTEMPTS = 3
MAX_CANDIDATE_ATTEMPTS = 5
STRONG_CANDIDATE_SCORE = 85.0


def _backend_generate(session: Any, **kwargs: Any) -> Any:
    """Neutral hook allowing the monolithic facade to wrap a backend session."""
    return session.generate(**kwargs)


def _report_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, bool) or item is None or isinstance(item, str):
            result[str(key)] = item
        elif isinstance(item, (int, float)):
            number = float(item)
            result[str(key)] = round(number, 6) if math.isfinite(number) else repr(number)
        else:
            result[str(key)] = item
    return result


def _acceptable_candidates(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> list[dict[str, Any]]:
    return [
        item
        for item in candidates
        if candidate_hard_ok(item, speech_slot)
        and candidate_pitch_evidence_ok(item)
    ]


def _candidate_failure_summary(candidates: list[dict[str, Any]], speech_slot: float) -> str:
    parts: list[str] = []
    for item in candidates:
        voice = item.get("voice_match") or {}
        prosody = item.get("source_prosody_match") or {}
        cadence = item.get("cadence_evidence") or {}
        duration_ratio = float(item.get("duration") or 0.0) / max(0.1, speech_slot)
        tempo = required_tempo(item, speech_slot)
        source_detail = (
            "srcF0×={median:.3f}/{p90:.3f}, srcPenalty={penalty:.2f}".format(
                median=float(prosody.get("f0_median_ratio_to_source") or 0.0),
                p90=float(prosody.get("f0_p90_ratio_to_source") or 0.0),
                penalty=float(prosody.get("penalty") or 0.0),
            )
            if prosody.get("available")
            else "srcProsody=n/a"
        )
        cadence_failures = ",".join(str(value) for value in cadence.get("failures") or []) or "none"
        parts.append(
            "attempt {attempt}: score={score:.2f}, base={base:.2f}, duration×={duration:.3f}, "
            "atempo={tempo:.3f}/{tempo_limit:.2f}, epoch={epoch}, "
            "voiced={voiced:.3f}, active={active:.3f}, gap={gap:.3f}, "
            "F0×={median:.3f}/{p90:.3f}, rawPitch={raw_pitch}, cadence={cadence}, "
            "{source}, clip={clip:.6f}, tail_restart={tail}".format(
                attempt=int(item.get("attempt") or 0),
                score=float(item.get("score") or 0.0),
                base=float(item.get("base_score") or item.get("score") or 0.0),
                duration=duration_ratio,
                tempo=tempo,
                tempo_limit=MAX_TEMPO,
                epoch=int(item.get("retry_epoch") or 0),
                voiced=float((item.get("pitch") or {}).get("voiced_ratio") or 0.0),
                active=float((item.get("activity") or {}).get("active_ratio") or 0.0),
                gap=float((item.get("activity") or {}).get("max_internal_gap") or 0.0),
                median=float(voice.get("f0_median_ratio") or 0.0),
                p90=float(voice.get("f0_p90_ratio") or 0.0),
                raw_pitch=candidate_pitch_evidence_ok(item),
                cadence=cadence_failures,
                source=source_detail,
                clip=float(item.get("clipping_ratio") or 0.0),
                tail=bool((item.get("tail_info") or {}).get("suspicious")),
            )
        )
    return "; ".join(parts)


def _raw_failure_evidence(
    candidates: list[dict[str, Any]],
    *,
    speech_slot: float,
    retry_epoch: int,
) -> dict[str, Any]:
    return {
        "policy": POLICY,
        "retry_epoch_policy": RETRY_EPOCH_POLICY,
        "failed_epoch": int(retry_epoch),
        "speech_slot": round(float(speech_slot), 6),
        "max_tempo": float(MAX_TEMPO),
        "attempts": [
            {
                "attempt": int(item.get("attempt") or 0),
                "seed": int(item.get("seed") or 0),
                "score": round(float(item.get("score") or 0.0), 6),
                "required_tempo": round(required_tempo(item, speech_slot), 6),
                "cadence_failures": [
                    str(value)
                    for value in (item.get("cadence_evidence") or {}).get("failures") or []
                ],
                "late_tail": bool(
                    ((item.get("cadence_evidence") or {}).get("tail_artifact") or {}).get(
                        "suspicious"
                    )
                ),
            }
            for item in candidates
        ],
    }


def main() -> None:
    configure_utf8()
    parser = argparse.ArgumentParser(
        description="Direct maximum-quality speech backend renderer."
    )
    parser.add_argument("--speech-backend", default="voxcpm2")
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
    continuation_reset = globals().get("set_continuation_context")
    if callable(continuation_reset):
        continuation_reset(None, "")

    backend = get_backend(args.speech_backend)
    environment_policy = backend.process_environment(
        {"threads": max(1, int(args.threads))},
        base_environment=os.environ,
    )
    os.environ.update(environment_policy.as_dict(os.environ))

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe не найдены в PATH.")

    import soundfile as sf
    import torch

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
        json.dumps(reference_reports, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    model_path = backend.discover_model(Path(args.archive_root).resolve())
    config_path = model_path / "config.json"
    config_sha = sha256_file(config_path)

    log(f"=== {backend.backend_id} DIRECT MAX-QUALITY RENDER ===")
    log(f"Policy: {POLICY}")
    log(f"PyTorch: {torch.__version__}")
    log(f"Backend environment policy: {environment_policy.as_metadata()}")
    log(f"Model: {model_path}")
    log(f"Model config SHA256: {config_sha}")
    log(f"CUDA доступна: {torch.cuda.is_available()} (должно быть False)")
    log(f"Base Steps: {args.steps}; Base CFG: {args.cfg}")
    log(
        "Candidate policy: 2 обязательных; 3-я при quality warning; "
        "4-я и 5-я только когда ни один кандидат не проходит hard gates"
    )
    log(
        "Candidate selection uses exact SRT speech slot + fit tempo + artifacts + raw pitch + "
        "voice identity + Russian cadence + source-guided prosody"
    )
    log(
        "Failed segments advance a durable seed epoch; successful checkpoints remain reusable"
    )
    log("Best-of-bad candidates are forbidden")
    log("Official retry_badcase enabled when supported")

    load_started = time.perf_counter()
    cache_length = max(2048, int(args.cache_length))
    session = backend.open_session(
        BackendSessionConfig(
            model_path=model_path,
            options={"cache_length": cache_length},
        )
    )
    audio_spec = session.audio_spec
    encode_sr = int(audio_spec.encode_sample_rate)
    output_sr = int(audio_spec.output_sample_rate)
    if encode_sr != EXPECTED_ENCODE_SR or output_sr != EXPECTED_OUTPUT_SR:
        raise RuntimeError(
            f"Неожиданный аудиотракт backend={backend.backend_id}: "
            f"encoder={encode_sr}, decoder={output_sr}; "
            f"ожидалось {EXPECTED_ENCODE_SR}->{EXPECTED_OUTPUT_SR}."
        )
    seconds_per_step = float(audio_spec.seconds_per_step)
    log(f"Audio: {encode_sr} Hz encode -> {output_sr} Hz decode")
    log(f"KV cache: {audio_spec.cache_length}; model step ≈ {seconds_per_step:.3f} сек.")
    log(f"Модель загружена за {time.perf_counter() - load_started:.1f} сек.")

    fitted_segments: list[tuple[dict[str, Any], Path]] = []
    report_segments: list[dict[str, Any]] = []
    total_synthesis = 0.0

    for position, segment in enumerate(segments, start=1):
        segment_id = int(segment["id"])
        target_duration = float(segment["end"]) - float(segment["start"])
        tail_guard = float(segment["tail_guard"])
        speech_slot = speech_slot_seconds(target_duration, tail_guard)
        stored_slot = segment.get("speech_slot")
        if stored_slot is not None and abs(float(stored_slot) - speech_slot) > 1e-6:
            raise RuntimeError(
                f"Сегмент #{segment_id}: speech_slot изменился после read_segments: "
                f"stored={float(stored_slot):.6f}, computed={speech_slot:.6f}."
            )
        retry_epoch = load_retry_epoch(work_dir, segment_id)
        segment["speech_slot"] = speech_slot
        segment["speech_slot_policy"] = SPEECH_SLOT_POLICY
        segment["retry_epoch"] = retry_epoch
        segment["retry_epoch_policy"] = RETRY_EPOCH_POLICY
        desired_steps = speech_slot / seconds_per_step
        max_len = max(24, int(math.ceil(desired_steps * 1.40)))
        profile = str(segment["reference_profile"])
        reference = references[profile]
        reference_report = reference_reports[profile]
        clean_path = clean_dir / f"{segment_id:02d}_{profile}_clean.wav"
        fitted_path = fitted_dir / f"{segment_id:02d}_{profile}_fitted.wav"
        checkpoint_path = checkpoints_dir / f"segment_{segment_id:02d}.json"
        expression_signature = {
            "policy": str(segment.get("expression_policy") or ""),
            "tier": str(segment.get("expression_tier") or ""),
            "score": segment.get("expression_score"),
            "style_instruction": str(segment.get("style_instruction") or ""),
            "source_prosody": segment.get("source_prosody") or {},
        }
        signature = {
            "policy": POLICY,
            "model_config_sha256": config_sha,
            "reference_sha256": reference_report["sha256"],
            "text": str(segment["text"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "tail_guard": tail_guard,
            "speech_slot": speech_slot,
            "speech_slot_policy": SPEECH_SLOT_POLICY,
            "retry_epoch": retry_epoch,
            "retry_epoch_policy": RETRY_EPOCH_POLICY,
            "start_delay_ms": int(segment.get("start_delay_ms", 0)),
            "reference_profile": profile,
            "expression": expression_signature,
            "steps": int(args.steps),
            "cfg": float(args.cfg),
            "base_seed": int(args.base_seed),
            "candidate_contract": {
                "adaptive_retry_policy": ADAPTIVE_RETRY_POLICY,
                "fit_tempo_policy": FIT_TEMPO_POLICY,
                "retry_epoch_policy": RETRY_EPOCH_POLICY,
                "base_attempts": BASE_CANDIDATE_ATTEMPTS,
                "max_attempts": MAX_CANDIDATE_ATTEMPTS,
                "max_tempo": MAX_TEMPO,
            },
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
                    continuation_hook = globals().get("set_continuation_context")
                    if callable(continuation_hook):
                        continuation_hook(clean_path, str(segment.get("text") or ""))
                    total_synthesis += sum(
                        float(item.get("synthesis_seconds", 0.0))
                        for item in saved_report.get("attempts", [])
                        if isinstance(item, dict)
                    )
                    log(
                        f"[{position}/{len(segments)}] #{segment_id} "
                        f"восстановлен из checkpoint (seed epoch {retry_epoch})"
                    )
                    continue

        log("")
        log(
            f"[{position}/{len(segments)}] #{segment_id} {profile.upper()} / "
            f"{target_duration:.2f} сек. / slot={speech_slot:.2f} сек. / "
            f"epoch={retry_epoch} / "
            f"delay={int(segment.get('start_delay_ms', 0))} ms / "
            f"delivery={str(segment.get('expression_tier') or 'unknown')}"
        )
        log(f"Текст: {segment['text']}")
        if segment.get("style_instruction"):
            log(f"Подача: {segment['style_instruction']}")

        candidates: list[dict[str, Any]] = []
        for attempt_index in range(1, MAX_CANDIDATE_ATTEMPTS + 1):
            if attempt_index >= 3 and candidates:
                acceptable_so_far = _acceptable_candidates(candidates, speech_slot)
                if acceptable_so_far:
                    best_so_far = min(
                        acceptable_so_far,
                        key=lambda item: float(item["score"]),
                    )
                    if attempt_index == 3 and float(best_so_far["score"]) < STRONG_CANDIDATE_SCORE:
                        break
                    if attempt_index >= 4:
                        break

            min_len = 2
            if (
                attempt_index >= 3
                and candidates
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
            seed = seed_for_attempt(
                int(args.base_seed),
                segment_id,
                attempt_index,
                retry_epoch,
            )
            set_seed(seed, torch)
            raw_path = (
                attempts_dir
                / f"{segment_id:02d}_{profile}_epoch{retry_epoch}_attempt{attempt_index}.wav"
            )

            started = time.perf_counter()
            with torch.inference_mode():
                wav = _backend_generate(
                    session,
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
            sample_rate = output_sr
            sf.write(str(raw_path), wav_np, sample_rate, subtype="PCM_24")
            leading, trailing = edge_silence(wav_np, sample_rate)
            tail_info = detect_tail_restart(wav_np, sample_rate)
            candidate = {
                "attempt": attempt_index,
                "seed": seed,
                "retry_epoch": retry_epoch,
                "retry_epoch_policy": RETRY_EPOCH_POLICY,
                "cfg": cfg_value,
                "steps": step_count,
                "path": str(raw_path),
                "samples": wav_np,
                "sample_rate": sample_rate,
                "duration": len(wav_np) / sample_rate,
                "actual_speech_slot": speech_slot,
                "speech_slot_policy": SPEECH_SLOT_POLICY,
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
            base_score = candidate_score(
                candidate,
                speech_slot,
                reference_report,
            )
            prosody_penalty = source_prosody_penalty(candidate, segment)
            candidate["base_score"] = float(base_score)
            candidate["score"] = float(base_score) + float(prosody_penalty)
            candidate["required_tempo"] = required_tempo(candidate, speech_slot)
            candidate["fit_tempo_policy"] = FIT_TEMPO_POLICY
            candidates.append(candidate)
            voice = candidate["voice_match"]
            prosody = candidate["source_prosody_match"]
            cadence = candidate.get("cadence_evidence") or {}
            source_detail = (
                f"srcF0×={prosody['f0_median_ratio_to_source']:.3f}/"
                f"{prosody['f0_p90_ratio_to_source']:.3f}; "
                f"srcPenalty={prosody['penalty']:.2f}; "
                if prosody.get("available")
                else f"srcProsody=n/a({prosody.get('reason')}); "
            )
            log(
                f"attempt {attempt_index}: {candidate['duration']:.2f} сек.; "
                f"score={candidate['score']:.2f} (base={base_score:.2f}); "
                f"atempo={candidate['required_tempo']:.3f}/{MAX_TEMPO:.2f}; "
                f"voiced={candidate['pitch']['voiced_ratio']:.3f}; "
                f"F0×={voice['f0_median_ratio']:.3f}/"
                f"{voice['f0_p90_ratio']:.3f}; "
                f"rawPitch={candidate_pitch_evidence_ok(candidate)}; "
                f"cadence={','.join(cadence.get('failures') or []) or 'ok'}; "
                f"{source_detail}"
                f"gap={candidate['activity']['max_internal_gap']:.3f}; "
                f"cfg={cfg_value:.2f}; steps={step_count}; "
                f"epoch={retry_epoch}; seed={seed}; CPU={elapsed:.1f}"
            )
            del wav
            gc.collect()

        acceptable = _acceptable_candidates(candidates, speech_slot)
        if not acceptable:
            diagnostics = _candidate_failure_summary(candidates, speech_slot)
            invalidated = invalidate_segment_for_retry(
                work_dir,
                segment,
                reason="raw_candidate_hard_failure",
                fitted_path=fitted_path,
                evidence=_raw_failure_evidence(
                    candidates,
                    speech_slot=speech_slot,
                    retry_epoch=retry_epoch,
                ),
            )
            for item in candidates:
                item.pop("samples", None)
            raise RuntimeError(
                f"Сегмент #{segment_id}: после {len(candidates)} прямых попыток "
                "нет ни одного hard-quality кандидата; best-of-bad запрещён. "
                f"Следующий повтор использует seed epoch {invalidated['retry_epoch']}. "
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
            output_sample_rate=output_sr,
        )
        if abs(float(fit["speech_slot"]) - speech_slot) > 1e-6:
            fitted_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Сегмент #{segment_id}: renderer/fitter speech-slot mismatch: "
                f"renderer={speech_slot:.6f}, fitter={float(fit['speech_slot']):.6f}."
            )
        if float(fit["tempo"]) > MAX_TEMPO + 1e-9:
            fitted_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Сегмент #{segment_id}: нарушен fit-tempo invariant: "
                f"выбран hard-quality кандидат atempo={fit['tempo']:.3f}, "
                f"предел={MAX_TEMPO:.2f}."
            )

        fitted_segments.append((segment, fitted_path))
        continuation_hook = globals().get("set_continuation_context")
        if callable(continuation_hook):
            continuation_hook(clean_path, str(segment.get("text") or ""))
        segment_report = {
            **segment,
            "renderer_policy": POLICY,
            "candidate_retry_policy": ADAPTIVE_RETRY_POLICY,
            "fit_tempo_policy": FIT_TEMPO_POLICY,
            "speech_slot_policy": SPEECH_SLOT_POLICY,
            "speech_slot": speech_slot,
            "retry_epoch_policy": RETRY_EPOCH_POLICY,
            "retry_epoch": retry_epoch,
            "max_candidate_attempts": MAX_CANDIDATE_ATTEMPTS,
            "reference_path": str(reference),
            "reference_sha256": reference_report["sha256"],
            "selected_attempt": int(selected["attempt"]),
            "selected_seed": int(selected["seed"]),
            "selected_raw_pitch_evidence_ok": True,
            "selected_required_tempo": round(float(selected["required_tempo"]), 6),
            "selected_base_score": round(float(selected["base_score"]), 6),
            "selected_score": round(float(selected["score"]), 6),
            "selected_voice_match": {
                key: round(float(value), 6)
                for key, value in selected["voice_match"].items()
            },
            "selected_source_prosody_match": _report_mapping(
                selected.get("source_prosody_match")
            ),
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
                        and not isinstance(value, bool)
                        else value
                    )
                    for key, value in {
                        "attempt": item["attempt"],
                        "seed": item["seed"],
                        "retry_epoch": item["retry_epoch"],
                        "retry_epoch_policy": item["retry_epoch_policy"],
                        "cfg": item["cfg"],
                        "steps": item["steps"],
                        "duration": item["duration"],
                        "actual_speech_slot": item["actual_speech_slot"],
                        "speech_slot_policy": item["speech_slot_policy"],
                        "required_tempo": item["required_tempo"],
                        "fit_tempo_policy": item["fit_tempo_policy"],
                        "base_score": item["base_score"],
                        "score": item["score"],
                        "raw_pitch_evidence_ok": candidate_pitch_evidence_ok(item),
                        "leading_silence": item["leading_silence"],
                        "trailing_silence": item["trailing_silence"],
                        "clipping_ratio": item["clipping_ratio"],
                        "synthesis_seconds": item["synthesis_seconds"],
                        **item["pitch"],
                        **item["activity"],
                        **item["voice_match"],
                        "source_prosody_match": _report_mapping(
                            item.get("source_prosody_match")
                        ),
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
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        for item in candidates:
            item.pop("samples", None)
        del candidates, selected, clean_samples
        gc.collect()

    build_timeline(
        fitted_segments,
        output,
        float(args.video_duration),
        output_sample_rate=output_sr,
    )
    final_duration = probe_duration(output)
    report = {
        "schema_version": "5.5-direct-durable-seed-epochs",
        "policy": POLICY,
        "speech_backend": backend.backend_id,
        "backend_environment": environment_policy.as_metadata(),
        "audio_spec": audio_spec.as_dict(),
        "strategy": (
            f"direct reference-only {backend.backend_id} + guarded references + exact SRT speech slots + "
            "bounded adaptive candidates + durable failed-segment seed epochs + "
            "pre-selection fit-tempo/artifact/voice/cadence hard gates + "
            "source-guided prosody ranking + assembled timeline QA + no best-of-bad fallback"
        ),
        "candidate_retry_policy": ADAPTIVE_RETRY_POLICY,
        "fit_tempo_policy": FIT_TEMPO_POLICY,
        "speech_slot_policy": SPEECH_SLOT_POLICY,
        "retry_epoch_policy": RETRY_EPOCH_POLICY,
        "base_candidate_attempts": BASE_CANDIDATE_ATTEMPTS,
        "max_candidate_attempts": MAX_CANDIDATE_ATTEMPTS,
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
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    log("")
    log("=== DIRECT MAX-QUALITY RENDER COMPLETE ===")
    log(f"Timeline: {output}")
    log(f"Report: {report_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)