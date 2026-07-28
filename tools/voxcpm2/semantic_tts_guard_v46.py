#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resumable focused QA for Professional Audio.

This overlay keeps every segment already accepted by Quality v4.2, resumes a
failed full render from its checkpoints, gives only the persistent failures more
seeds, and reports the exact failed criteria instead of a bare list of IDs.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import soundfile as sf

from tools.voxcpm2 import generic_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import professional_audio_v45 as professional
from tools.voxcpm2 import semantic_tts_guard as legacy
from tools.voxcpm2 import semantic_tts_guard_v4 as base

_POLICY = "focused-resume-v4.6"
_ORIGINAL_PREPARE_REPAIR = repair_runtime.prepare_repair_checkpoints
_INSTALLED = False


def log(message: str) -> None:
    print(f"[TTS-QA-V4.6] {message}", flush=True)


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _checkpoint_ids(work_dir: Path) -> set[int]:
    result: set[int] = set()
    for path in (work_dir / "checkpoints").glob("segment_*.json"):
        match = re.search(r"segment_(\d+)", path.stem)
        if match:
            result.add(int(match.group(1)))
    return result


def _checkpoint_base_seed(work_dir: Path) -> int:
    values: list[int] = []
    for path in (work_dir / "checkpoints").glob("segment_*.json"):
        payload = _load_object(path)
        signature = payload.get("signature") if isinstance(payload, dict) else None
        if isinstance(signature, dict):
            try:
                values.append(int(signature.get("base_seed") or 0))
            except (TypeError, ValueError):
                pass
    return max(values, default=0)


def _latest_failed_report(root: Path) -> tuple[Path | None, dict[str, Any]]:
    candidates = sorted(
        root.rglob("*.semantic_qa.json"),
        key=lambda path: path.stat().st_mtime if path.is_file() else 0.0,
        reverse=True,
    )
    for path in candidates:
        payload = _load_object(path)
        failed = payload.get("failed_segment_ids")
        if isinstance(failed, list) and failed:
            return path, payload
    return None, {}


def _write_marker(
    work_dir: Path,
    *,
    state: str,
    base_seed: int,
    failed_ids: Iterable[int],
    pipeline_signature: str = "",
    report_path: Path | None = None,
    round_index: int = 0,
) -> None:
    marker = {
        "guard_version": base._GUARD_VERSION,
        "policy": _POLICY,
        "state": str(state),
        "base_seed": int(base_seed),
        "pipeline_signature": str(pipeline_signature),
        "failed_segment_ids": sorted({int(value) for value in failed_ids}),
        "completed_rounds": int(round_index),
        "report": str(report_path) if report_path else "",
    }
    path = work_dir / "semantic_guard.marker.json"
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")


def _recover_partial_marker(
    work_dir: Path,
    *,
    all_ids: set[int],
    selected_ids: set[int],
) -> bool:
    marker_path = work_dir / "semantic_guard.marker.json"
    marker = _load_object(marker_path)
    if marker.get("guard_version") == base._GUARD_VERSION:
        return True

    report_path, report = _latest_failed_report(work_dir.parent)
    failed_ids = {
        int(value)
        for value in report.get("failed_segment_ids", [])
        if str(value).isdigit()
    }
    checkpoints = _checkpoint_ids(work_dir)
    good_ids = all_ids - failed_ids
    if (
        not failed_ids
        or not selected_ids.issubset(failed_ids)
        or not good_ids
        or not good_ids.issubset(checkpoints)
    ):
        return False

    _write_marker(
        work_dir,
        state="partial_recovered",
        base_seed=_checkpoint_base_seed(work_dir),
        failed_ids=failed_ids,
        report_path=report_path,
    )
    log(
        "восстановлено частичное состояние: хорошие сегменты "
        f"{sorted(good_ids)} сохранены; повторяются только {sorted(selected_ids)}"
    )
    return True


def _prepare_repair_checkpoints_v46(
    work_dir: Path,
    *,
    all_ids: set[int],
    selected_ids: set[int],
    new_base_seed: int,
    repair_all: bool,
) -> None:
    if not repair_all:
        _recover_partial_marker(
            work_dir,
            all_ids={int(value) for value in all_ids},
            selected_ids={int(value) for value in selected_ids},
        )
    _ORIGINAL_PREPARE_REPAIR(
        work_dir,
        all_ids=all_ids,
        selected_ids=selected_ids,
        new_base_seed=new_base_seed,
        repair_all=repair_all,
    )


def _resume_partial_signature(
    work_dir: Path,
    *,
    pipeline_signature: str,
    all_ids: set[int],
) -> bool:
    marker_path = work_dir / "semantic_guard.marker.json"
    marker = _load_object(marker_path)
    if (
        marker.get("guard_version") == base._GUARD_VERSION
        and marker.get("pipeline_signature") == pipeline_signature
    ):
        return True

    state = str(marker.get("state") or "")
    failed_ids = {
        int(value)
        for value in marker.get("failed_segment_ids", [])
        if str(value).isdigit()
    }
    checkpoints = _checkpoint_ids(work_dir)
    if (
        marker.get("guard_version") == base._GUARD_VERSION
        and state.startswith("partial")
        and failed_ids
        and (all_ids - failed_ids).issubset(checkpoints)
    ):
        marker["pipeline_signature"] = pipeline_signature
        marker["state"] = "partial_resumed"
        marker_path.write_text(
            json.dumps(marker, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(
            "pipeline signature восстановлена; готовые checkpoints не удаляются"
        )
        return True
    return False


def _calmer_reference_profile(command: Sequence[str]) -> str:
    measurements: list[tuple[float, float, str]] = []
    for profile, flag in (
        ("extended", "--extended-reference"),
        ("composite", "--composite-reference"),
    ):
        try:
            path = Path(legacy._flag_value(command, flag)).resolve()
            samples, sample_rate = sf.read(path, dtype="float32")
            if np.asarray(samples).ndim > 1:
                samples = np.asarray(samples, dtype=np.float32).mean(axis=1)
            pitch = professional.pitch_profile(np.asarray(samples), int(sample_rate))
            measurements.append(
                (
                    float(pitch.get("f0_median") or 9999.0),
                    float(pitch.get("f0_p90") or 9999.0),
                    profile,
                )
            )
        except Exception:
            continue
    return min(measurements)[2] if measurements else "extended"


def _checks_by_id(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }


def _failed_parts(check: dict[str, Any]) -> list[str]:
    result: list[str] = []
    acoustic = check.get("acoustic")
    if isinstance(acoustic, dict) and not acoustic.get("passed", True):
        result.append(
            "акустика "
            f"rms={acoustic.get('rms')}, clip={acoustic.get('clipping_ratio')}, "
            f"zcr={acoustic.get('zero_crossing_rate')}"
        )
    semantic = check.get("semantic")
    if isinstance(semantic, dict) and not semantic.get("passed", True):
        heard = str(semantic.get("heard") or "").replace("\n", " ")[:120]
        result.append(
            "распознавание "
            f"recall={semantic.get('token_recall')}, sim={semantic.get('sequence_similarity')}, "
            f"услышано=«{heard}»"
        )
    timing = check.get("timing")
    if isinstance(timing, dict) and not timing.get("passed", True):
        result.append(
            "стык "
            f"onset={timing.get('onset_ms')}ms, tail={timing.get('trailing_ms')}ms, "
            f"start_artifact={timing.get('isolated_start_artifact')}"
        )
    continuity = check.get("continuity_v45")
    if isinstance(continuity, dict) and not continuity.get("passed", True):
        result.append(
            "пауза "
            f"gap={continuity.get('max_internal_gap')}s, active={continuity.get('active_ratio')}"
        )
    voice = check.get("voice_match_v45")
    if isinstance(voice, dict) and not voice.get("passed", True):
        result.append(
            "голос "
            f"median×{voice.get('f0_median_ratio')}, p90×{voice.get('f0_p90_ratio')}, "
            f"voiced={voice.get('voiced_ratio')}"
        )
    return result or ["неуточнённый критерий QA"]


def _failure_summary(report: dict[str, Any]) -> str:
    checks = _checks_by_id(report)
    failed_ids = [
        int(value)
        for value in report.get("failed_segment_ids", [])
        if str(value).isdigit()
    ]
    return "; ".join(
        f"#{segment_id}: " + ", ".join(_failed_parts(checks.get(segment_id, {})))
        for segment_id in failed_ids
    )


def _calm_punctuation(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"[;:—–]+", ",", value)
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r",{2,}", ",", value)
    value = re.sub(r"\s+", " ", value).strip(" ,")
    if value and value[-1] not in ".!?":
        value += "."
    return value


def _adapt_persistent_failures(
    guarded_segments: Path,
    segments: list[dict[str, Any]],
    failed_ids: Iterable[int],
    report: dict[str, Any],
    *,
    completed_round: int,
    calmer_profile: str,
) -> None:
    if completed_round < 3:
        return
    failed_set = {int(value) for value in failed_ids}
    checks = _checks_by_id(report)
    changed = False
    for item in segments:
        segment_id = int(item["id"])
        if segment_id not in failed_set:
            continue
        check = checks.get(segment_id, {})
        semantic = check.get("semantic") if isinstance(check, dict) else {}
        timing = check.get("timing") if isinstance(check, dict) else {}
        continuity = check.get("continuity_v45") if isinstance(check, dict) else {}
        voice = check.get("voice_match_v45") if isinstance(check, dict) else {}

        adaptations: list[str] = list(item.get("qa_adaptations") or [])
        if isinstance(voice, dict) and not voice.get("passed", True):
            if item.get("reference_profile") != calmer_profile:
                item["reference_profile"] = calmer_profile
                adaptations.append(f"calmer_reference:{calmer_profile}")
                changed = True

        if (
            isinstance(continuity, dict)
            and not continuity.get("passed", True)
        ) or (
            isinstance(semantic, dict)
            and not semantic.get("passed", True)
        ):
            calmer_text = _calm_punctuation(str(item.get("text") or ""))
            if calmer_text and calmer_text != item.get("text"):
                item["text"] = calmer_text
                adaptations.append("calm_punctuation")
                changed = True

        if isinstance(timing, dict) and not timing.get("passed", True):
            trailing = float(timing.get("trailing_ms") or 0.0)
            onset = float(timing.get("onset_ms") or 0.0)
            current_guard = float(item.get("tail_guard") or 0.18)
            if trailing < float(timing.get("min_trailing_ms") or 45.0):
                new_guard = max(current_guard, 0.28)
                if new_guard != current_guard:
                    item["tail_guard"] = new_guard
                    adaptations.append("larger_tail_guard")
                    changed = True
            elif onset > float(timing.get("max_onset_ms") or 220.0):
                new_guard = min(current_guard, 0.12)
                if new_guard != current_guard:
                    item["tail_guard"] = new_guard
                    adaptations.append("more_speech_room")
                    changed = True

        if adaptations:
            item["qa_adaptations"] = list(dict.fromkeys(adaptations))
            item["qa_policy"] = _POLICY

    if changed:
        guarded_segments.write_text(
            json.dumps(segments, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(
            f"после {completed_round}-го раунда применён безопасный fallback "
            f"к сегментам {sorted(failed_set)} без изменения слов"
        )


def _run_quality_synth_v46(
    command: Sequence[str],
    *args: Any,
    **kwargs: Any,
) -> Any:
    original_command = [str(part) for part in command]
    env = dict(kwargs.get("env") or os.environ)
    work_dir = Path(legacy._flag_value(original_command, "--work-dir")).resolve()
    timeline = Path(legacy._flag_value(original_command, "--output")).resolve()
    source_segments = Path(
        legacy._flag_value(original_command, "--segments-json")
    ).resolve()
    base_seed = int(legacy._flag_value(original_command, "--base-seed"))

    guard_dir = work_dir / "semantic_guard_v4"
    guard_dir.mkdir(parents=True, exist_ok=True)
    guarded_segments = guard_dir / "segments_guarded.json"
    segments = legacy._prepare_guarded_segments(source_segments, guarded_segments)

    renderer = Path(base.__file__).resolve().parent / base._QUALITY_RENDERER
    if not renderer.is_file():
        raise RuntimeError(f"Не найден Quality renderer: {renderer}")
    rewritten = list(original_command)
    original_renderer: Path | None = None
    for index, part in enumerate(rewritten):
        if Path(part).name.casefold() == base._SYNTH_NAME.casefold():
            original_renderer = Path(part).resolve()
            rewritten[index] = str(renderer)
            break
    if original_renderer is None:
        raise RuntimeError("Не найден исходный NoChew renderer в команде.")
    legacy._replace_flag(rewritten, "--segments-json", str(guarded_segments))

    env.pop("VOXCPM_PROMPT_TEXTS_JSON", None)
    env["VOXCPM_ORIGINAL_RENDERER"] = str(original_renderer)
    env["VOXCPM_SEMANTIC_GUARD_VERSION"] = base._GUARD_VERSION
    kwargs["env"] = env

    pipeline_signature = base._pipeline_signature(
        original_command,
        guarded_segments,
        renderer,
    )
    all_ids = {int(item["id"]) for item in segments}
    if not _resume_partial_signature(
        work_dir,
        pipeline_signature=pipeline_signature,
        all_ids=all_ids,
    ):
        base._invalidate_stale_checkpoints(work_dir, pipeline_signature)

    configured_rounds = int(os.getenv("DUB_TTS_QA_MAX_ROUNDS", "5") or "5")
    max_rounds = max(3, min(7, configured_rounds))
    last_report: dict[str, Any] = {}
    last_report_path: Path | None = None
    calmer_profile = _calmer_reference_profile(original_command)

    for round_index in range(max_rounds):
        round_seed = base_seed + round_index * 100_000
        legacy._replace_flag(rewritten, "--base-seed", str(round_seed))
        log(
            f"QA round {round_index + 1}/{max_rounds}; seed={round_seed}; "
            f"calmer_reference={calmer_profile}"
        )
        result = base._REAL_SUBPROCESS.run(rewritten, *args, **kwargs)
        if int(getattr(result, "returncode", 1)) != 0:
            return result

        last_report_path = timeline.with_suffix(
            f".semantic_qa_v46.round{round_index + 1}.json"
        )
        failed, last_report = base.verify_timeline_v4(
            timeline,
            segments,
            last_report_path,
        )
        if not failed:
            _write_marker(
                work_dir,
                state="complete",
                base_seed=round_seed,
                failed_ids=[],
                pipeline_signature=pipeline_signature,
                report_path=last_report_path,
                round_index=round_index + 1,
            )
            timeline.with_suffix(".semantic_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log("все реплики прошли Professional Audio QA")
            return result

        next_seed = base_seed + (round_index + 1) * 100_000
        base._retarget(
            work_dir,
            good_ids=all_ids - set(failed),
            failed_ids=failed,
            new_base_seed=next_seed,
        )
        _adapt_persistent_failures(
            guarded_segments,
            segments,
            failed,
            last_report,
            completed_round=round_index + 1,
            calmer_profile=calmer_profile,
        )
        _write_marker(
            work_dir,
            state="partial",
            base_seed=next_seed,
            failed_ids=failed,
            pipeline_signature=pipeline_signature,
            report_path=last_report_path,
            round_index=round_index + 1,
        )
        log(
            f"не прошли {failed}; хорошие checkpoints сохранены; "
            "повторяются только эти реплики"
        )

    final_report_path = timeline.with_suffix(".semantic_qa.json")
    final_report_path.write_text(
        json.dumps(last_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    details = _failure_summary(last_report)
    failed_ids = last_report.get("failed_segment_ids", [])
    raise RuntimeError(
        "Professional Audio QA не принял отдельные реплики после "
        f"{max_rounds} прицельных раундов. Сегменты: {failed_ids}. "
        f"Причины: {details}. Отчёт: {final_report_path}"
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base._run_quality_synth = _run_quality_synth_v46
    repair_runtime.prepare_repair_checkpoints = _prepare_repair_checkpoints_v46
    _INSTALLED = True
    log(
        "installed partial-checkpoint resume, five focused rounds, adaptive fallback "
        "and exact failure diagnostics"
    )


__all__ = [
    "_failure_summary",
    "_prepare_repair_checkpoints_v46",
    "_recover_partial_marker",
    "install",
]
