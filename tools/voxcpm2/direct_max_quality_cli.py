#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for the direct max-quality speech-backend renderer."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
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

from services.speech_backends import (
    GENERATION_LENGTH_POLICY,
    GENERATION_LENGTH_REQUEST_POLICY,
    GENERATION_PROFILE_POLICY,
    GENERATION_PROFILE_REQUEST_POLICY,
    BackendGenerationLengthPlan,
    BackendGenerationLengthRequest,
    BackendGenerationProfilePlan,
    BackendGenerationProfileRequest,
    BackendGenerationRequest,
    BackendSessionConfig,
    get_backend,
)
from tools.voxcpm2.direct_max_quality_io import (
    POLICY,
    EXPECTED_ENCODE_SR,
    EXPECTED_OUTPUT_SR,
    REFERENCE_TAIL_SILENCE,
    MAX_TEMPO,
    PREFERRED_MAX_TEMPO,
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
    candidate_score as _base_candidate_score,
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

def _tempo_policy_penalty(duration: float, speech_slot: float) -> float:
    ratio = float(duration) / max(0.1, float(speech_slot))
    if ratio <= PREFERRED_MAX_TEMPO:
        return 0.0
    return 90.0 + (ratio - PREFERRED_MAX_TEMPO) * 400.0


def candidate_score(
    candidate: dict[str, Any],
    speech_slot: float,
    reference_voice: dict[str, Any],
) -> float:
    """Score with source-owned preference against avoidable hard fitting."""
    base = float(_base_candidate_score(candidate, speech_slot, reference_voice))
    penalty = _tempo_policy_penalty(float(candidate.get("duration") or 0.0), speech_slot)
    candidate["tempo_preference_penalty"] = float(penalty)
    candidate["required_tempo_estimate"] = float(candidate.get("duration") or 0.0) / max(0.1, float(speech_slot))
    return base + penalty


BASE_CANDIDATE_ATTEMPTS = 3
MAX_CANDIDATE_ATTEMPTS = 5
STRONG_CANDIDATE_SCORE = 85.0


def _build_generation_length_request(
    segment: dict[str, Any],
    *,
    duration_budget: float,
    attempt: int,
    previous_output_durations: tuple[float, ...],
) -> BackendGenerationLengthRequest:
    """Build model-neutral evidence before backend-specific length planning."""
    del segment
    return BackendGenerationLengthRequest(
        duration_budget=duration_budget,
        attempt=attempt,
        previous_output_durations=previous_output_durations,
    )


def _build_generation_profile_request(
    base_backend_options: Mapping[str, Any],
    *,
    attempt: int,
) -> BackendGenerationProfileRequest:
    """Build attempt evidence without interpreting backend option values."""
    return BackendGenerationProfileRequest(
        attempt=attempt,
        base_backend_options=base_backend_options,
    )


def _merge_backend_options(*sources: Mapping[str, Any]) -> dict[str, Any]:
    """Combine independent backend plans and fail closed on ownership overlap."""
    merged: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            raise TypeError("Backend plan options должны быть mapping.")
        overlap = sorted(set(merged).intersection(source))
        if overlap:
            raise RuntimeError(
                "Backend plans конфликтуют по option keys: " + ", ".join(overlap)
            )
        merged.update(dict(source))
    return merged


def _build_generation_request(
    session: Any,
    **kwargs: Any,
) -> BackendGenerationRequest:
    """Build the model-neutral request used by every direct CLI entrypoint."""
    del session
    raw_options = kwargs.get("backend_options")
    if not isinstance(raw_options, Mapping):
        raise TypeError("backend_options должен быть mapping из backend plans.")
    duration_budget = kwargs.get("duration_budget")
    return BackendGenerationRequest(
        text=str(kwargs.get("text") or ""),
        reference_audio=Path(kwargs["reference"]).resolve(),
        seed=int(kwargs.get("seed") or 0),
        duration_budget=(
            float(duration_budget) if duration_budget is not None else None
        ),
        backend_options=dict(raw_options),
    )


def _backend_generate(session: Any, **kwargs: Any) -> Any:
    """Generate through the typed request boundary, independent of import order."""
    request = _build_generation_request(session, **kwargs)
    if not isinstance(request, BackendGenerationRequest):
        raise TypeError("Generation request factory должен вернуть BackendGenerationRequest.")
    return session.generate(request)


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
        "generation_length_policy": GENERATION_LENGTH_POLICY,
        "generation_length_request_policy": GENERATION_LENGTH_REQUEST_POLICY,
        "generation_profile_policy": GENERATION_PROFILE_POLICY,
        "generation_profile_request_policy": GENERATION_PROFILE_REQUEST_POLICY,
        "failed_epoch": int(retry_epoch),
        "speech_slot": round(float(speech_slot), 6),
        "max_tempo": float(MAX_TEMPO),
        "attempts": [
            {
                "attempt": int(item.get("attempt") or 0),
                "seed": int(item.get("seed") or 0),
                "score": round(float(item.get("score") or 0.0), 6),
                "required_tempo": round(required_tempo(item, speech_slot), 6),
                "generation_length_request": item.get("generation_length_request") or {},
                "generation_length_plan": item.get("generation_length_plan") or {},
                "generation_profile_request": item.get("generation_profile_request") or {},
                "generation_profile_plan": item.get("generation_profile_plan") or {},
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


def _render_main() -> None:
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
    base_generation_profile_options = {
        "cfg": float(args.cfg),
        "steps": max(1, int(args.steps)),
    }
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
    log(f"Base backend profile options: {base_generation_profile_options}")
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
    log(f"Audio: {encode_sr} Hz encode -> {output_sr} Hz decode")
    log(f"Backend audio spec: {audio_spec.as_dict()}")
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
            "base_generation_profile_options": base_generation_profile_options,
            "base_seed": int(args.base_seed),
            "candidate_contract": {
                "adaptive_retry_policy": ADAPTIVE_RETRY_POLICY,
                "fit_tempo_policy": FIT_TEMPO_POLICY,
                "generation_length_policy": GENERATION_LENGTH_POLICY,
                "generation_length_request_policy": GENERATION_LENGTH_REQUEST_POLICY,
                "generation_profile_policy": GENERATION_PROFILE_POLICY,
                "generation_profile_request_policy": GENERATION_PROFILE_REQUEST_POLICY,
                "backend_adapter_policy": backend.adapter_policy,
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

            previous_output_durations = tuple(
                float(item["duration"])
                for item in candidates
            )
            length_request = _build_generation_length_request(
                segment,
                duration_budget=speech_slot,
                attempt=attempt_index,
                previous_output_durations=previous_output_durations,
            )
            if not isinstance(length_request, BackendGenerationLengthRequest):
                raise TypeError(
                    "Generation length request factory должен вернуть "
                    "BackendGenerationLengthRequest."
                )
            length_plan = backend.plan_generation_length(audio_spec, length_request)
            if not isinstance(length_plan, BackendGenerationLengthPlan):
                raise TypeError(
                    "Speech backend length planner должен вернуть "
                    "BackendGenerationLengthPlan."
                )
            if length_plan.backend_id != backend.backend_id:
                raise RuntimeError(
                    "Speech backend length plan принадлежит другому backend: "
                    f"{length_plan.backend_id} != {backend.backend_id}."
                )
            if (
                length_plan.duration_budget != length_request.duration_budget
                or length_plan.attempt != length_request.attempt
            ):
                raise RuntimeError(
                    "Speech backend length plan не соответствует typed request."
                )

            profile_request = _build_generation_profile_request(
                base_generation_profile_options,
                attempt=attempt_index,
            )
            if not isinstance(profile_request, BackendGenerationProfileRequest):
                raise TypeError(
                    "Generation profile request factory должен вернуть "
                    "BackendGenerationProfileRequest."
                )
            profile_plan = backend.plan_generation_profile(profile_request)
            if not isinstance(profile_plan, BackendGenerationProfilePlan):
                raise TypeError(
                    "Speech backend profile planner должен вернуть "
                    "BackendGenerationProfilePlan."
                )
            if profile_plan.backend_id != backend.backend_id:
                raise RuntimeError(
                    "Speech backend profile plan принадлежит другому backend: "
                    f"{profile_plan.backend_id} != {backend.backend_id}."
                )
            if profile_plan.attempt != profile_request.attempt:
                raise RuntimeError(
                    "Speech backend profile plan не соответствует typed request."
                )
            backend_options = _merge_backend_options(
                length_plan.backend_options,
                profile_plan.backend_options,
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
                    duration_budget=speech_slot,
                    backend_options=backend_options,
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
                "path": str(raw_path),
                "samples": wav_np,
                "sample_rate": sample_rate,
                "duration": len(wav_np) / sample_rate,
                "actual_speech_slot": speech_slot,
                "speech_slot_policy": SPEECH_SLOT_POLICY,
                "generation_length_request": length_request.as_dict(),
                "generation_length_plan": length_plan.as_dict(),
                "generation_profile_request": profile_request.as_dict(),
                "generation_profile_plan": profile_plan.as_dict(),
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
                f"lengthPlan={length_plan.metadata.get('policy')}; "
                f"profilePlan={profile_plan.metadata.get('policy')}; "
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
            "generation_length_policy": GENERATION_LENGTH_POLICY,
            "generation_length_request_policy": GENERATION_LENGTH_REQUEST_POLICY,
            "generation_profile_policy": GENERATION_PROFILE_POLICY,
            "generation_profile_request_policy": GENERATION_PROFILE_REQUEST_POLICY,
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
            "selected_generation_length_request": selected["generation_length_request"],
            "selected_generation_length_plan": selected["generation_length_plan"],
            "selected_generation_profile_request": selected["generation_profile_request"],
            "selected_generation_profile_plan": selected["generation_profile_plan"],
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
                        "duration": item["duration"],
                        "actual_speech_slot": item["actual_speech_slot"],
                        "speech_slot_policy": item["speech_slot_policy"],
                        "generation_length_request": item["generation_length_request"],
                        "generation_length_plan": item["generation_length_plan"],
                        "generation_profile_request": item["generation_profile_request"],
                        "generation_profile_plan": item["generation_profile_plan"],
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
        "schema_version": "5.8-backend-generation-profile-plan",
        "policy": POLICY,
        "speech_backend": backend.backend_id,
        "backend_environment": environment_policy.as_metadata(),
        "audio_spec": audio_spec.as_dict(),
        "strategy": (
            f"direct reference-only {backend.backend_id} + guarded references + exact SRT speech slots + "
            "typed model-neutral length/profile requests + backend-owned length and attempt profile "
            "planning + bounded adaptive candidates + durable failed-segment seed epochs + "
            "pre-selection fit-tempo/artifact/voice/cadence hard gates + source-guided prosody "
            "ranking + assembled timeline QA + no best-of-bad fallback"
        ),
        "candidate_retry_policy": ADAPTIVE_RETRY_POLICY,
        "fit_tempo_policy": FIT_TEMPO_POLICY,
        "generation_length_policy": GENERATION_LENGTH_POLICY,
        "generation_length_request_policy": GENERATION_LENGTH_REQUEST_POLICY,
        "generation_profile_policy": GENERATION_PROFILE_POLICY,
        "generation_profile_request_policy": GENERATION_PROFILE_REQUEST_POLICY,
        "speech_slot_policy": SPEECH_SLOT_POLICY,
        "retry_epoch_policy": RETRY_EPOCH_POLICY,
        "base_candidate_attempts": BASE_CANDIDATE_ATTEMPTS,
        "max_candidate_attempts": MAX_CANDIDATE_ATTEMPTS,
        "model_path": str(model_path),
        "model_config_sha256": config_sha,
        "encode_sample_rate": encode_sr,
        "output_sample_rate": output_sr,
        "base_generation_profile_options": base_generation_profile_options,
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

from tools.voxcpm2 import direct_universal_runtime as universal_runtime
from tools.voxcpm2 import direct_surgical_runtime as surgical_runtime
from tools.voxcpm2 import direct_final_audit_v3 as final_audit
from tools.voxcpm2.direct_failure_recovery import (
    POLICY as EARLY_STOP_RECOVERY_POLICY,
    run_with_failure_recovery,
)

UNIVERSAL_RUNTIME_POLICY = universal_runtime.POLICY
_UNIVERSAL_STATE: dict[str, Any] = {
    "segments": {},
    "work_dir": None,
    "current_segment_id": None,
    "total_segments": 0,
}
_universal_base_read_segments = read_segments
_universal_base_load_retry_epoch = load_retry_epoch
_universal_base_invalidate_segment_for_retry = invalidate_segment_for_retry
_universal_base_seed_for_attempt = seed_for_attempt
_universal_base_acceptable_candidates = _acceptable_candidates
_universal_base_raw_failure_evidence = _raw_failure_evidence


def read_segments(path: Path) -> list[dict[str, Any]]:
    segments = list(_universal_base_read_segments(Path(path)))
    _UNIVERSAL_STATE["segments"] = universal_runtime._segments_by_id(segments)
    _UNIVERSAL_STATE["total_segments"] = len(segments)
    return segments


def _universal_segment(segment_id: Any) -> dict[str, Any] | None:
    try:
        return _UNIVERSAL_STATE["segments"].get(int(segment_id))
    except (TypeError, ValueError, OverflowError):
        return None


def _universal_scope(
    work_dir: Path,
    segment_id: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    segment = _universal_segment(segment_id)
    context = direct_timing_guard.load_signature_context(work_dir)
    if not isinstance(segment, dict):
        return None, context, ""
    profile = str(segment.get("reference_profile") or "extended")
    reference = work_dir.resolve() / "references_guarded" / f"{profile}.wav"
    if reference.is_file():
        context = {
            **context,
            "reference_profile": profile,
            "reference_sha256": str(sha256_file(reference)),
        }
    fingerprint = direct_timing_guard.failure_scope_fingerprint(
        segment,
        signature_context=context,
    )
    return segment, context, fingerprint


def load_retry_epoch(work_dir: Path, segment_id: Any) -> int:
    _UNIVERSAL_STATE["work_dir"] = Path(work_dir).resolve()
    segment, _context, scope = _universal_scope(Path(work_dir), segment_id)
    if isinstance(segment, dict) and scope:
        try:
            return int(
                _universal_base_load_retry_epoch(
                    work_dir,
                    segment_id,
                    scope_fingerprint=scope,
                )
            )
        except TypeError:
            pass
    return int(_universal_base_load_retry_epoch(work_dir, segment_id))


def invalidate_segment_for_retry(
    work_dir: Path,
    segment: dict[str, Any],
    *,
    reason: str,
    fitted_path: Path | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _segment_value, context, scope = _universal_scope(
        Path(work_dir),
        segment.get("id"),
    )
    enriched = dict(evidence or {})
    enriched["failure_scope_fingerprint"] = scope or (
        direct_timing_guard.failure_scope_fingerprint(
            segment,
            signature_context=context,
        )
    )
    return _universal_base_invalidate_segment_for_retry(
        work_dir,
        segment,
        reason=reason,
        fitted_path=fitted_path,
        evidence=enriched,
    )


def seed_for_attempt(
    base_seed: int,
    segment_id: int,
    attempt: int,
    retry_epoch: int,
) -> int:
    _UNIVERSAL_STATE["current_segment_id"] = int(segment_id)
    total = max(1, int(_UNIVERSAL_STATE.get("total_segments") or 1))
    ordered = sorted(int(value) for value in _UNIVERSAL_STATE["segments"])
    try:
        position = ordered.index(int(segment_id)) + 1
    except ValueError:
        position = min(max(1, int(segment_id)), total)
    segment = _universal_segment(segment_id) or {"text": ""}
    slot = float(segment.get("speech_slot") or 1.0)
    work_dir = _UNIVERSAL_STATE.get("work_dir")
    context = (
        _universal_scope(Path(work_dir), segment_id)[1]
        if work_dir is not None
        else {}
    )
    if int(attempt) == 1 and work_dir is not None:
        direct_timing_guard.enforce_retry_epoch_budget(
            work_dir=Path(work_dir),
            segment=segment,
            retry_epoch=int(retry_epoch),
            signature_context=context,
        )
    plan = direct_timing_guard.candidate_efficiency_plan(
        segment,
        speech_slot=max(0.001, slot),
        retry_epoch=int(retry_epoch),
        max_tempo=MAX_TEMPO,
    )
    max_attempts = int(plan.get("max_attempts") or 5)
    log(
        "DUB_PROGRESS "
        + json.dumps(
            {
                "progress": universal_runtime._progress_value(
                    position=position,
                    total=total,
                    attempt=int(attempt),
                    max_attempts=max_attempts,
                ),
                "stage": (
                    f"voxcpm2 · сегмент {position}/{total} · "
                    f"вариант {int(attempt)}/{max_attempts} · "
                    f"epoch {int(retry_epoch)}"
                ),
                "policy": universal_runtime._PROGRESS_POLICY,
                "risk_band": plan.get("risk_band"),
            },
            ensure_ascii=False,
        )
    )
    return int(
        _universal_base_seed_for_attempt(
            base_seed,
            segment_id,
            attempt,
            retry_epoch,
        )
    )


def _universal_current_segment() -> dict[str, Any] | None:
    return _universal_segment(_UNIVERSAL_STATE.get("current_segment_id"))


def _acceptable_candidates(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> list[dict[str, Any]]:
    acceptable = list(_universal_base_acceptable_candidates(candidates, speech_slot))
    segment = _universal_current_segment()
    if not isinstance(segment, dict) or acceptable:
        return acceptable
    retry_epoch = int(segment.get("retry_epoch") or 0)
    work_dir = _UNIVERSAL_STATE.get("work_dir")
    context = (
        _universal_scope(Path(work_dir), segment.get("id"))[1]
        if work_dir is not None
        else {}
    )
    timing_failure = direct_timing_guard.evaluate_dynamic_timing_failure(
        candidates,
        segment=segment,
        speech_slot=float(speech_slot),
        retry_epoch=retry_epoch,
        max_tempo=MAX_TEMPO,
    )
    if timing_failure is not None and work_dir is not None:
        block = direct_timing_guard.persist_timing_block(
            Path(work_dir),
            segment=segment,
            signature_context=context,
            retry_epoch=retry_epoch,
            evidence=timing_failure,
        )
        raise RuntimeError(
            direct_timing_guard.format_timing_block_message(block, repeated=False)
        )
    plan = direct_timing_guard.candidate_efficiency_plan(
        segment,
        speech_slot=float(speech_slot),
        retry_epoch=retry_epoch,
        max_tempo=MAX_TEMPO,
    )
    budget = int(plan.get("max_attempts") or 5)
    if len(candidates) >= budget:
        summary = ", ".join(
            f"#{int(item.get('attempt') or 0)}: "
            f"score={float(item.get('score') or 0.0):.1f}, "
            f"tempo={float(item.get('required_tempo') or 0.0):.3f}"
            for item in candidates
        )
        raise RuntimeError(
            f"Сегмент #{int(segment.get('id') or 0)}: адаптивный бюджет "
            f"{budget} кандидатов исчерпан (risk={plan.get('risk_band')}); "
            f"hard-quality кандидат не найден. {summary}"
        )
    return acceptable


def _raw_failure_evidence(
    candidates: list[dict[str, Any]],
    *,
    speech_slot: float,
    retry_epoch: int,
) -> dict[str, Any]:
    payload = dict(
        _universal_base_raw_failure_evidence(
            candidates,
            speech_slot=speech_slot,
            retry_epoch=retry_epoch,
        )
    )
    segment = _universal_current_segment()
    work_dir = _UNIVERSAL_STATE.get("work_dir")
    if isinstance(segment, dict):
        context = (
            _universal_scope(Path(work_dir), segment.get("id"))[1]
            if work_dir is not None
            else {}
        )
        payload["failure_scope_fingerprint"] = (
            direct_timing_guard.failure_scope_fingerprint(
                segment,
                signature_context=context,
            )
        )
    payload["universal_runtime_policy"] = universal_runtime.POLICY
    return payload

SURGICAL_RUNTIME_POLICY = surgical_runtime.POLICY
_SURGICAL_RUNTIME_STATE: dict[str, Any] = {
    "segments": {},
    "work_dir": None,
    "retry_epochs": {},
    "current_segment_id": None,
    "runtime_context": None,
}
_surgical_base_log = log
_surgical_base_get_backend = get_backend
_surgical_base_prepare_reference = prepare_reference
_surgical_base_read_segments = read_segments
_surgical_base_build_generation_length_request = _build_generation_length_request
_surgical_base_acceptable_candidates = _acceptable_candidates
_surgical_base_raw_failure_evidence = _raw_failure_evidence
_surgical_hash_file = sha256_file
_surgical_max_tempo = float(MAX_TEMPO)
_surgical_expected_encode = int(EXPECTED_ENCODE_SR)
_surgical_expected_output = int(EXPECTED_OUTPUT_SR)

def log(message: str) -> Any:
        text = str(message)
        if text.startswith("Модель загружена за"):
            return _surgical_base_log(
                "Модель работает лениво: checkpoint-only resume не открывает веса; "
                "загрузка начнётся перед первым отсутствующим сегментом."
            )
        return _surgical_base_log(text)

def get_backend(name: str) -> Any:
        backend = _surgical_base_get_backend(name)
        if str(getattr(backend, "backend_id", "")).casefold() != "voxcpm2":
            return backend
        return surgical_runtime.direct_surgical_io.LazyBackend(
            backend,
            encode=_surgical_expected_encode,
            output=_surgical_expected_output,
            log=_surgical_base_log,
        )

def prepare_reference(source: Path, output: Path, sf_module: Any) -> dict[str, Any]:
        cached = surgical_runtime.direct_surgical_io.cached_reference(
            source=source,
            output=output,
            _surgical_hash_file=_surgical_hash_file,
            expected_sample_rate=_surgical_expected_encode,
        )
        if cached is not None:
            _surgical_base_log(
                f"Reference cache hit: {Path(output).stem} "
                f"({float(cached['duration']):.2f} сек.)"
            )
            return cached
        report = dict(_surgical_base_prepare_reference(source, output, sf_module))
        return surgical_runtime.direct_surgical_io.enrich_reference_report(
            report,
            source=source,
            _surgical_hash_file=_surgical_hash_file,
        )

def read_segments(path: Path) -> list[dict[str, Any]]:
        values = list(_surgical_base_read_segments(Path(path)))
        _SURGICAL_RUNTIME_STATE["segments"] = surgical_runtime._segments_by_id(values)
        return values

def _segment(segment_id: Any) -> dict[str, Any] | None:
        try:
            return _SURGICAL_RUNTIME_STATE["segments"].get(int(segment_id))
        except (TypeError, ValueError, OverflowError):
            return None

def _runtime_context() -> dict[str, str]:
        cached = _SURGICAL_RUNTIME_STATE.get("runtime_context")
        if isinstance(cached, dict):
            return cached
        repo = Path(__file__).resolve().parents[2]
        hashes: dict[str, str] = {}
        for relative in surgical_runtime._RUNTIME_SCOPE_FILES:
            path = repo / relative
            if not path.is_file():
                raise RuntimeError(f"Не найден runtime-файл для retry scope: {relative}")
            hashes[relative] = str(_surgical_hash_file(path))
        encoded = json.dumps(
            hashes,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        result = {
            "surgical_runtime_policy": surgical_runtime.POLICY,
            "surgical_runtime_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        _SURGICAL_RUNTIME_STATE["runtime_context"] = result
        return result

def _scope(
        work_dir: Path,
        segment_id: Any,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        segment = _segment(segment_id)
        context = {
            **surgical_runtime.guard.load_signature_context(work_dir),
            **_runtime_context(),
        }
        if not isinstance(segment, dict):
            return None, context, ""
        profile = str(segment.get("reference_profile") or "extended")
        reference = Path(work_dir).resolve() / "references_guarded" / f"{profile}.wav"
        if reference.is_file():
            context.update(
                reference_profile=profile,
                reference_sha256=str(_surgical_hash_file(reference)),
            )
        fingerprint = surgical_runtime.guard.failure_scope_fingerprint(
            segment,
            signature_context=context,
        )
        return segment, context, fingerprint

def load_retry_epoch(work_dir: Path, segment_id: Any) -> int:
        work = Path(work_dir).resolve()
        _SURGICAL_RUNTIME_STATE["work_dir"] = work
        segment, _context, scope = _scope(work, segment_id)
        value = int(
            surgical_runtime.direct_retry_epoch.load_retry_epoch(
                work,
                segment_id,
                scope_fingerprint=scope or None,
            )
        )
        key = int(segment_id)
        _SURGICAL_RUNTIME_STATE["retry_epochs"][key] = value
        if isinstance(segment, dict):
            segment["retry_epoch"] = value
        return value

def invalidate_segment_for_retry(
        work_dir: Path,
        segment: dict[str, Any],
        *,
        reason: str,
        fitted_path: Path | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _value, context, scope = _scope(work_dir, segment.get("id"))
        enriched = dict(evidence or {})
        enriched["failure_scope_fingerprint"] = scope or surgical_runtime.guard.failure_scope_fingerprint(
            segment,
            signature_context=context,
        )
        return surgical_runtime.direct_retry_epoch.invalidate_segment_for_retry(
            work_dir,
            segment,
            reason=reason,
            fitted_path=fitted_path,
            evidence=enriched,
        )

def _position(segment_id: int) -> tuple[int, int]:
        ordered = sorted(int(value) for value in _SURGICAL_RUNTIME_STATE["segments"])
        total = max(1, len(ordered))
        try:
            return ordered.index(int(segment_id)) + 1, total
        except ValueError:
            return min(max(1, int(segment_id)), total), total

def _build_generation_length_request(
        segment: dict[str, Any],
        *,
        duration_budget: float,
        attempt: int,
        previous_output_durations: tuple[float, ...],
    ) -> Any:
        segment_id = int(segment.get("id") or 0)
        _SURGICAL_RUNTIME_STATE["current_segment_id"] = segment_id
        epoch = int(_SURGICAL_RUNTIME_STATE["retry_epochs"].get(segment_id, segment.get("retry_epoch") or 0))
        work = _SURGICAL_RUNTIME_STATE.get("work_dir")
        context: dict[str, Any] = {}
        if work is not None:
            _value, context, _scope_hash = _scope(Path(work), segment_id)
            if int(attempt) == 1:
                surgical_runtime.guard.enforce_retry_epoch_budget(
                    work_dir=Path(work),
                    segment=segment,
                    retry_epoch=epoch,
                    signature_context=context,
                )
                marker = surgical_runtime.guard.load_matching_timing_block(
                    Path(work),
                    segment=segment,
                    signature_context=context,
                )
                if marker is not None:
                    raise surgical_runtime.guard.RetryableSynthesisFailure(
                        surgical_runtime.guard.format_timing_block_message(marker, repeated=True),
                        segment=segment,
                        evidence=marker.get("evidence") or {},
                        advance_retry=False,
                        failure_kind="unchanged_timing_block",
                    )
        plan = surgical_runtime.guard.candidate_efficiency_plan(
            segment,
            speech_slot=max(0.001, float(duration_budget)),
            retry_epoch=epoch,
            _surgical_max_tempo=_surgical_max_tempo,
        )
        position, total = _position(segment_id)
        _surgical_base_log(
            "DUB_PROGRESS "
            + json.dumps(
                {
                    "progress": surgical_runtime._progress_value(
                        position=position,
                        total=total,
                        attempt=int(attempt),
                        max_attempts=int(plan.get("max_attempts") or 5),
                    ),
                    "stage": (
                        f"voxcpm2 · сегмент {position}/{total} · "
                        f"вариант {int(attempt)}/{int(plan.get('max_attempts') or 5)} · "
                        f"epoch {epoch}"
                    ),
                    "policy": _PROGRESS_POLICY,
                    "risk_band": plan.get("risk_band"),
                },
                ensure_ascii=False,
            )
        )
        return _surgical_base_build_generation_length_request(
            segment,
            duration_budget=duration_budget,
            attempt=attempt,
            previous_output_durations=previous_output_durations,
        )

def seed_for_attempt(base_seed: int, segment_id: int, attempt: int, epoch: int) -> int:
        _SURGICAL_RUNTIME_STATE["current_segment_id"] = int(segment_id)
        _SURGICAL_RUNTIME_STATE["retry_epochs"][int(segment_id)] = int(epoch)
        return int(
            surgical_runtime.direct_retry_epoch.seed_for_attempt(base_seed, segment_id, attempt, epoch)
        )

def _acceptable_candidates(
        candidates: list[dict[str, Any]],
        speech_slot: float,
    ) -> list[dict[str, Any]]:
        try:
            acceptable = list(_surgical_base_acceptable_candidates(candidates, speech_slot))
        except RuntimeError as exc:
            message = str(exc)
            if not any(
                marker in message
                for marker in ("адаптивный бюджет", "не помещается естественно")
            ):
                raise
            acceptable = []
            legacy_error = exc
        else:
            legacy_error = None
        segment = _segment(_SURGICAL_RUNTIME_STATE.get("current_segment_id"))
        if not isinstance(segment, dict) or acceptable:
            return acceptable
        segment_id = int(segment.get("id") or 0)
        epoch = int(_SURGICAL_RUNTIME_STATE["retry_epochs"].get(segment_id, segment.get("retry_epoch") or 0))
        work = _SURGICAL_RUNTIME_STATE.get("work_dir")
        context = _scope(Path(work), segment_id)[1] if work is not None else {}
        timing = surgical_runtime.guard.evaluate_dynamic_timing_failure(
            candidates,
            segment=segment,
            speech_slot=float(speech_slot),
            retry_epoch=epoch,
            _surgical_max_tempo=_surgical_max_tempo,
        )
        if timing is not None and work is not None:
            marker = surgical_runtime.guard.persist_timing_block(
                Path(work),
                segment=segment,
                signature_context=context,
                retry_epoch=epoch,
                evidence=timing,
            )
            raise surgical_runtime.guard.RetryableSynthesisFailure(
                surgical_runtime.guard.format_timing_block_message(marker, repeated=False),
                segment=segment,
                evidence=timing,
                advance_retry=True,
                failure_kind="measured_timing_failure",
            )
        plan = surgical_runtime.guard.candidate_efficiency_plan(
            segment,
            speech_slot=float(speech_slot),
            retry_epoch=epoch,
            _surgical_max_tempo=_surgical_max_tempo,
        )
        budget = int(plan.get("max_attempts") or 5)
        if len(candidates) >= budget:
            evidence = {
                "kind": "adaptive-candidate-budget-exhausted",
                "candidate_plan": plan,
                "speech_slot": float(speech_slot),
                "_surgical_max_tempo": _surgical_max_tempo,
                "attempts": [
                    {
                        "attempt": int(item.get("attempt") or 0),
                        "seed": int(item.get("seed") or 0),
                        "duration": float(item.get("duration") or 0.0),
                        "required_tempo": float(item.get("required_tempo") or 0.0),
                        "score": float(item.get("score") or 0.0),
                    }
                    for item in candidates
                ],
            }
            raise surgical_runtime.guard.RetryableSynthesisFailure(
                f"Сегмент #{segment_id}: адаптивный бюджет {budget} кандидатов "
                f"исчерпан (risk={plan.get('risk_band')}); hard-quality кандидат не найден.",
                segment=segment,
                evidence=evidence,
                advance_retry=True,
                failure_kind="adaptive_budget_exhausted",
            )
        if legacy_error is not None:
            raise legacy_error
        return []

def _raw_failure_evidence(
        candidates: list[dict[str, Any]],
        *,
        speech_slot: float,
        retry_epoch: int,
    ) -> dict[str, Any]:
        payload = dict(
            _surgical_base_raw_failure_evidence(
                candidates,
                speech_slot=speech_slot,
                retry_epoch=retry_epoch,
            )
        )
        segment = _segment(_SURGICAL_RUNTIME_STATE.get("current_segment_id"))
        work = _SURGICAL_RUNTIME_STATE.get("work_dir")
        if isinstance(segment, dict):
            context = _scope(Path(work), segment.get("id"))[1] if work is not None else {}
            payload["failure_scope_fingerprint"] = surgical_runtime.guard.failure_scope_fingerprint(
                segment,
                signature_context=context,
            )
        payload["surgical_runtime_policy"] = surgical_runtime.POLICY
        return payload

FINAL_AUDIT_POLICY = final_audit.POLICY
_FINAL_AUDIT_STATE: dict[str, Any] = {
    "segments": [],
    "segments_json": None,
    "segments_json_sha256": "",
    "work_dir": None,
    "preflight_done": False,
    "model_context": {},
}
_final_audit_base_read_segments = read_segments
_final_audit_base_prepare_reference = prepare_reference
_final_audit_base_get_backend = get_backend


def _final_audit_base_context() -> dict[str, Any]:
    return {
        "final_audit_policy": final_audit.POLICY,
        "final_audit_sha256": final_audit._module_sha256(sha256_file),
        "segments_json_sha256": _FINAL_AUDIT_STATE.get("segments_json_sha256") or "",
        **dict(_FINAL_AUDIT_STATE.get("model_context") or {}),
    }


def _final_audit_persist_context() -> dict[str, Any]:
    work = _FINAL_AUDIT_STATE.get("work_dir")
    if work is None:
        return _final_audit_base_context()
    current = dict(surgical_runtime.guard.load_signature_context(Path(work)))
    current.update(_final_audit_base_context())
    surgical_runtime.guard.write_signature_context(Path(work), current)
    return current


def read_segments(path: Path) -> list[dict[str, Any]]:
    source = Path(path).resolve()
    final_audit._raw_segments(source)
    values = list(_final_audit_base_read_segments(source))
    if not values:
        raise RuntimeError("Direct renderer получил пустой список сегментов.")
    _FINAL_AUDIT_STATE.update(
        segments=values,
        segments_json=source,
        segments_json_sha256=str(sha256_file(source)),
        work_dir=None,
        preflight_done=False,
        model_context={},
    )
    return values


def _final_audit_model_discovered(model_path: Path) -> None:
    model = Path(model_path).resolve()
    _FINAL_AUDIT_STATE["model_context"] = final_audit._model_context(
        model,
        sha256_file,
    )
    _final_audit_persist_context()


def get_backend(name: str) -> Any:
    backend = _final_audit_base_get_backend(name)
    if str(getattr(backend, "backend_id", "")).strip().casefold() != "voxcpm2":
        return backend
    setter = getattr(backend, "set_model_discovery_callback", None)
    if not callable(setter):
        raise RuntimeError(
            "VoxCPM2 backend не поддерживает source-owned model discovery audit callback."
        )
    setter(_final_audit_model_discovered)
    return backend


def prepare_reference(source: Path, output: Path, sf_module: Any) -> dict[str, Any]:
    target = Path(output).resolve()
    work = (
        target.parent.parent
        if target.parent.name == "references_guarded"
        else target.parent
    )
    _FINAL_AUDIT_STATE["work_dir"] = work
    if not bool(_FINAL_AUDIT_STATE.get("preflight_done")):
        segments = list(_FINAL_AUDIT_STATE.get("segments") or [])
        if not segments:
            raise RuntimeError("Direct timing preflight вызван до read_segments.")
        context = _final_audit_persist_context()
        report = surgical_runtime.guard.run_pre_model_guard(
            segments,
            work_dir=work,
            max_tempo=float(MAX_TEMPO),
            signature_context=context,
        )
        _FINAL_AUDIT_STATE["preflight_done"] = True
        warnings = report.get("warning_ids") if isinstance(report, Mapping) else []
        log(
            "direct final timing preflight passed before references/model: "
            f"warnings={warnings or []}"
        )
    return dict(_final_audit_base_prepare_reference(source, output, sf_module))


_BASE_ALL = tuple(globals().get('__all__', ()))

from dataclasses import replace

from pathlib import Path

import types

from typing import Any

from services.speech_backends import (
    BackendGenerationLengthRequest,
    BackendGenerationRequest,
)

from tools.voxcpm2 import direct_monolith_contract

from tools.voxcpm2 import russian_pronunciation

from tools.voxcpm2 import source_prosody_policy

POLICY = "direct-cli-monolithic-voice-v5"

GENERATION_REQUEST_FACTORY_POLICY = "typed-generation-request-factory-v3"

GENERATION_LENGTH_HINT_POLICY = "cadence-minimum-completion-ratio-v1"

SYNTHESIS_TEXT_POLICY = russian_pronunciation.POLICY

PRONUNCIATION_VARIANT_POLICY = russian_pronunciation.VARIANT_POLICY

_CURRENT_ATTEMPT = 1

_CONTINUATION_REFERENCE: Path | None = None

_CONTINUATION_TEXT = ""

CONTINUATION_POLICY = "backend-capability-gated-previous-block-prompt-v2"

def set_continuation_context(reference: Path | None, text: str = "") -> None:
    global _CONTINUATION_REFERENCE, _CONTINUATION_TEXT
    _CONTINUATION_REFERENCE = Path(reference).resolve() if reference is not None else None
    _CONTINUATION_TEXT = str(text or "").strip()

_legacy_read_segments = read_segments

_legacy_seed_for_attempt = seed_for_attempt

_legacy_generate = _generate

_legacy_build_generation_length_request = _build_generation_length_request

_legacy_build_generation_request = _build_generation_request

_legacy_source_prosody_penalty = source_prosody_penalty

_legacy_candidate_hard_ok = candidate_hard_ok

_legacy_acceptable_candidates = _acceptable_candidates

_legacy_candidate_failure_summary = _candidate_failure_summary

_legacy_raw_failure_evidence = _raw_failure_evidence

def read_segments(path: Path) -> list[dict[str, Any]]:
    segments = _legacy_read_segments(Path(path))
    marked = [source_prosody_policy.mark_diagnostic_only(item) for item in segments]
    return direct_monolith_contract.register_segments(marked)

def seed_for_attempt(
    base_seed: int,
    segment_id: int,
    attempt: int,
    retry_epoch: int,
) -> int:
    global _CURRENT_ATTEMPT
    direct_monolith_contract.set_current_segment_id(segment_id)
    _CURRENT_ATTEMPT = max(1, int(attempt))
    return int(
        _legacy_seed_for_attempt(
            base_seed,
            segment_id,
            attempt,
            retry_epoch,
        )
    )

def _generate(
    model: Any,
    *,
    text: str,
    reference: Path,
    cfg: float,
    steps: int,
    min_len: int,
    max_len: int,
    seed: int,
) -> Any:
    """Compatibility seam; production cadence planning uses typed requests."""
    segment = direct_monolith_contract.current_segment() or {"text": text}
    synthesis = russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)
    kwargs = {
        "text": synthesis,
        "reference": reference,
        "cfg": cfg,
        "steps": steps,
        "min_len": min_len,
        "max_len": max_len,
        "seed": seed,
    }
    if _CONTINUATION_REFERENCE is not None:
        return _legacy_generate(
            model,
            **kwargs,
            continuation_reference=_CONTINUATION_REFERENCE,
            continuation_text=_CONTINUATION_TEXT,
        )
    return _legacy_generate(model, **kwargs)

def _build_generation_length_request(
    segment: dict[str, Any],
    *,
    duration_budget: float,
    attempt: int,
    previous_output_durations: tuple[float, ...],
) -> BackendGenerationLengthRequest:
    """Add cadence intent without interpreting backend-specific length units."""
    base_request = _legacy_build_generation_length_request(
        segment,
        duration_budget=duration_budget,
        attempt=attempt,
        previous_output_durations=previous_output_durations,
    )
    cadence = str(segment.get("cadence_type") or "")
    minimum_ratio = 0.58 if cadence in {"linked", "continuation"} else 0.40
    metadata = dict(base_request.metadata)
    metadata.update(
        {
            "policy": GENERATION_LENGTH_HINT_POLICY,
            "cadence_type": cadence,
        }
    )
    return replace(
        base_request,
        minimum_completion_ratio=minimum_ratio,
        metadata=metadata,
    )

def _build_generation_request(
    session: Any,
    **kwargs: Any,
) -> BackendGenerationRequest:
    """Extend the neutral request without replacing backend length options."""
    base_request = _legacy_build_generation_request(session, **kwargs)
    segment = direct_monolith_contract.current_segment() or {
        "text": base_request.text,
    }
    synthesis = russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)

    continuation_reference: Path | None = None
    continuation_text = ""
    if (
        _CONTINUATION_REFERENCE is not None
        and bool(getattr(session, "supports_continuation_context", False))
    ):
        continuation_reference = _CONTINUATION_REFERENCE
        continuation_text = _CONTINUATION_TEXT

    return BackendGenerationRequest(
        text=synthesis,
        reference_audio=base_request.reference_audio,
        seed=base_request.seed,
        duration_budget=base_request.duration_budget,
        style_instruction=str(segment.get("style_instruction") or ""),
        continuation_reference=continuation_reference,
        continuation_text=continuation_text,
        backend_options=base_request.backend_options,
    )

def source_prosody_penalty(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> float:
    """Keep source-language prosody as evidence, never as ranking weight."""
    pronunciation = segment.get("pronunciation")
    if not isinstance(pronunciation, dict):
        pronunciation = russian_pronunciation.prepare_segment(segment)
        segment["pronunciation"] = pronunciation
    display_segment = dict(segment)
    display_segment["text"] = str(
        pronunciation.get("display_text") or segment.get("text") or ""
    )
    ranking_segment = source_prosody_policy.ranking_view(display_segment)
    diagnostic_penalty = float(
        _legacy_source_prosody_penalty(candidate, ranking_segment)
    )
    monolith = direct_monolith_contract.evaluate_candidate(candidate, segment)
    match = candidate.get("source_prosody_match")
    if not isinstance(match, dict):
        match = {}
        candidate["source_prosody_match"] = match
    variant = russian_pronunciation.variant_for_attempt(
        segment,
        int(candidate.get("attempt") or _CURRENT_ATTEMPT),
    )
    match["monolith_identity"] = monolith
    match["source_prosody_policy"] = source_prosody_policy.POLICY
    match["source_prosody_ranking_enabled"] = False
    match["diagnostic_penalty"] = diagnostic_penalty
    match["synthesis_text_policy"] = SYNTHESIS_TEXT_POLICY
    match["pronunciation_variant_policy"] = PRONUNCIATION_VARIANT_POLICY
    match["pronunciation_variant"] = variant
    match["display_text"] = str(pronunciation.get("display_text") or "")
    match["synthesis_text_without_control"] = str(
        variant.get("synthesis_text_without_control") or ""
    )
    return float(direct_monolith_contract.candidate_penalty(candidate))

def candidate_hard_ok(candidate: dict[str, Any], speech_slot: float) -> bool:
    return bool(
        _legacy_candidate_hard_ok(candidate, speech_slot)
        and direct_monolith_contract.candidate_hard_ok(candidate)
    )

def _acceptable_candidates(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> list[dict[str, Any]]:
    result = list(_legacy_acceptable_candidates(candidates, speech_slot))
    direct_monolith_contract.record_acceptable(result)
    return result

def _monolith_diagnostic(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("monolith_identity")
    if not isinstance(evidence, dict):
        return "monolith=missing"
    failures = ",".join(str(value) for value in evidence.get("failures") or []) or "ok"
    identity = evidence.get("identity") or {}
    neighbour = evidence.get("neighbour") or {}
    transition = evidence.get("source_relative_transition") or {}
    start = evidence.get("start_artifact") or {}
    stress = evidence.get("stress_evidence") or {}
    variant = (candidate.get("source_prosody_match") or {}).get("pronunciation_variant") or {}
    return (
        "monolith={failures}, anchorSim={anchor:.3f}, neighbourSim={neighbour_sim}, "
        "adjF0={adj_f0}, sourceAdj={source_adj}, allowedAdj={allowed_adj}, "
        "f0={f0:.1f}, startLeak={start_leak}, stress={stress}, variant={variant}"
    ).format(
        failures=failures,
        anchor=float(identity.get("anchor_spectral_similarity") or 0.0),
        neighbour_sim=(
            f"{float(neighbour.get('spectral_similarity')):.3f}"
            if neighbour.get("spectral_similarity") is not None
            else "n/a"
        ),
        adj_f0=(
            f"{float(transition.get('generated_f0_median_jump_st')):.2f}st"
            if transition.get("generated_f0_median_jump_st") is not None
            else "n/a"
        ),
        source_adj=(
            f"{float(transition.get('source_f0_median_jump_st')):.2f}st"
            if transition.get("source_f0_median_jump_st") is not None
            else "n/a"
        ),
        allowed_adj=(
            f"{float(transition.get('allowed_f0_median_jump_st')):.2f}st"
            if transition.get("allowed_f0_median_jump_st") is not None
            else "n/a"
        ),
        f0=float(identity.get("f0_median") or 0.0),
        start_leak=bool(start.get("suspicious")),
        stress=(str(stress.get("reason") or "ok") if stress.get("required") else "n/a"),
        variant=int(variant.get("variant_index") or 0),
    )

def _candidate_failure_summary(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> str:
    base = _legacy_candidate_failure_summary(candidates, speech_slot)
    extras = [
        f"attempt {int(item.get('attempt') or 0)}: {_monolith_diagnostic(item)}"
        for item in candidates
        if isinstance(item, dict)
    ]
    return "; ".join(value for value in (base, *extras) if value)

def _raw_failure_evidence(
    candidates: list[dict[str, Any]],
    *,
    speech_slot: float,
    retry_epoch: int,
) -> dict[str, Any]:
    payload = dict(
        _legacy_raw_failure_evidence(
            candidates,
            speech_slot=speech_slot,
            retry_epoch=retry_epoch,
        )
    )
    payload["monolith_policy"] = direct_monolith_contract.POLICY
    payload["pronunciation_variant_policy"] = PRONUNCIATION_VARIANT_POLICY
    by_attempt = {
        int(item.get("attempt") or 0): item
        for item in candidates
        if isinstance(item, dict)
    }
    attempts = payload.get("attempts")
    if isinstance(attempts, list):
        for row in attempts:
            if not isinstance(row, dict):
                continue
            candidate = by_attempt.get(int(row.get("attempt") or 0))
            evidence = candidate.get("monolith_identity") if isinstance(candidate, dict) else None
            if isinstance(evidence, dict):
                row["monolith_identity"] = evidence
            match = candidate.get("source_prosody_match") if isinstance(candidate, dict) else None
            if isinstance(match, dict) and isinstance(match.get("pronunciation_variant"), dict):
                row["pronunciation_variant"] = match["pronunciation_variant"]
    return payload

read_segments = read_segments

seed_for_attempt = seed_for_attempt

_generate = _generate

_build_generation_length_request = _build_generation_length_request

_build_generation_request = _build_generation_request

set_continuation_context = set_continuation_context

source_prosody_penalty = source_prosody_penalty

candidate_hard_ok = candidate_hard_ok

_acceptable_candidates = _acceptable_candidates

_candidate_failure_summary = _candidate_failure_summary

_raw_failure_evidence = _raw_failure_evidence

def main() -> Any:
    return run_with_failure_recovery(_render_main, invalidate_segment_for_retry)


__all__ = sorted(
    set(name for name in _BASE_ALL if not name.startswith("__"))
    | {
        "CONTINUATION_POLICY",
        "GENERATION_LENGTH_HINT_POLICY",
        "GENERATION_REQUEST_FACTORY_POLICY",
        "POLICY",
        "PRONUNCIATION_VARIANT_POLICY",
        "SYNTHESIS_TEXT_POLICY",
        "_acceptable_candidates",
        "_backend_generate",
        "_build_generation_length_request",
        "_build_generation_request",
        "_candidate_failure_summary",
        "_generate",
        "_monolith_diagnostic",
        "_raw_failure_evidence",
        "candidate_hard_ok",
        "main",
        "read_segments",
        "seed_for_attempt",
        "set_continuation_context",
        "source_prosody_penalty",
    }
)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
