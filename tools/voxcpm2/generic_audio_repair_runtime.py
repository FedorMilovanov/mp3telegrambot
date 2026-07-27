#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild selected Dub Studio audio segments without translation or title work."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from services.dub_studio import utc_now
from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import semantic_tts_guard as legacy_guard
from tools.voxcpm2 import semantic_tts_guard_v4

_ACTION = "repair_audio"


def log(message: str) -> None:
    production.log(message)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Не найден {label}: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} должен содержать JSON-объект.")
    return payload


def _load_segments(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(f"Не найден список реплик: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или повреждён.")
    result = [dict(item) for item in payload if isinstance(item, dict)]
    ids = [int(item.get("id") or 0) for item in result]
    if len(result) != len(payload) or any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise RuntimeError("segments_ru_final.json содержит некорректные ID.")
    return sorted(result, key=lambda item: int(item["id"]))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_marker(work_dir: Path) -> dict[str, Any]:
    path = work_dir / "semantic_guard.marker.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_guard_version() -> str:
    return str(getattr(semantic_tts_guard_v4, "_GUARD_VERSION", "semantic-tts-guard-v4.2"))


def _delete_segment_files(work_dir: Path, segment_ids: set[int]) -> None:
    for segment_id in segment_ids:
        (work_dir / "checkpoints" / f"segment_{segment_id:02d}.json").unlink(missing_ok=True)
        for directory in ("segments_clean", "segments_fitted", "attempts"):
            root = work_dir / directory
            if not root.is_dir():
                continue
            for path in root.glob(f"{segment_id:02d}_*"):
                path.unlink(missing_ok=True)


def prepare_repair_checkpoints(
    work_dir: Path,
    *,
    all_ids: set[int],
    selected_ids: set[int],
    new_base_seed: int,
    repair_all: bool,
) -> None:
    """Keep verified checkpoints for unselected segments and invalidate repairs."""
    if repair_all:
        _delete_segment_files(work_dir, all_ids)
        (work_dir / "semantic_guard.marker.json").unlink(missing_ok=True)
        return

    marker = _read_marker(work_dir)
    if marker.get("guard_version") != _current_guard_version():
        raise RuntimeError(
            "Выборочный ремонт возможен только после полного Quality v4.2 рендера. "
            "Сначала выполните /dubfix PROJECT_ID all."
        )
    legacy_guard._retarget_checkpoints(
        work_dir,
        good_ids=all_ids - selected_ids,
        failed_ids=selected_ids,
        new_base_seed=int(new_base_seed),
    )
    _delete_segment_files(work_dir, selected_ids)


def _source_cues(root: Path) -> list[pipeline.Cue]:
    groups_path = root / "source_groups.json"
    if groups_path.is_file():
        payload = json.loads(groups_path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            cues = [
                pipeline.Cue(
                    float(item["start"]),
                    float(item["end"]),
                    str(item.get("source") or item.get("english") or "").strip(),
                )
                for item in payload
                if isinstance(item, dict)
                and float(item.get("end") or 0.0) > float(item.get("start") or 0.0)
                and str(item.get("source") or item.get("english") or "").strip()
            ]
            if cues:
                return cues
    raise RuntimeError("Не найден source_groups.json для безопасной пересборки voice reference.")


def _rebuild_references(root: Path, source: Path, duration: float) -> tuple[Path, Path]:
    cues = _source_cues(root)
    extended_intervals, composite_intervals = pipeline.reference_intervals(cues, duration)
    reference_dir = root / "references"
    reference_dir.mkdir(parents=True, exist_ok=True)
    extended = reference_dir / "extended_reference.wav"
    composite = reference_dir / "composite_reference.wav"
    dub_quality_v4.build_reference_v4(
        source,
        extended_intervals,
        extended,
        target_seconds=min(16.0, max(12.0, duration * 0.45)),
    )
    dub_quality_v4.build_reference_v4(
        source,
        composite_intervals,
        composite,
        target_seconds=min(16.0, max(10.0, duration * 0.38)),
    )
    return extended, composite


def _existing_references(root: Path) -> tuple[Path, Path]:
    extended = root / "references" / "extended_reference.wav"
    composite = root / "references" / "composite_reference.wav"
    if not extended.is_file() or not composite.is_file():
        raise RuntimeError("Не найдены голосовые референсы; используйте полный аудиоремонт all.")
    return extended, composite


def _repair_seed(request: dict[str, Any], marker: dict[str, Any], manifest: dict[str, Any]) -> int:
    initial = int(request.get("base_seed") or 2026072800)
    previous = int(marker.get("base_seed") or initial)
    history = manifest.get("audio_repairs")
    repair_index = len(history) + 1 if isinstance(history, list) else 1
    return max(initial, previous) + max(1, repair_index) * 100_000


def _refresh_named_outputs(manifest: dict[str, Any], stable_mixed: Path, stable_russian: Path) -> None:
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    pairs = (
        (outputs.get("mixed"), stable_mixed),
        (outputs.get("russian_only"), stable_russian),
    )
    for raw_destination, source in pairs:
        if not raw_destination:
            continue
        destination = Path(str(raw_destination)).expanduser().resolve()
        if destination == source.resolve():
            continue
        production._hardlink_or_copy(source, destination)


def _update_manifest(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    selected_ids: list[int],
    repair_all: bool,
    base_seed: int,
    report_path: Path,
) -> None:
    history = manifest.get("audio_repairs")
    repairs = list(history) if isinstance(history, list) else []
    repairs.append(
        {
            "action": _ACTION,
            "repaired_at": utc_now(),
            "repair_all": bool(repair_all),
            "segment_ids": selected_ids,
            "base_seed": int(base_seed),
            "quality_guard": _current_guard_version(),
            "report": str(report_path),
            "translation_reused": True,
            "gemini_called": False,
        }
    )
    manifest["phase"] = "completed"
    manifest["audio_quality_guard"] = _current_guard_version()
    manifest["audio_repairs"] = repairs[-30:]
    production.save_json(manifest_path, manifest)


def main() -> None:
    pipeline.configure_utf8()
    project_id = production.current_project_id()
    root = production.project_root(project_id)
    request = production.load_request(root)
    repair_path = root / "input" / "audio_repair.json"
    repair = _load_object(repair_path, "audio_repair.json")
    if int(repair.get("schema_version") or 0) != 1 or str(repair.get("project_id")) != project_id:
        raise RuntimeError("Некорректный запрос аудиоремонта.")

    segments_path = root / "segments_ru_final.json"
    segments = _load_segments(segments_path)
    if str(repair.get("segments_sha256") or "") != _sha256(segments_path):
        raise RuntimeError("Реплики проекта изменились после команды /dubfix; создайте запрос заново.")
    all_ids = {int(item["id"]) for item in segments}
    selected_ids = sorted({int(value) for value in repair.get("segment_ids") or []})
    selected_set = set(selected_ids)
    if not selected_set or not selected_set.issubset(all_ids):
        raise RuntimeError("Запрос аудиоремонта содержит неизвестные реплики.")
    repair_all = bool(repair.get("repair_all"))
    if repair_all != (selected_set == all_ids):
        raise RuntimeError("Флаг repair_all не соответствует выбранным репликам.")

    source = root / "source" / "source.mp4"
    if not source.is_file():
        raise RuntimeError("Не найден локальный source.mp4 проекта.")
    output_dir = root / "output"
    manifest_path = output_dir / "manifest.json"
    manifest = _load_object(manifest_path, "manifest.json")
    duration = pipeline.ffprobe_duration(source)

    segment_work = root / "segment_work"
    master_work = root / "master_work"
    audio_dir = root / "audio"
    for directory in (segment_work, master_work, audio_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    marker = _read_marker(segment_work)
    base_seed = _repair_seed(request, marker, manifest)
    prepare_repair_checkpoints(
        segment_work,
        all_ids=all_ids,
        selected_ids=selected_set,
        new_base_seed=base_seed,
        repair_all=repair_all,
    )

    if repair_all:
        log("=== AUDIO REPAIR: REBUILD QUALITY REFERENCES ===")
        extended_reference, composite_reference = _rebuild_references(root, source, duration)
    else:
        extended_reference, composite_reference = _existing_references(root)

    cpu_venv = Path(str(request.get("cpu_venv") or r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"))
    cpu_python = cpu_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not cpu_python.is_file():
        raise RuntimeError(f"CPU Python не найден: {cpu_python}")
    repo = Path(__file__).resolve().parents[2]
    synth_script = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq" / "voxcpm2_cpu_shorts_production.py"
    master_script = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq" / "master_constant_mix.py"
    if not synth_script.is_file() or not master_script.is_file():
        raise RuntimeError("Production NoChew renderer/master не найдены.")

    mode = str(request.get("translation_mode") or "")
    if mode == "direct":
        legacy_guard.sanitize_tts_text = lambda value: str(value or "").strip()
    semantic_tts_guard_v4.install()

    threads = max(1, int(request.get("threads") or 10))
    steps = max(1, int(request.get("steps") or 16))
    cfg = float(request.get("cfg") or 1.8)
    original_level = float(request.get("original_level") or 0.18)
    video_id = str(request["video_id"])
    russian_timeline = audio_dir / f"{video_id}_ru_timeline.wav"
    stable_mixed = output_dir / "final_upload.mp4"
    stable_russian = output_dir / "russian_only.mp4"

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

    log("=== AUDIO REPAIR: VOXCPM2 QUALITY ===")
    log(f"Проект={project_id}; selected={selected_ids}; all={repair_all}; seed={base_seed}")
    synth = [
        str(cpu_python),
        str(synth_script),
        "--archive-root",
        str(Path(str(request.get("vox_archive") or r"C:\AI-Archive\VoxCPM2-paused-RTX3060")).resolve()),
        "--extended-reference",
        str(extended_reference),
        "--composite-reference",
        str(composite_reference),
        "--segments-json",
        str(segments_path),
        "--work-dir",
        str(segment_work),
        "--output",
        str(russian_timeline),
        "--threads",
        str(threads),
        "--steps",
        str(steps),
        "--cfg",
        str(cfg),
        "--cache-length",
        "4096",
        "--video-duration",
        f"{duration:.6f}",
        "--base-seed",
        str(base_seed),
    ]
    result = production.subprocess.run(synth, cwd=str(repo), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Аудиоремонт VoxCPM2 завершился с кодом {result.returncode}.")

    log("=== AUDIO REPAIR: QUALITY MASTER ===")
    master = [
        str(cpu_python),
        str(master_script),
        "--source-video",
        str(source),
        "--russian-wav",
        str(russian_timeline),
        "--work-dir",
        str(master_work),
        "--mixed-video",
        str(stable_mixed),
        "--russian-only-video",
        str(stable_russian),
        "--original-level",
        f"{original_level:.6f}",
        "--target-i",
        "-14.0",
        "--target-lra",
        "9.0",
        "--target-tp",
        "-1.0",
    ]
    result = production.subprocess.run(master, cwd=str(repo), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Аудиоремонт master завершился с кодом {result.returncode}.")

    _refresh_named_outputs(manifest, stable_mixed, stable_russian)
    report_path = output_dir / "audio_repair_report.json"
    production.save_json(
        report_path,
        {
            "schema_version": 1,
            "project_id": project_id,
            "repair_all": repair_all,
            "segment_ids": selected_ids,
            "base_seed": base_seed,
            "guard_version": _current_guard_version(),
            "translation_reused": True,
            "gemini_called": False,
            "russian_timeline": str(russian_timeline),
            "mixed_video": str(stable_mixed),
            "russian_only_video": str(stable_russian),
        },
    )
    _update_manifest(
        manifest_path,
        manifest,
        selected_ids=selected_ids,
        repair_all=repair_all,
        base_seed=base_seed,
        report_path=report_path,
    )
    last_request = repair_path.with_name("audio_repair.last.json")
    last_request.unlink(missing_ok=True)
    shutil.move(str(repair_path), str(last_request))
    log("=== AUDIO REPAIR COMPLETED WITHOUT GEMINI ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"ОШИБКА AUDIO REPAIR: {exc}", file=os.sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
