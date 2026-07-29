#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ready-SRT entrypoint for the clean direct Dub production path."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from services.dub_title_policy import install_voxcpm_title_policy
from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import direct_max_quality_io as direct_io
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import generic_direct_runtime as production


_DIRECT_MARKER_POLICY = "direct-cli-runtime-marker-v1"
_FAILURE_REPORT = "direct_renderer_failure.json"
_LEGACY_RESUME_POLICY = "validated-late-prefix-after-tempo-policy-v1"
_OLD_PREFERRED_MAX_TEMPO = 1.35


def _install_clean_runtime_adapters() -> None:
    """Keep hardened download/title routing, but never install a TTS guard."""
    hardened = production.hardened
    hardened.download_source = clean_source_download.download_source
    hardened.pipeline.download_source = clean_source_download.download_source
    hardened.pipeline.download_captions = hardened.download_captions
    hardened.pipeline.gemini_json = hardened.gemini_json
    install_voxcpm_title_policy(hardened)
    hardened._install_project_title_standard()
    production.log(
        "clean adapters: verified yt-dlp source + canonical title route; "
        "TTS guard disabled"
    )


def _current_request() -> tuple[Path, dict[str, Any]]:
    root = production.project_root(production.current_project_id())
    return root, production.load_request(root)


def _build_clean_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[Any]]:
    _root, request = _current_request()
    return clean.build_direct_segments(
        groups,
        delay_ms=clean_request_settings.russian_delay_ms(request),
        duration=duration,
    )


def _read_json_value(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = _read_json_value(path)
    return payload if isinstance(payload, dict) else {}


def _same_number(left: Any, right: Any, *, tolerance: float = 1e-6) -> bool:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance


def _expected_expression(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": str(segment.get("expression_policy") or ""),
        "tier": str(segment.get("expression_tier") or ""),
        "score": segment.get("expression_score"),
        "style_instruction": str(segment.get("style_instruction") or ""),
        "source_prosody": segment.get("source_prosody") or {},
    }


def _legacy_checkpoint_prefix(
    root: Path,
    request: dict[str, Any],
) -> list[int]:
    """Validate the old successful prefix; the renderer rechecks full signatures.

    Job #15 was produced before the early direct marker existed and failed only on
    the final segment's 1.358/1.35 tempo boundary. The old wrapper therefore left
    valid segment checkpoints but no runtime marker. We only adopt a contiguous
    quality-passed prefix whose user text, timing, expression and render settings
    still match the current project. The inner renderer remains authoritative and
    will regenerate any item whose model/reference hashes do not match exactly.
    """
    segments_payload = _read_json_value(root / "segments_ru_final.json")
    if not isinstance(segments_payload, list) or len(segments_payload) < 2:
        return []
    segments = {
        int(item.get("id")): item
        for item in segments_payload
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }
    if len(segments) != len(segments_payload):
        return []

    segment_work = root / "segment_work"
    checkpoint_dir = segment_work / "checkpoints"
    fitted_dir = segment_work / "segments_fitted"
    checkpoint_paths = sorted(checkpoint_dir.glob("segment_*.json"))
    if not checkpoint_paths:
        return []

    steps = int(request["steps"]) if request.get("steps") is not None else 16
    cfg = float(request["cfg"]) if request.get("cfg") is not None else 1.8
    base_seed = (
        int(request["base_seed"])
        if request.get("base_seed") is not None
        else 2026072800
    )
    accepted_ids: list[int] = []

    for path in checkpoint_paths:
        payload = _read_json(path)
        signature = payload.get("signature")
        report = payload.get("report")
        if not isinstance(signature, dict) or not isinstance(report, dict):
            return []
        try:
            segment_id = int(report.get("id"))
        except (TypeError, ValueError, OverflowError):
            return []
        segment = segments.get(segment_id)
        if not isinstance(segment, dict):
            return []
        profile = str(segment.get("reference_profile") or "")
        fitted = fitted_dir / f"{segment_id:02d}_{profile}_fitted.wav"
        if not fitted.is_file() or fitted.stat().st_size < 4096:
            return []

        fit = report.get("fit")
        if (
            report.get("renderer_policy") != direct_io.POLICY
            or report.get("selected_raw_pitch_evidence_ok") is not True
            or not isinstance(fit, dict)
            or not _same_number(report.get("start"), segment.get("start"))
            or not _same_number(report.get("end"), segment.get("end"))
            or not _same_number(report.get("tail_guard"), segment.get("tail_guard"))
            or float(fit.get("tempo") or 999.0) > _OLD_PREFERRED_MAX_TEMPO + 1e-6
        ):
            return []

        expected_core = {
            "policy": direct_io.POLICY,
            "text": str(segment.get("text") or ""),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "tail_guard": float(segment["tail_guard"]),
            "start_delay_ms": int(segment.get("start_delay_ms", 0)),
            "reference_profile": profile,
            "expression": _expected_expression(segment),
            "steps": steps,
            "cfg": cfg,
            "base_seed": base_seed,
        }
        for key, expected in expected_core.items():
            actual = signature.get(key)
            if isinstance(expected, float):
                if not _same_number(actual, expected):
                    return []
            elif actual != expected:
                return []
        if not str(signature.get("model_config_sha256") or ""):
            return []
        if not str(signature.get("reference_sha256") or ""):
            return []
        accepted_ids.append(segment_id)

    accepted_ids = sorted(set(accepted_ids))
    if not accepted_ids:
        return []
    if accepted_ids != list(range(1, accepted_ids[-1] + 1)):
        return []
    if accepted_ids[-1] >= len(segments_payload):
        return []
    return accepted_ids


def _seed_resumable_clean_marker(root: Path, request: dict[str, Any]) -> None:
    """Let the core preserve partial checkpoints proven compatible by direct CLI."""
    segment_work = root / "segment_work"
    checkpoints = segment_work / "checkpoints"
    if not any(checkpoints.glob("segment_*.json")):
        return

    repo = Path(__file__).resolve().parents[2]
    cpu_python = clean._cpu_python(request)
    archive = Path(
        str(request.get("vox_archive") or r"C:\AI-Archive\VoxCPM2-paused-RTX3060")
    ).resolve()
    fingerprints = clean.clean_runtime_contract.build_fingerprints(
        repo=repo,
        archive=archive,
        cpu_python=cpu_python,
    )
    expected_direct = {
        "schema_version": 1,
        "policy": _DIRECT_MARKER_POLICY,
        "render_contract_sha256": fingerprints["render_contract_sha256"],
        "cache_length": 4096,
        "python_executable": str(cpu_python.resolve()),
    }

    direct_marker_path = segment_work / "direct_cli_runtime.marker.json"
    direct_marker = _read_json(direct_marker_path)
    migration_ids: list[int] = []
    if direct_marker != expected_direct:
        migration_ids = _legacy_checkpoint_prefix(root, request)
        if not migration_ids:
            return
        direct_marker_path.write_text(
            json.dumps(expected_direct, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

    marker = {
        "schema_version": 3,
        "policy": clean.POLICY,
        "runtime_contract_policy": clean.clean_runtime_contract.POLICY,
        "render_contract_sha256": fingerprints["render_contract_sha256"],
        "release_contract_sha256": fingerprints["release_contract_sha256"],
        "segment_qa_passed": False,
        "release_complete": False,
        "checkpoint_resume_provisional": True,
    }
    if migration_ids:
        marker.update(
            checkpoint_resume_migration=_LEGACY_RESUME_POLICY,
            adopted_checkpoint_ids=migration_ids,
        )
    segment_work.mkdir(parents=True, exist_ok=True)
    (segment_work / "clean_production.marker.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if migration_ids:
        production.log(
            "восстановлен проверенный поздний checkpoint-prefix: "
            f"1–{migration_ids[-1]}; renderer перепроверит полные signatures"
        )
    else:
        production.log("совместимые fingerprinted checkpoints сохранены для продолжения")


def _renderer_failure_detail(root: Path) -> str:
    payload = _read_json(root / "segment_work" / _FAILURE_REPORT)
    message = str(payload.get("message") or "").strip()
    error_type = str(payload.get("error_type") or "RuntimeError").strip()
    if not message:
        return ""
    return f"{error_type}: {message}"


def _run_clean_voxcpm_and_master(
    *,
    root: Path,
    request: dict[str, Any],
    source: Path,
    cues: list[Any],
    duration: float,
    segments_json: Path,
    final_mixed: Path,
    final_russian: Path,
) -> Path:
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source,
        cues=cues,
        duration=duration,
        reference_dir=root / "references",
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _expressive_built, reference_detail = controlled_reference_gate.build_or_keep_calm(
        source=source,
        segments=planned,
        output=composite,
        identity_reference=extended,
    )
    production.log(
        "source-guided emotional arc prepared; user SRT text preserved; "
        + reference_detail
    )
    _seed_resumable_clean_marker(root, request)
    try:
        return clean.render_and_master(
            root=root,
            request=request,
            source=source,
            duration=duration,
            segments_json=segments_json,
            extended_reference=extended,
            composite_reference=composite,
            final_mixed=final_mixed,
            final_russian=final_russian,
            # Fingerprints and per-segment signatures already invalidate stale work.
            # Keeping this False makes a late failed segment resumable.
            force_fresh=False,
        )
    except RuntimeError as exc:
        detail = _renderer_failure_detail(root)
        if detail and "завершился с кодом" in str(exc):
            raise RuntimeError(f"Прямой VoxCPM2 renderer: {detail}") from exc
        raise


def main() -> None:
    production.hardened.install_runtime_adapters = _install_clean_runtime_adapters
    production.group_srt_cues = clean.group_ready_srt
    production._build_direct_segments = _build_clean_direct_segments
    production._run_voxcpm_and_master = _run_clean_voxcpm_and_master
    production.main()
    root, request = _current_request()
    clean_request_settings.repair_manifest(root, request)


if __name__ == "__main__":
    main()
