#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ready-SRT entrypoint for the clean direct Dub production path."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.dub_title_policy import install_voxcpm_title_policy
from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import generic_direct_runtime as production


_DIRECT_MARKER_POLICY = "direct-cli-runtime-marker-v1"
_FAILURE_REPORT = "direct_renderer_failure.json"


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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _seed_resumable_clean_marker(root: Path, request: dict[str, Any]) -> None:
    """Let the core preserve partial checkpoints proven compatible by direct CLI."""
    segment_work = root / "segment_work"
    checkpoints = segment_work / "checkpoints"
    if not any(checkpoints.glob("segment_*.json")):
        return

    direct_marker_path = segment_work / "direct_cli_runtime.marker.json"
    direct_marker = _read_json(direct_marker_path)
    if not direct_marker:
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
    if direct_marker != expected_direct:
        return

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
    segment_work.mkdir(parents=True, exist_ok=True)
    (segment_work / "clean_production.marker.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
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
