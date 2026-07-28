#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean production core for Dub Studio.

The bot and a manual PowerShell launch use the same NoChew renderer and master.
This module never replaces subprocess and never patches VoxCPM internals. It
prepares short phrases and calm references, launches the renderer directly, and
runs an independent post-render QA gate.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_qa_v45
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import semantic_tts_guard_v4

POLICY = "clean-direct-production-v1"
TARGET_SECONDS = 4.2
MAX_SECONDS = 5.4
MASTER_I = -16.0
MASTER_LRA = 8.0
MASTER_TP = -1.5


def log(message: str) -> None:
    print(f"[CLEAN-DUB] {message}", flush=True)


def group_source_cues(cues: list[Any]) -> list[dict[str, Any]]:
    groups = dub_quality_v4.group_cues_v4(
        cues,
        target_seconds=TARGET_SECONDS,
        max_seconds=MAX_SECONDS,
    )
    _validate_groups(groups, "english")
    return groups


def group_ready_srt(cues: list[Any]) -> list[dict[str, Any]]:
    groups = dub_quality_v4.group_ready_srt_v4(cues, max_seconds=MAX_SECONDS)
    _validate_groups(groups, "source")
    return groups


def _validate_groups(groups: list[dict[str, Any]], text_key: str) -> None:
    if not groups:
        raise RuntimeError("После сегментации не осталось речевых блоков.")
    previous_end = 0.0
    for index, item in enumerate(groups, start=1):
        start = float(item["start"])
        end = float(item["end"])
        text = re.sub(r"\s+", " ", str(item.get(text_key) or "")).strip()
        if not text or end <= start:
            raise RuntimeError(f"Некорректный речевой блок #{index}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Речевые блоки пересекаются около #{index}.")
        if end - start > MAX_SECONDS + 0.30:
            raise RuntimeError(
                f"Речевой блок #{index} слишком длинный: {end - start:.3f} сек."
            )
        previous_end = end


def build_render_segments(
    groups: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    segments, subtitles = professional_audio_v45.build_render_segments_v45(
        groups,
        translations,
        delay_ms=delay_ms,
        duration=duration,
    )
    _mark_and_validate_segments(segments, duration)
    return segments, subtitles


def build_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    segments, subtitles = professional_audio_v45.build_direct_segments_v45(
        groups,
        delay_ms=delay_ms,
        duration=duration,
    )
    _mark_and_validate_segments(segments, duration)
    return segments, subtitles


def _mark_and_validate_segments(
    segments: list[dict[str, Any]],
    duration: float,
) -> None:
    if not segments:
        raise RuntimeError("Список реплик перед VoxCPM пуст.")
    previous_end = 0.0
    for item in segments:
        item["production_policy"] = POLICY
        segment_id = int(item["id"])
        start = float(item["start"])
        end = float(item["end"])
        delay = max(0, int(item.get("start_delay_ms", 0))) / 1000.0
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text or end <= start:
            raise RuntimeError(f"Некорректная реплика #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Реплика #{segment_id} пересекается с предыдущей.")
        if end + delay > float(duration) + 0.02:
            raise RuntimeError(f"Реплика #{segment_id} выходит за конец видео.")
        if end - start > MAX_SECONDS + 0.30:
            raise RuntimeError(
                f"Реплика #{segment_id} слишком длинная: {end - start:.3f} сек."
            )
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        rate = words / max(0.35, end - start)
        if rate > 6.2:
            raise RuntimeError(
                f"Реплика #{segment_id} физически перегружена: {rate:.2f} слова/с."
            )
        previous_end = end


def build_calm_references(
    *,
    source: Path,
    cues: list[pipeline.Cue],
    duration: float,
    reference_dir: Path,
) -> tuple[Path, Path]:
    """Choose calm, continuous source windows and save transparent reports."""
    reference_dir.mkdir(parents=True, exist_ok=True)
    extended = reference_dir / "extended_reference.wav"
    composite = reference_dir / "composite_reference.wav"
    extended_intervals, composite_intervals = pipeline.reference_intervals(cues, duration)
    professional_audio_v45.build_reference_v45(
        source,
        extended_intervals,
        extended,
        target_seconds=9.0,
    )
    professional_audio_v45.build_reference_v45(
        source,
        composite_intervals,
        composite,
        target_seconds=8.0,
    )
    _validate_reference_report(extended.with_suffix(".selection.json"), "extended")
    _validate_reference_report(composite.with_suffix(".selection.json"), "composite")
    return extended, composite


def _number(item: dict[str, Any], key: str, default: float) -> float:
    value = item.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _validate_reference_report(path: Path, profile: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"Не создан отчёт отбора {profile} voice reference.")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    selected = payload.get("selected") if isinstance(payload, dict) else None
    if not isinstance(selected, list) or not selected:
        raise RuntimeError(f"Не выбраны пригодные фрагменты для {profile} voice reference.")
    usable = [
        item
        for item in selected
        if isinstance(item, dict)
        and _number(item, "voiced_ratio", 0.0) >= 0.16
        and _number(item, "active_ratio", 0.0) >= 0.25
        and _number(item, "max_internal_gap", 99.0) <= 0.85
    ]
    if not usable:
        raise RuntimeError(
            f"Все выбранные фрагменты {profile} voice reference слишком шумные, "
            "обрывочные или почти без устойчивой речи."
        )


def _renderer_paths(repo: Path) -> tuple[Path, Path]:
    example = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq"
    renderer = example / "voxcpm2_cpu_shorts_production.py"
    master = example / "master_constant_mix.py"
    if not renderer.is_file() or not master.is_file():
        raise RuntimeError("Прямой NoChew renderer/master не найдены.")
    return renderer, master


def _cpu_python(request: dict[str, Any]) -> Path:
    venv = Path(str(request.get("cpu_venv") or r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"))
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise RuntimeError(f"CPU Python не найден: {python}")
    return python


def _environment(threads: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": "-1",
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "OMP_NUM_THREADS": str(threads),
            "MKL_NUM_THREADS": str(threads),
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    for key in (
        "VOXCPM_ORIGINAL_RENDERER",
        "VOXCPM_RESCUE_RENDERER",
        "VOXCPM_PROMPT_TEXTS_JSON",
        "VOXCPM_SEMANTIC_GUARD_VERSION",
    ):
        env.pop(key, None)
    return env


def _failure_summary(report: dict[str, Any]) -> str:
    result: list[str] = []
    for item in report.get("segments", []):
        if not isinstance(item, dict) or item.get("passed", True):
            continue
        segment_id = item.get("id")
        parts: list[str] = []
        semantic = item.get("semantic")
        if isinstance(semantic, dict) and not semantic.get("passed", True):
            heard = str(semantic.get("heard") or "").replace("\n", " ")[:80]
            parts.append(
                f"ASR recall={semantic.get('token_recall')}, услышано=«{heard}»"
            )
        timing = item.get("timing")
        if isinstance(timing, dict) and not timing.get("passed", True):
            parts.append(
                f"стык onset={timing.get('onset_ms')}ms tail={timing.get('trailing_ms')}ms"
            )
        continuity = item.get("continuity_v45")
        if isinstance(continuity, dict) and not continuity.get("passed", True):
            parts.append(f"пауза={continuity.get('max_internal_gap')}s")
        voice = item.get("voice_match_v45")
        if isinstance(voice, dict) and not voice.get("passed", True):
            parts.append(
                f"голос median×{voice.get('f0_median_ratio')} p90×{voice.get('f0_p90_ratio')}"
            )
        result.append(f"#{segment_id}: " + ", ".join(parts or ["QA failure"]))
    return "; ".join(result)


def _read_clean_marker(work_dir: Path) -> dict[str, Any]:
    path = work_dir / "clean_production.marker.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _remove_render_state(work_dir: Path) -> None:
    for directory_name in ("checkpoints", "segments_clean", "segments_fitted", "attempts"):
        target = work_dir / directory_name
        if target.is_dir():
            shutil.rmtree(target)
    for marker in ("semantic_guard.marker.json", "clean_production.marker.json"):
        (work_dir / marker).unlink(missing_ok=True)


def render_and_master(
    *,
    root: Path,
    request: dict[str, Any],
    source: Path,
    duration: float,
    segments_json: Path,
    extended_reference: Path,
    composite_reference: Path,
    final_mixed: Path,
    final_russian: Path,
    force_fresh: bool = False,
) -> Path:
    """Run the PowerShell-equivalent renderer directly, then independent QA."""
    repo = Path(__file__).resolve().parents[2]
    renderer, master = _renderer_paths(repo)
    cpu_python = _cpu_python(request)
    segment_work = root / "segment_work"
    master_work = root / "master_work"
    audio_dir = root / "audio"
    for directory in (segment_work, master_work, audio_dir, root / "output"):
        directory.mkdir(parents=True, exist_ok=True)

    segments = json.loads(segments_json.read_text(encoding="utf-8-sig"))
    if not isinstance(segments, list):
        raise RuntimeError("segments_ru_final.json повреждён.")
    _mark_and_validate_segments(segments, duration)
    segments_json.write_text(
        json.dumps(segments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    existing_checkpoints = any((segment_work / "checkpoints").glob("segment_*.json"))
    marker = _read_clean_marker(segment_work)
    if existing_checkpoints and marker.get("policy") != POLICY:
        log("обнаружены checkpoints старой цепочки; выполняю безопасный fresh render")
        force_fresh = True
    if force_fresh:
        _remove_render_state(segment_work)

    threads = max(1, int(request.get("threads") or 10))
    steps = max(1, int(request.get("steps") or 16))
    cfg = float(request.get("cfg") or 1.8)
    seed = int(request.get("base_seed") or 2026072800)
    original_level = float(request.get("original_level") or 0.18)
    archive = Path(
        str(request.get("vox_archive") or r"C:\AI-Archive\VoxCPM2-paused-RTX3060")
    ).resolve()
    timeline = audio_dir / f"{request['video_id']}_ru_timeline.wav"
    env = _environment(threads)
    all_ids = {int(item["id"]) for item in segments}
    last_report: dict[str, Any] = {}

    for round_index in range(2):
        round_seed = seed + round_index * 100_000
        command = [
            str(cpu_python),
            str(renderer),
            "--archive-root", str(archive),
            "--extended-reference", str(extended_reference),
            "--composite-reference", str(composite_reference),
            "--segments-json", str(segments_json),
            "--work-dir", str(segment_work),
            "--output", str(timeline),
            "--threads", str(threads),
            "--steps", str(steps),
            "--cfg", str(cfg),
            "--cache-length", "4096",
            "--video-duration", f"{duration:.6f}",
            "--base-seed", str(round_seed),
        ]
        log(
            f"direct NoChew round {round_index + 1}/2; seed={round_seed}; "
            "без renderer wrappers"
        )
        result = subprocess.run(command, cwd=str(repo), env=env, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Прямой VoxCPM2 renderer завершился с кодом {result.returncode}."
            )
        report_path = timeline.with_suffix(
            f".clean_qa.round{round_index + 1}.json"
        )
        failed, last_report = professional_audio_qa_v45.verify_timeline_v45(
            timeline,
            segments,
            report_path,
        )
        if not failed:
            timeline.with_suffix(".clean_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (segment_work / "clean_production.marker.json").write_text(
                json.dumps(
                    {
                        "policy": POLICY,
                        "base_seed": round_seed,
                        "renderer": str(renderer),
                        "failed_segment_ids": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            break
        if round_index == 0:
            log(f"QA отклонил {failed}; один прямой повтор только этих ID")
            semantic_tts_guard_v4._retarget(
                segment_work,
                good_ids=all_ids - set(failed),
                failed_ids=failed,
                new_base_seed=seed + 100_000,
            )
            continue
        timeline.with_suffix(".clean_qa.json").write_text(
            json.dumps(last_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise RuntimeError(
            "Чистый direct renderer не прошёл независимый QA после одного "
            f"прицельного повтора. Сегменты: {failed}. "
            f"Причины: {_failure_summary(last_report)}"
        )

    master_command = [
        str(cpu_python),
        str(master),
        "--source-video", str(source),
        "--russian-wav", str(timeline),
        "--work-dir", str(master_work),
        "--mixed-video", str(final_mixed),
        "--russian-only-video", str(final_russian),
        "--original-level", f"{original_level:.6f}",
        "--target-i", f"{MASTER_I:.1f}",
        "--target-lra", f"{MASTER_LRA:.1f}",
        "--target-tp", f"{MASTER_TP:.1f}",
    ]
    result = subprocess.run(master_command, cwd=str(repo), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Прямой master завершился с кодом {result.returncode}.")

    (root / "output" / "clean_production_report.json").write_text(
        json.dumps(
            {
                "policy": POLICY,
                "renderer": str(renderer),
                "master": str(master),
                "wrapper_count": 0,
                "target_loudness_lufs": MASTER_I,
                "true_peak_dbtp": MASTER_TP,
                "reference_reports": [
                    str(extended_reference.with_suffix(".selection.json")),
                    str(composite_reference.with_suffix(".selection.json")),
                ],
                "qa_report": str(timeline.with_suffix(".clean_qa.json")),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return timeline


__all__ = [
    "POLICY",
    "build_calm_references",
    "build_direct_segments",
    "build_render_segments",
    "group_ready_srt",
    "group_source_cues",
    "render_and_master",
]
