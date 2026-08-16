#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean production core for Dub Studio.

The bot and manual PowerShell launch share the same direct renderer and master.
No subprocess proxy or VoxCPM monkeypatch is installed. Durable checkpoints are
accepted only under a fingerprint of the actual renderer modules, selected model
snapshot and backend runtime. A baseline becomes release-complete only after the
final encoded AAC files pass media QA.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_qa_v45
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import semantic_tts_guard_v4
from services.speech_backends import DEFAULT_BACKEND_ID, get_backend

POLICY = "clean-direct-production-v2"
TARGET_SECONDS = 4.2
MAX_SECONDS = 5.4
MASTER_I = -16.0
MASTER_LRA = 8.0
MASTER_TP = -1.5


def log(message: str) -> None:
    print(f"[CLEAN-DUB] {message}", flush=True)


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result


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
        start = _finite(item.get("start"), field=f"group[{index}].start")
        end = _finite(item.get("end"), field=f"group[{index}].end")
        text = re.sub(r"\s+", " ", str(item.get(text_key) or "")).strip()
        if start < 0.0 or not text or end <= start:
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
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration должен быть > 0.")
    if not segments:
        raise RuntimeError("Список реплик перед speech backend пуст.")
    previous_end = 0.0
    previous_effective_end = 0.0
    seen_ids: set[int] = set()
    for item in segments:
        item["production_policy"] = POLICY
        segment_id = int(item["id"])
        if segment_id <= 0 or segment_id in seen_ids:
            raise RuntimeError(f"Некорректный или повторный ID реплики: {segment_id}.")
        seen_ids.add(segment_id)
        start = _finite(item.get("start"), field=f"segment[{segment_id}].start")
        end = _finite(item.get("end"), field=f"segment[{segment_id}].end")
        try:
            delay_ms = int(item.get("start_delay_ms", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"Некорректный delay реплики #{segment_id}.") from exc
        if not 0 <= delay_ms <= 1500:
            raise RuntimeError(f"Delay реплики #{segment_id} вне диапазона 0..1500 ms.")
        delay = delay_ms / 1000.0
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if start < 0.0 or not text or end <= start:
            raise RuntimeError(f"Некорректная реплика #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Реплика #{segment_id} пересекается с предыдущей.")
        effective_start = start + delay
        effective_end = end + delay
        if effective_start < previous_effective_end - 0.001:
            raise RuntimeError(
                f"Реплика #{segment_id} пересекается после применения delay."
            )
        if effective_end > duration_value + 0.02:
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
        previous_effective_end = effective_end


def build_calm_references(
    *,
    source: Path,
    cues: list[pipeline.Cue],
    duration: float,
    reference_dir: Path,
) -> tuple[Path, Path]:
    """Legacy public helper; production entrypoints use continuous-reference v2."""
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
    try:
        value = float(item.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


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


def _renderer_paths(
    repo: Path,
    request: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Resolve engine-owned entrypoints without hard-coding a TTS model in core."""
    payload = dict(request or {})
    payload.setdefault("speech_backend", DEFAULT_BACKEND_ID)
    backend = get_backend(payload["speech_backend"])
    runtime = backend.runtime_paths(Path(repo), payload)
    renderer = runtime.renderer_entrypoint
    master = runtime.master_entrypoint
    if not renderer.is_file() or not master.is_file():
        raise RuntimeError(
            "Speech backend renderer/master не найдены: "
            f"backend={runtime.backend_id}; renderer={renderer}; master={master}"
        )
    return renderer, master


def _cpu_python(request: dict[str, Any]) -> Path:
    """Resolve the selected backend interpreter through its runtime adapter."""
    backend = get_backend(request.get("speech_backend") or DEFAULT_BACKEND_ID)
    runtime = backend.runtime_paths(Path(__file__).resolve().parents[2], request)
    python = runtime.cpu_python
    if not python.is_file():
        raise RuntimeError(
            f"CPU Python не найден для backend={backend.backend_id}: {python}"
        )
    return python


def _environment(
    threads: int,
    *,
    backend_id: object = DEFAULT_BACKEND_ID,
) -> dict[str, str]:
    """Compatibility helper delegating process policy to the selected backend."""
    backend = get_backend(backend_id)
    return backend.process_environment(
        {"threads": threads},
        base_environment=os.environ,
    ).as_dict(os.environ)


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
            if semantic.get("numeric_anchors_passed") is False:
                parts.append("не совпало числовое/датовое значение")
            parts.append(f"ASR recall={semantic.get('token_recall')}, услышано=«{heard}»")
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


def _write_clean_marker(work_dir: Path, payload: dict[str, Any]) -> None:
    (work_dir / "clean_production.marker.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


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
    """Run the direct renderer, independent QA and final verified master."""
    repo = Path(__file__).resolve().parents[2]
    settings = clean_runtime_contract.normalize_settings(request, duration=duration)
    backend = get_backend(settings.get("speech_backend") or DEFAULT_BACKEND_ID)
    runtime = backend.runtime_paths(repo, settings)
    renderer = runtime.renderer_entrypoint
    master = runtime.master_entrypoint
    cpu_python = runtime.cpu_python
    archive = runtime.archive_root
    for label, path in (
        ("source", source),
        ("segments", segments_json),
        ("extended reference", extended_reference),
        ("composite reference", composite_reference),
    ):
        if not path.is_file():
            raise RuntimeError(f"Не найден {label}: {path}")
    fingerprints = clean_runtime_contract.build_fingerprints(
        repo=repo,
        archive=archive,
        cpu_python=cpu_python,
        backend_id=backend.backend_id,
    )

    segment_work = root / "segment_work"
    master_work = root / "master_work"
    audio_dir = root / "audio"
    for directory in (segment_work, master_work, audio_dir, root / "output"):
        directory.mkdir(parents=True, exist_ok=True)

    segments = json.loads(segments_json.read_text(encoding="utf-8-sig"))
    if not isinstance(segments, list):
        raise RuntimeError("segments_ru_final.json повреждён.")
    _mark_and_validate_segments(segments, settings["duration"])
    segments_json.write_text(
        json.dumps(segments, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    existing_checkpoints = any((segment_work / "checkpoints").glob("segment_*.json"))
    marker = _read_clean_marker(segment_work)
    marker_current = bool(
        marker.get("policy") == POLICY
        and marker.get("runtime_contract_policy") == clean_runtime_contract.POLICY
        and marker.get("render_contract_sha256")
        == fingerprints["render_contract_sha256"]
    )
    if existing_checkpoints and not marker_current:
        log(
            "checkpoints не соответствуют renderer/model/runtime fingerprint; "
            "выполняю безопасный fresh render"
        )
        force_fresh = True
    if force_fresh:
        _remove_render_state(segment_work)

    threads = int(settings["threads"])
    steps = int(settings["steps"])
    cfg = float(settings["cfg"])
    seed = int(settings["base_seed"])
    original_level = float(settings["original_level"])
    timeline = audio_dir / f"{settings['video_id']}_ru_timeline.wav"
    env = backend.process_environment(
        {"threads": threads, "speech_backend": backend.backend_id},
        base_environment=os.environ,
    ).as_dict(os.environ)
    all_ids = {int(item["id"]) for item in segments}
    last_report: dict[str, Any] = {}
    accepted_seed: int | None = None

    for round_index in range(2):
        round_seed = seed + round_index * clean_runtime_contract.RETRY_SEED_OFFSET
        command = backend.build_renderer_command(
            runtime,
            values={
                "extended_reference": str(extended_reference),
                "composite_reference": str(composite_reference),
                "segments_json": str(segments_json),
                "segment_work": str(segment_work),
                "timeline": str(timeline),
                "threads": str(threads),
                "steps": str(steps),
                "cfg": str(cfg),
                "cache_length": "4096",
                "duration": f"{settings['duration']:.6f}",
                "base_seed": str(round_seed),
            },
        )
        log(
            f"direct NoChew round {round_index + 1}/2; seed={round_seed}; "
            "без renderer wrappers"
        )
        result = subprocess.run(command, cwd=str(repo), env=env, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Прямой speech backend renderer завершился с кодом {result.returncode}."
            )
        report_path = timeline.with_suffix(f".clean_qa.round{round_index + 1}.json")
        failed, last_report = professional_audio_qa_v45.verify_timeline_v45(
            timeline,
            segments,
            report_path,
        )
        if not failed:
            accepted_seed = round_seed
            timeline.with_suffix(".clean_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            _write_clean_marker(
                segment_work,
                {
                    "schema_version": 3,
                    "policy": POLICY,
                    "runtime_contract_policy": clean_runtime_contract.POLICY,
                    "render_contract_sha256": fingerprints["render_contract_sha256"],
                    "release_contract_sha256": fingerprints["release_contract_sha256"],
                    "base_seed": round_seed,
                    "renderer": str(renderer),
                    "failed_segment_ids": [],
                    "qa_policy": last_report.get("professional_audio_policy"),
                    "numeric_semantic_policy": last_report.get("numeric_semantic_policy"),
                    "segment_qa_passed": True,
                    "release_complete": False,
                },
            )
            break
        if round_index == 0:
            log(f"QA отклонил {failed}; один прямой повтор только этих ID")
            semantic_tts_guard_v4._retarget(
                segment_work,
                good_ids=all_ids - set(failed),
                failed_ids=failed,
                new_base_seed=seed + clean_runtime_contract.RETRY_SEED_OFFSET,
            )
            continue
        timeline.with_suffix(".clean_qa.json").write_text(
            json.dumps(last_report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        raise RuntimeError(
            "Чистый direct renderer не прошёл независимый QA после одного "
            f"прицельного повтора. Сегменты: {failed}. "
            f"Причины: {_failure_summary(last_report)}"
        )

    if accepted_seed is None:
        raise RuntimeError("Segment QA не создал принятого baseline.")

    master_command = backend.build_master_command(
        runtime,
        values={
            "source": str(source),
            "timeline": str(timeline),
            "master_work": str(master_work),
            "final_mixed": str(final_mixed),
            "final_russian": str(final_russian),
            "original_level": f"{original_level:.6f}",
            "target_i": f"{MASTER_I:.1f}",
            "target_lra": f"{MASTER_LRA:.1f}",
            "target_tp": f"{MASTER_TP:.1f}",
        },
    )
    result = subprocess.run(master_command, cwd=str(repo), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Прямой speech backend master завершился с кодом {result.returncode}."
        )

    final_media_qa = master_work / "final_media_verification.json"
    if not final_media_qa.is_file():
        raise RuntimeError("Master не создал final_media_verification.json.")
    try:
        final_verification = json.loads(final_media_qa.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Не читается final_media_verification.json.") from exc
    if not isinstance(final_verification, dict) or final_verification.get("passed") is not True:
        raise RuntimeError("Final encoded AAC media QA не принят.")
    release_marker = _read_clean_marker(segment_work)
    if (
        release_marker.get("render_contract_sha256") != fingerprints["render_contract_sha256"]
        or release_marker.get("release_contract_sha256")
        != fingerprints["release_contract_sha256"]
        or release_marker.get("segment_qa_passed") is not True
    ):
        raise RuntimeError("Segment marker изменился до завершения master.")
    release_marker.update(
        release_complete=True,
        base_seed=accepted_seed,
        final_media_qa=str(final_media_qa),
        mixed_video=str(final_mixed),
        russian_only_video=str(final_russian),
    )
    _write_clean_marker(segment_work, release_marker)

    (root / "output" / "clean_production_report.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "policy": POLICY,
                "runtime_contract_policy": clean_runtime_contract.POLICY,
                "render_contract_sha256": fingerprints["render_contract_sha256"],
                "release_contract_sha256": fingerprints["release_contract_sha256"],
                "release_complete": True,
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
                "final_media_qa": str(final_media_qa),
                "fingerprints": fingerprints,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
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

_BASE_ALL = tuple(globals().get('__all__', ()))

import json

import math

import os

import re

import subprocess as _stdlib_subprocess

from pathlib import Path

from typing import Any

from tools.voxcpm2 import final_encoded_delivery_qa

from tools.voxcpm2 import semantic_block_runtime

_REPO_ROOT = Path(__file__).resolve().parents[3]

_legacy_build_direct_segments = build_direct_segments

CHILD_PYTHON_POLICY = "repo-root-pythonpath-master-stderr-and-post-aac-v2"

DELIVERY_RETRY_POLICY = "bounded-checkpointed-delivery-retry-v1"

MAX_AUTOMATIC_DELIVERY_RETRIES = 3

MASTER_ENTRYPOINT_NAMES = frozenset({"master_constant_mix.py", "master_monolithic_mix.py"})

_LAST_CHILD_STDERR = ""

POLICY = POLICY

MAX_SECONDS = MAX_SECONDS

SEMANTIC_BLOCK_MAX_SECONDS = semantic_block_runtime.MAX_BLOCK_SECONDS

log = log

_RETRYABLE_DELIVERY_MARKERS = (
    "следующий повтор использует seed epoch",
    "переведена на новый seed epoch",
    "переведен на новый seed epoch",
    "seed epochs",
    "hard-quality кандидат",
    "linked_phrase_gap",
    "late_broadband_burst",
    "late_broadband_tail",
    "assembled_delivery:",
    "post_aac_delivery:",
    "ending/tail qa",
)

_NON_RETRYABLE_INFRASTRUCTURE_MARKERS = (
    "modulenotfounderror",
    "filenotfounderror",
    "permissionerror",
    "preflight",
    "fingerprint",
    "не найден ffmpeg",
    "не найдены в path",
    "не найден cpu python",
    "не найден source",
    "не найден segments",
    "не найден voice reference",
    "http 403",
    "http 404",
)

def _child_python_env(value: Any) -> dict[str, str]:
    """Return an isolated environment with the repository import root first."""
    if value is None:
        env = dict(os.environ)
    elif isinstance(value, dict):
        env = {str(key): str(item) for key, item in value.items()}
    else:
        raise RuntimeError("subprocess env должен быть словарём или None.")

    repo = str(_REPO_ROOT)
    existing = str(env.get("PYTHONPATH") or "")
    parts = [item for item in existing.split(os.pathsep) if item]
    normalized = {os.path.normcase(os.path.abspath(item)) for item in parts}
    if os.path.normcase(os.path.abspath(repo)) not in normalized:
        parts.insert(0, repo)
    else:
        parts = [repo] + [
            item
            for item in parts
            if os.path.normcase(os.path.abspath(item))
            != os.path.normcase(os.path.abspath(repo))
        ]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env

def _is_python_script_command(command: Any) -> bool:
    if not isinstance(command, (list, tuple)) or len(command) < 2:
        return False
    # A Linux CI process must still recognize the Windows commands production
    # will run. ``Path`` only treats separators from the host OS as separators.
    executable = re.split(r"[\\/]", str(command[0]))[-1].casefold()
    script = re.split(r"[\\/]", str(command[1]))[-1].casefold()
    return executable.startswith("python") and script.endswith(".py")

def _is_master_command(command: Any) -> bool:
    return bool(
        _is_python_script_command(command)
        and re.split(r"[\\/]", str(command[1]))[-1].casefold()
        in MASTER_ENTRYPOINT_NAMES
    )

def _is_master_release_command(command: Any) -> bool:
    """Distinguish a real production master from --help/import smoke tests."""
    if not _is_master_command(command) or not isinstance(command, (list, tuple)):
        return False
    values = [str(item) for item in command]
    if "--help" in values or "-h" in values:
        return False
    for flag in ("--work-dir", "--russian-only-video"):
        if flag not in values:
            return False
        index = values.index(flag)
        if index + 1 >= len(values) or not values[index + 1].strip():
            return False
    return True

def _command_flag(command: Any, flag: str) -> str:
    if not isinstance(command, (list, tuple)):
        raise RuntimeError("Master command должен быть списком аргументов.")
    values = [str(item) for item in command]
    try:
        index = values.index(flag)
    except ValueError as exc:
        raise RuntimeError(f"Master command не содержит {flag}.") from exc
    if index + 1 >= len(values) or not values[index + 1].strip():
        raise RuntimeError(f"Master command не содержит значение после {flag}.")
    return values[index + 1]

def _verify_post_aac_master_output(command: Any) -> dict[str, Any]:
    russian_only = Path(_command_flag(command, "--russian-only-video")).resolve()
    work_dir = Path(_command_flag(command, "--work-dir")).resolve()
    output_dir = russian_only.parent
    if output_dir.name.casefold() != "output":
        raise RuntimeError(
            "Russian-only MP4 должен находиться в стандартной project/output папке."
        )
    project_root = output_dir.parent
    return final_encoded_delivery_qa.verify_final_encoded_russian(
        russian_only_video=russian_only,
        segments_path=project_root / "segments_ru_final.json",
        report_path=work_dir / "final_encoded_delivery_qa.json",
        final_media_report_path=work_dir / "final_media_verification.json",
    )

def _run_child_process(command: Any, *args: Any, **kwargs: Any):
    """Run child commands with deterministic imports and fail-closed release QA."""
    global _LAST_CHILD_STDERR

    is_python = _is_python_script_command(command)
    is_master = _is_master_command(command)
    is_master_release = _is_master_release_command(command)
    if is_python:
        kwargs["env"] = _child_python_env(kwargs.get("env"))
    if is_master and kwargs.get("stderr") is None:
        kwargs["stderr"] = _stdlib_subprocess.PIPE
        kwargs.setdefault("text", True)
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")

    result = _stdlib_subprocess.run(command, *args, **kwargs)
    if is_master and int(getattr(result, "returncode", 0) or 0) != 0:
        detail = str(getattr(result, "stderr", "") or "").strip()
        if detail:
            detail = detail[-12000:]
        else:
            detail = f"process exited with code {result.returncode} without stderr"
        _LAST_CHILD_STDERR = detail
        raise RuntimeError("Прямой master завершился с точной причиной:\n" + detail)
    if is_master_release:
        try:
            _verify_post_aac_master_output(command)
        except Exception as exc:
            _LAST_CHILD_STDERR = str(exc)
            raise RuntimeError(
                "Прямой master создал файлы, но post-AAC ending/tail QA их отклонил:\n"
                + str(exc)
            ) from exc
    return result

class _SubprocessProxy:
    """Module-like proxy scoped to the legacy clean-core module only."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_stdlib_subprocess, name)

    @staticmethod
    def run(command: Any, *args: Any, **kwargs: Any):
        return _run_child_process(command, *args, **kwargs)

def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result

def _strict_int(
    value: Any,
    *,
    field: str,
    low: int,
    high: int,
) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} не может быть bool.")
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        raise RuntimeError(f"{field} должен быть целым числом.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{field}={result} вне диапазона {low}..{high}.")
    return result

def _mark_and_validate_segments(
    segments: list[dict[str, Any]],
    duration: float,
) -> None:
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration должен быть > 0.")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Список реплик перед VoxCPM пуст или повреждён.")

    previous_end = 0.0
    previous_effective_end = 0.0
    seen_ids: set[int] = set()
    for position, item in enumerate(segments, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"segment[{position}] должен быть JSON-объектом, "
                f"получено {type(item).__name__}."
            )
        segment_id = _strict_int(
            item.get("id"),
            field=f"segment[{position}].id",
            low=1,
            high=2**31 - 1,
        )
        if segment_id in seen_ids:
            raise RuntimeError(f"Повторный ID реплики: {segment_id}.")
        seen_ids.add(segment_id)
        item["id"] = segment_id
        item["production_policy"] = POLICY

        start = _finite(item.get("start"), field=f"segment[{segment_id}].start")
        end = _finite(item.get("end"), field=f"segment[{segment_id}].end")
        delay_ms = _strict_int(
            item.get("start_delay_ms", 0),
            field=f"segment[{segment_id}].start_delay_ms",
            low=0,
            high=1500,
        )
        item["start_delay_ms"] = delay_ms
        delay = delay_ms / 1000.0
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if start < 0.0 or not text or end <= start:
            raise RuntimeError(f"Некорректная реплика #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Реплика #{segment_id} пересекается с предыдущей.")
        effective_start = start + delay
        effective_end = end + delay
        if effective_start < previous_effective_end - 0.001:
            raise RuntimeError(
                f"Реплика #{segment_id} пересекается после применения delay."
            )
        if effective_end > duration_value + 0.02:
            raise RuntimeError(f"Реплика #{segment_id} выходит за конец видео.")
        segment_limit = (
            SEMANTIC_BLOCK_MAX_SECONDS
            if str(item.get("semantic_block_policy") or "") == semantic_block_runtime.POLICY
            else MAX_SECONDS
        )
        if end - start > segment_limit + 0.30:
            raise RuntimeError(
                f"Реплика #{segment_id} слишком длинная: {end - start:.3f} сек."
            )
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        rate = words / max(0.35, end - start)
        if rate > 6.2:
            raise RuntimeError(
                f"Реплика #{segment_id} физически перегружена: {rate:.2f} слова/с."
            )
        item["start"] = start
        item["end"] = end
        item["text"] = text
        previous_end = end
        previous_effective_end = effective_end

def build_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Select the direct planning policy without exposing model internals."""
    if any(str(item.get("semantic_block_policy") or "") == semantic_block_runtime.POLICY for item in groups):
        return semantic_block_runtime.build_direct_segments(
            groups,
            delay_ms=delay_ms,
            duration=duration,
        )
    return _legacy_build_direct_segments(
        groups,
        delay_ms=delay_ms,
        duration=duration,
    )

build_direct_segments = build_direct_segments

subprocess = _SubprocessProxy()

_finite = _finite

_mark_and_validate_segments = _mark_and_validate_segments

_legacy_render_and_master = render_and_master

def _retryable_delivery_failure(detail: str) -> bool:
    """Accept only failures whose quality code already invalidated a checkpoint."""
    normalized = str(detail or "").casefold().replace("ё", "е")
    if not normalized:
        return False
    if any(marker in normalized for marker in _NON_RETRYABLE_INFRASTRUCTURE_MARKERS):
        return False
    return any(marker in normalized for marker in _RETRYABLE_DELIVERY_MARKERS)

def _direct_failure_report(root: Any) -> str:
    try:
        path = Path(root).resolve() / "segment_work" / "direct_renderer_failure.json"
    except (TypeError, ValueError, OSError):
        return ""
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    message = str(payload.get("message") or "").strip()
    error_type = str(payload.get("error_type") or "RuntimeError").strip()
    return f"{error_type}: {message}" if message else ""

def _delivery_failure_detail(
    exc: RuntimeError,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Recover the deepest child cause without treating an old report as current."""
    exception_detail = str(exc).strip()
    details: list[str] = []
    child_detail = str(_LAST_CHILD_STDERR or "").strip()
    if child_detail:
        details.append(child_detail)

    # The renderer deliberately streams logs instead of buffering many minutes
    # of output. Its fresh failure JSON is authoritative only when that exact
    # child process returned non-zero; otherwise an older report must not turn
    # an infrastructure error into a quality retry.
    if "Прямой VoxCPM2 renderer завершился с кодом" in exception_detail:
        root = kwargs.get("root")
        if root is None and args:
            root = args[0]
        report_detail = _direct_failure_report(root)
        if report_detail:
            details.append(report_detail)
    if exception_detail:
        details.append(exception_detail)

    unique: list[str] = []
    for value in details:
        if value not in unique:
            unique.append(value)
    return "\n".join(unique)

def render_and_master(*args: Any, **kwargs: Any) -> Any:
    """Retry quality-only failures in-place while preserving good checkpoints."""
    global _LAST_CHILD_STDERR

    try:
        retry_limit = int(MAX_AUTOMATIC_DELIVERY_RETRIES)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("MAX_AUTOMATIC_DELIVERY_RETRIES должен быть целым.") from exc
    retry_limit = max(0, min(8, retry_limit))

    for retry_index in range(retry_limit + 1):
        _LAST_CHILD_STDERR = ""
        try:
            return _legacy_render_and_master(*args, **kwargs)
        except RuntimeError as exc:
            detail = _delivery_failure_detail(exc, args, kwargs)
            if not _retryable_delivery_failure(detail):
                if detail and detail != str(exc).strip():
                    raise RuntimeError(detail) from exc
                raise
            if retry_index >= retry_limit:
                raise RuntimeError(
                    "Автоматическое checkpoint-восстановление исчерпано "
                    f"после {retry_limit} повторов. Последняя точная причина:\n{detail}"
                ) from exc
            log(
                "quality-only failure; сохраняю успешные checkpoints и запускаю "
                f"автоматический повтор {retry_index + 1}/{retry_limit}. "
                f"Причина: {detail[:1200]}"
            )

    raise RuntimeError("Недостижимое состояние automatic delivery retry.")

__all__ = sorted(
    set(name for name in globals() if not name.startswith("__") and name != "_legacy")
    | {
        "CHILD_PYTHON_POLICY",
        "DELIVERY_RETRY_POLICY",
        "MAX_AUTOMATIC_DELIVERY_RETRIES",
        "_LAST_CHILD_STDERR",
        "_child_python_env",
        "_command_flag",
        "_delivery_failure_detail",
        "_direct_failure_report",
        "_finite",
        "_is_master_command",
        "_is_master_release_command",
        "_is_python_script_command",
        "_legacy_render_and_master",
        "_mark_and_validate_segments",
        "_retryable_delivery_failure",
        "_run_child_process",
        "_strict_int",
        "_verify_post_aac_master_output",
        "render_and_master",
    }
)
