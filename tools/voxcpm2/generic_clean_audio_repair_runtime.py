#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean direct audio repair without translation or renderer wrappers."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

from services.dub_studio import utc_now
from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import clean_segment_normalizer
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import direct_max_quality_io
from tools.voxcpm2 import expressive_continuity
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


def _checkpoint_payload(work_dir: Path, segment_id: int) -> dict[str, Any]:
    path = work_dir / "checkpoints" / f"segment_{segment_id:02d}.json"
    if not path.is_file() or path.stat().st_size <= 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _finite_report_value(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _checkpoint_ready(
    work_dir: Path,
    segment_id: int,
) -> tuple[bool, str]:
    payload = _checkpoint_payload(work_dir, segment_id)
    signature = payload.get("signature") if isinstance(payload, dict) else None
    report = payload.get("report") if isinstance(payload, dict) else None
    if not isinstance(signature, dict) or not isinstance(report, dict):
        return False, "checkpoint JSON неполон"
    if str(signature.get("policy") or "") != direct_max_quality_io.POLICY:
        return False, "устаревший renderer-policy"
    if str(report.get("renderer_policy") or "") != direct_max_quality_io.POLICY:
        return False, "report renderer-policy отсутствует или устарел"
    if int(report.get("id") or 0) != segment_id:
        return False, "report id не совпадает"
    profile = str(signature.get("reference_profile") or "")
    if profile not in {"extended", "composite"}:
        return False, "reference_profile отсутствует"
    selected_voice = report.get("selected_voice_match")
    if not isinstance(selected_voice, dict) or not all(
        _finite_report_value(selected_voice.get(key))
        for key in ("f0_median_ratio", "f0_p90_ratio", "spectral_similarity")
    ):
        return False, "selected voice evidence неполон"
    fit = report.get("fit")
    if not isinstance(fit, dict) or not _finite_report_value(fit.get("fitted_duration")):
        return False, "fit report неполон"
    fitted = work_dir / "segments_fitted" / f"{segment_id:02d}_{profile}_fitted.wav"
    if not fitted.is_file() or fitted.stat().st_size <= 0:
        return False, "fitted WAV отсутствует или пуст"
    return True, direct_max_quality_io.POLICY


def _renderer_baseline_ready(
    work_dir: Path,
    segment_ids: set[int],
) -> tuple[bool, str]:
    failures: list[str] = []
    for segment_id in sorted(segment_ids):
        ready, detail = _checkpoint_ready(work_dir, segment_id)
        if not ready:
            failures.append(f"#{segment_id}: {detail}")
    if failures:
        return False, "; ".join(failures[:12])
    return True, direct_max_quality_io.POLICY


def _request_value(request: dict[str, Any], key: str, default: Any) -> Any:
    return default if key not in request or request[key] is None else request[key]


def _current_fingerprints(request: dict[str, Any]) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    archive_value = str(
        _request_value(
            request,
            "vox_archive",
            r"C:\AI-Archive\VoxCPM2-paused-RTX3060",
        )
    ).strip()
    if not archive_value:
        raise RuntimeError("vox_archive пуст в запросе аудиоремонта.")
    return clean_runtime_contract.build_fingerprints(
        repo=repo,
        archive=Path(archive_value).resolve(),
        cpu_python=clean._cpu_python(request),
    )


def _fingerprinted_baseline_ready(
    marker: dict[str, Any],
    fingerprints: dict[str, Any],
) -> tuple[bool, str]:
    expected_render = str(fingerprints.get("render_contract_sha256") or "")
    expected_release = str(fingerprints.get("release_contract_sha256") or "")
    if marker.get("policy") != clean.POLICY:
        return False, f"production-policy={marker.get('policy') or 'missing'}"
    if marker.get("runtime_contract_policy") != clean_runtime_contract.POLICY:
        return False, "runtime contract marker отсутствует или устарел"
    if str(marker.get("render_contract_sha256") or "") != expected_render:
        return False, "renderer/model/voxcpm fingerprint изменился"
    if str(marker.get("release_contract_sha256") or "") != expected_release:
        return False, "QA/master/reference release fingerprint изменился"
    if marker.get("segment_qa_passed") is not True:
        return False, "segment QA baseline не принят"
    if marker.get("release_complete") is not True:
        return False, "final AAC release baseline не завершён"
    return True, "fingerprinted release-complete clean expressive baseline"


def _next_seed(
    request: dict[str, Any],
    marker: dict[str, Any],
    manifest: dict[str, Any],
) -> int:
    try:
        initial = int(_request_value(request, "base_seed", 2026072800))
        previous = int(_request_value(marker, "base_seed", initial))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Некорректный base_seed для аудиоремонта.") from exc
    if not 0 <= initial <= clean_runtime_contract.MAX_BASE_SEED:
        raise RuntimeError("Исходный repair base_seed выходит за безопасный диапазон.")
    if not 0 <= previous <= clean_runtime_contract.MAX_BASE_SEED:
        raise RuntimeError("Marker base_seed выходит за безопасный диапазон.")
    history = manifest.get("audio_repairs")
    repair_index = len(history) + 1 if isinstance(history, list) else 1
    candidate = max(initial, previous) + max(1, repair_index) * clean_runtime_contract.RETRY_SEED_OFFSET
    if not 0 <= candidate <= clean_runtime_contract.MAX_BASE_SEED:
        raise RuntimeError("Следующий repair seed выходит за безопасный диапазон.")
    return candidate


def _existing_references(root: Path) -> tuple[Path, Path]:
    extended = root / "references" / "extended_reference.wav"
    composite = root / "references" / "composite_reference.wav"
    for path in (extended, composite):
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(
                "Не найдены чистые voice references. Выполните /dubfix PROJECT_ID all."
            )
        report = path.with_suffix(".selection.json")
        if not report.is_file() or report.stat().st_size <= 0:
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
    marker: dict[str, Any],
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
            "runtime_contract_policy": clean_runtime_contract.POLICY,
            "render_contract_sha256": marker.get("render_contract_sha256"),
            "release_contract_sha256": marker.get("release_contract_sha256"),
            "release_complete": marker.get("release_complete") is True,
            "renderer_policy": direct_max_quality_io.POLICY,
            "reference_policy": continuous_reference_policy.POLICY,
            "expression_policy": expressive_continuity.POLICY,
            "report": str(report_path),
            "translation_reused": True,
            "gemini_called": False,
        }
    )
    manifest["phase"] = "completed"
    manifest["audio_quality_guard"] = clean.POLICY
    manifest["runtime_contract_policy"] = clean_runtime_contract.POLICY
    manifest["render_contract_sha256"] = marker.get("render_contract_sha256")
    manifest["release_contract_sha256"] = marker.get("release_contract_sha256")
    manifest["release_complete"] = marker.get("release_complete") is True
    manifest["audio_production"] = "direct-powershell-equivalent"
    manifest["renderer_policy"] = direct_max_quality_io.POLICY
    manifest["reference_policy"] = continuous_reference_policy.POLICY
    manifest["expression_policy"] = expressive_continuity.POLICY
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
    if int(repair.get("schema_version") or 0) != 1 or str(repair.get("project_id")) != project_id:
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

    if repair_all_requested:
        clean_segment_normalizer.normalize(root, request, duration=duration)
        repair, segments = _reload_repair_and_segments(repair_path, segments_path)
        manifest = legacy_repair._load_object(manifest_path, "manifest.json")

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
        extended, composite = continuous_reference_policy.build_calm_references(
            source=source,
            cues=cues,
            duration=duration,
            reference_dir=root / "references",
        )
    else:
        expression_ready = bool(segments) and all(
            str(item.get("expression_policy") or "") == expressive_continuity.POLICY
            for item in segments
        )
        fingerprints = _current_fingerprints(request)
        fingerprint_ready, fingerprint_detail = _fingerprinted_baseline_ready(
            marker,
            fingerprints,
        )
        renderer_ready, renderer_detail = _renderer_baseline_ready(segment_work, all_ids)
        if not expression_ready or not renderer_ready or not fingerprint_ready:
            raise RuntimeError(
                "Выборочный ремонт разрешён только после успешного clean expressive "
                f"baseline renderer {direct_max_quality_io.POLICY}. "
                f"Fingerprint: {fingerprint_detail}. Renderer: {renderer_detail}. "
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

    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_path,
        duration=duration,
        report_path=output_dir / "expressive_continuity.json",
    )
    expressive_built = False
    reference_detail = "existing controlled/calm reference reused"
    if repair_all:
        expressive_built, reference_detail = controlled_reference_gate.build_or_keep_calm(
            source=source,
            segments=planned,
            output=composite,
            identity_reference=extended,
        )
    production.log(
        "source-guided emotional arc prepared; translation reused verbatim; "
        + reference_detail
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

    final_marker = _clean_marker(segment_work)
    if (
        final_marker.get("policy") != clean.POLICY
        or not final_marker.get("render_contract_sha256")
        or not final_marker.get("release_contract_sha256")
        or final_marker.get("release_complete") is not True
    ):
        raise RuntimeError("После ремонта не создан release-complete fingerprinted marker.")

    legacy_repair._refresh_named_outputs(manifest, stable_mixed, stable_russian)
    report_path = output_dir / "audio_repair_report.json"
    production.save_json(
        report_path,
        {
            "schema_version": 8,
            "project_id": project_id,
            "repair_all": repair_all,
            "segment_ids": selected_ids,
            "base_seed": seed,
            "production_policy": clean.POLICY,
            "runtime_contract_policy": clean_runtime_contract.POLICY,
            "render_contract_sha256": final_marker.get("render_contract_sha256"),
            "release_contract_sha256": final_marker.get("release_contract_sha256"),
            "release_complete": True,
            "renderer_policy": direct_max_quality_io.POLICY,
            "reference_policy": continuous_reference_policy.POLICY,
            "segment_policy": clean_segment_normalizer.POLICY,
            "expression_policy": expressive_continuity.POLICY,
            "renderer_mode": "direct-powershell-equivalent",
            "translation_reused": True,
            "gemini_called": False,
            "russian_timeline": str(timeline),
            "mixed_video": str(stable_mixed),
            "russian_only_video": str(stable_russian),
            "qa_report": str(timeline.with_suffix(".clean_qa.json")),
            "final_media_qa": str(root / "master_work" / "final_media_verification.json"),
            "expression_report": str(output_dir / "expressive_continuity.json"),
            "controlled_expressive_reference": bool(expressive_built or not repair_all),
            "reference_detail": reference_detail,
        },
    )
    _update_manifest(
        manifest_path,
        manifest,
        selected_ids=selected_ids,
        repair_all=repair_all,
        seed=seed,
        report_path=report_path,
        marker=final_marker,
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
