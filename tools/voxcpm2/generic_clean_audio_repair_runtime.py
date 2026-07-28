#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean direct audio repair without translation or renderer wrappers."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from services.dub_studio import utc_now
from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_segment_normalizer
from tools.voxcpm2 import generic_audio_repair_runtime as legacy_repair
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import legacy_segment_migration_v45
from tools.voxcpm2 import semantic_tts_guard_v4

_ACTION = "repair_audio"


def _clean_marker(work_dir: Path) -> dict[str, Any]:
    path = work_dir / "clean_production.marker.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _next_seed(
    request: dict[str, Any],
    marker: dict[str, Any],
    manifest: dict[str, Any],
) -> int:
    initial = int(request.get("base_seed") or 2026072800)
    previous = int(marker.get("base_seed") or initial)
    history = manifest.get("audio_repairs")
    repair_index = len(history) + 1 if isinstance(history, list) else 1
    return max(initial, previous) + max(1, repair_index) * 100_000


def _existing_references(root: Path) -> tuple[Path, Path]:
    extended = root / "references" / "extended_reference.wav"
    composite = root / "references" / "composite_reference.wav"
    for path in (extended, composite):
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(
                "Не найдены чистые voice references. Выполните /dubfix PROJECT_ID all."
            )
        report = path.with_suffix(".selection.json")
        if not report.is_file():
            raise RuntimeError(
                "У voice reference нет отчёта чистого отбора. "
                "Выполните полный ремонт all."
            )
    return extended, composite


def _update_manifest(
    path: Path,
    manifest: dict[str, Any],
    *,
    selected_ids: list[int],
    repair_all: bool,
    seed: int,
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
            "base_seed": int(seed),
            "production_policy": clean.POLICY,
            "report": str(report_path),
            "translation_reused": True,
            "gemini_called": False,
        }
    )
    manifest["phase"] = "completed"
    manifest["audio_quality_guard"] = clean.POLICY
    manifest["audio_production"] = "direct-powershell-equivalent"
    manifest["audio_repairs"] = repairs[-30:]
    production.save_json(path, manifest)


def _reload_repair_and_segments(
    repair_path: Path,
    segments_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repair = legacy_repair._load_object(repair_path, "audio_repair.json")
    segments = legacy_repair._load_segments(segments_path)
    if str(repair.get("segments_sha256") or "") != legacy_repair._sha256(segments_path):
        raise RuntimeError(
            "Реплики проекта изменились после команды /dubfix; создайте запрос заново."
        )
    return repair, segments


def main() -> None:
    pipeline.configure_utf8()
    project_id = production.current_project_id()
    root = production.project_root(project_id)
    request = production.load_request(root)
    repair_path = root / "input" / "audio_repair.json"
    repair = legacy_repair._load_object(repair_path, "audio_repair.json")
    if (
        int(repair.get("schema_version") or 0) != 1
        or str(repair.get("project_id")) != project_id
    ):
        raise RuntimeError("Некорректный запрос аудиоремонта.")

    repair_all_requested = bool(repair.get("repair_all"))
    if repair_all_requested:
        legacy_segment_migration_v45.migrate(root, request)

    segments_path = root / "segments_ru_final.json"
    repair, segments = _reload_repair_and_segments(repair_path, segments_path)

    source = root / "source" / "source.mp4"
    if not source.is_file():
        raise RuntimeError("Не найден локальный source.mp4 проекта.")
    output_dir = root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest = legacy_repair._load_object(manifest_path, "manifest.json")
    duration = pipeline.ffprobe_duration(source)

    # Normalize only a full historical repair. The helper updates both the
    # segment JSON and audio_repair hash while preserving the exact Russian token
    # stream. A selective repair never edits text or timing.
    if repair_all_requested:
        clean_segment_normalizer.normalize(root, request, duration=duration)
        repair, segments = _reload_repair_and_segments(repair_path, segments_path)

    all_ids = {int(item["id"]) for item in segments}
    selected_ids = sorted({int(value) for value in repair.get("segment_ids") or []})
    selected_set = set(selected_ids)
    if not selected_set or not selected_set.issubset(all_ids):
        raise RuntimeError("Запрос аудиоремонта содержит неизвестные реплики.")
    repair_all = bool(repair.get("repair_all"))
    if repair_all != (selected_set == all_ids):
        raise RuntimeError("Флаг repair_all не соответствует выбранным репликам.")

    segment_work = root / "segment_work"
    marker = _clean_marker(segment_work)
    seed = _next_seed(request, marker, manifest)

    if repair_all:
        production.log("=== CLEAN AUDIO REPAIR: FRESH REFERENCES + FRESH RENDER ===")
        cues = legacy_repair._source_cues(root)
        extended, composite = clean.build_calm_references(
            source=source,
            cues=cues,
            duration=duration,
            reference_dir=root / "references",
        )
    else:
        if marker.get("policy") != clean.POLICY:
            raise RuntimeError(
                "Выборочный ремонт разрешён только после успешного чистого baseline. "
                "Сначала выполните /dubfix PROJECT_ID all."
            )
        extended, composite = _existing_references(root)
        semantic_tts_guard_v4._retarget(
            segment_work,
            good_ids=all_ids - selected_set,
            failed_ids=selected_set,
            new_base_seed=seed,
        )
        legacy_repair._delete_segment_files(segment_work, selected_set)
        production.log(
            f"=== CLEAN AUDIO REPAIR: ONLY {selected_ids}; DIRECT NEW SEED {seed} ==="
        )

    runtime_request = dict(request)
    runtime_request["base_seed"] = seed
    stable_mixed = output_dir / "final_upload.mp4"
    stable_russian = output_dir / "russian_only.mp4"
    timeline = clean.render_and_master(
        root=root,
        request=runtime_request,
        source=source,
        duration=duration,
        segments_json=segments_path,
        extended_reference=extended,
        composite_reference=composite,
        final_mixed=stable_mixed,
        final_russian=stable_russian,
        force_fresh=repair_all,
    )

    legacy_repair._refresh_named_outputs(manifest, stable_mixed, stable_russian)
    report_path = output_dir / "audio_repair_report.json"
    production.save_json(
        report_path,
        {
            "schema_version": 2,
            "project_id": project_id,
            "repair_all": repair_all,
            "segment_ids": selected_ids,
            "base_seed": seed,
            "production_policy": clean.POLICY,
            "segment_policy": clean_segment_normalizer.POLICY,
            "renderer_mode": "direct-powershell-equivalent",
            "translation_reused": True,
            "gemini_called": False,
            "russian_timeline": str(timeline),
            "mixed_video": str(stable_mixed),
            "russian_only_video": str(stable_russian),
            "qa_report": str(timeline.with_suffix(".clean_qa.json")),
        },
    )
    _update_manifest(
        manifest_path,
        manifest,
        selected_ids=selected_ids,
        repair_all=repair_all,
        seed=seed,
        report_path=report_path,
    )

    last_request = repair_path.with_name("audio_repair.last.json")
    last_request.unlink(missing_ok=True)
    shutil.move(str(repair_path), str(last_request))
    production.log("=== CLEAN AUDIO REPAIR COMPLETED WITHOUT GEMINI OR WRAPPERS ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(f"ОШИБКА CLEAN AUDIO REPAIR: {exc}", file=__import__("sys").stderr)
        traceback.print_exc()
        raise SystemExit(1)
