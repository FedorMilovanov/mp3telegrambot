#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade that extends the Dub Studio health contract.

The large, proven command implementation remains in ``handlers/dub_health.py``.
This package preserves every existing check, replaces the superseded worker-v4.5
source assertion with the v4.6 preflight contract, and synchronizes the worker
runtime expected by health and the supervisor before worker autostart runs.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_health.py"
_SPEC = importlib.util.spec_from_file_location(
    "handlers._dub_health_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый Dub health: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_WORKER_RUNTIME = "dub-worker-quality-v4.6"

# handlers.dub_health is imported by install_dub_studio_runtime() before that
# function calls ensure_worker_running(). Synchronize both legacy modules here,
# so a healthy v4.6 worker is never mistaken for an old idle worker and killed.
from services import dub_studio_runtime as _supervisor  # noqa: E402

_supervisor._WORKER_RUNTIME = _WORKER_RUNTIME
_legacy._WORKER_RUNTIME = _WORKER_RUNTIME
_legacy_quality_contract = _legacy._quality_contract


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _v46_static_contract(repo: Path) -> tuple[bool, str]:
    repo = Path(repo)
    voxcpm = repo / "tools" / "voxcpm2"
    paths = {
        "worker": voxcpm / "dub_worker_hardened.py",
        "supervisor": repo / "services" / "dub_studio_runtime.py",
        "supervisor_facade": repo / "services" / "dub_studio_runtime" / "__init__.py",
        "title_facade": repo / "services" / "dub_title_policy" / "__init__.py",
        "health_facade": Path(__file__).resolve(),
        "preflight": voxcpm / "dub_job_preflight.py",
        "preflight_facade": voxcpm / "dub_job_preflight" / "__init__.py",
        "core_facade": voxcpm / "clean_production_core" / "__init__.py",
    }
    text = {name: _read(path) for name, path in paths.items()}
    ok = (
        '_RUNTIME_VERSION = "dub-worker-quality-v4.6"' in text["worker"]
        and "from tools.voxcpm2 import dub_job_preflight" in text["worker"]
        and "def _execute_job_with_preflight(" in text["worker"]
        and "worker.execute_job = _execute_job_with_preflight" in text["worker"]
        and 'POLICY = "dub-production-preflight-v1"' in text["preflight"]
        and 'POLICY = "dub-production-preflight-v2"' in text["preflight_facade"]
        and "REPORT_SCHEMA = 2" in text["preflight_facade"]
        and '"render_custom"' in text["preflight_facade"]
        and "generic_project_runtime.load_request(root)" in text["preflight_facade"]
        and "uuid.uuid4().hex" in text["preflight_facade"]
        and "os.fsync(handle.fileno())" in text["preflight_facade"]
        and "*clean_runtime_contract._RENDER_MODULES" in text["preflight_facade"]
        and "*clean_runtime_contract._RELEASE_MODULES" in text["preflight_facade"]
        and '"tools/voxcpm2/dub_job_preflight/__init__.py"' in text["preflight_facade"]
        and "recipe.work_root" in text["preflight_facade"]
        and "PREFLIGHT_HEARTBEAT_SECONDS = 5.0" in text["preflight_facade"]
        and "def _preflight_heartbeat(" in text["preflight_facade"]
        and "clean_runtime_contract._model_manifest(" in text["preflight_facade"]
        and "clean_runtime_contract._voxcpm_runtime(" in text["preflight_facade"]
        and 'status="busy"' in text["preflight_facade"]
        and '_WORKER_RUNTIME = "dub-worker-quality-v4.6"' in text["health_facade"]
        and "_supervisor._WORKER_RUNTIME = _WORKER_RUNTIME" in text["health_facade"]
        and "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME" in text["health_facade"]
        and '_WORKER_RUNTIME = "dub-worker-quality-v4.6"' in text["supervisor_facade"]
        and "class _WriteThroughModule" in text["supervisor_facade"]
        and "_module.__class__ = _WriteThroughModule" in text["supervisor_facade"]
        and "_legacy._patch_health = _patch_health" in text["title_facade"]
        and "legacy_health.collect_dub_health = wrapped" in text["title_facade"]
        and 'CHILD_PYTHON_POLICY = "repo-root-pythonpath-and-master-stderr-v1"'
        in text["core_facade"]
        and "_legacy.subprocess = _SubprocessProxy()" in text["core_facade"]
    )
    detail = (
        "worker/preflight v4.6/v2 synchronized; shared recipe root normalized; "
        "full implementation/model/runtime cache; preflight heartbeat; atomic report; "
        "deterministic child imports; supervisor/title hooks write through"
        if ok
        else "worker/preflight v4.6 compatibility contract incomplete"
    )
    return ok, detail


def _legacy_quality_without_superseded_worker(repo: Path) -> tuple[bool, str]:
    ok, detail = _legacy_quality_contract(repo)
    if ok:
        return True, detail
    prefix = "не прошли: "
    if not str(detail).startswith(prefix):
        return False, detail
    failed = [item.strip() for item in str(detail)[len(prefix):].split(",") if item.strip()]
    failed = [item for item in failed if item != "worker-v45"]
    if failed:
        return False, "не прошли: " + ", ".join(failed)
    return True, "все legacy-контракты активны; worker-v45 заменён v4.6 preflight"


def _supplemental_quality_contract(repo: Path) -> tuple[bool, str]:
    repo = Path(repo)
    voxcpm = repo / "tools" / "voxcpm2"
    paths = {
        "zero_safe_qa": voxcpm / "final_media_qa" / "__init__.py",
        "repair_facade": voxcpm / "generic_clean_audio_repair_runtime" / "__init__.py",
        "repair_main": voxcpm / "generic_clean_audio_repair_runtime" / "__main__.py",
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "runtime_facade": voxcpm / "clean_runtime_contract" / "__init__.py",
        "core_facade": voxcpm / "clean_production_core" / "__init__.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "normalizer": voxcpm / "clean_segment_normalizer.py",
        "migration": voxcpm / "legacy_segment_migration_v45.py",
        "source_download": voxcpm / "clean_source_download.py",
        "source_facade": voxcpm / "clean_source_download" / "__init__.py",
        "project_runtime": voxcpm / "generic_project_runtime" / "__init__.py",
        "wizard_facade": repo / "handlers" / "dub_wizard" / "__init__.py",
        "repair_handler": repo / "handlers" / "dub_audio_repair" / "__init__.py",
        "preflight_facade": voxcpm / "dub_job_preflight" / "__init__.py",
    }
    text = {name: _read(path) for name, path in paths.items()}
    checks = {
        "zero-safe-post-aac-v2": (
            'ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v2"'
            in text["zero_safe_qa"]
            and 'REPORT_SCHEMA = "dub-final-media-qa-v6"' in text["zero_safe_qa"]
            and "absolute_level_mode" in text["zero_safe_qa"]
            and "local_required_windows" in text["zero_safe_qa"]
            and "zero-safe two-branch regression" in text["zero_safe_qa"]
        ),
        "truthful-audio-repair-settings": (
            "def _dominant_segment_delay(" in text["repair_facade"]
            and "actual_delay_ms=_dominant_segment_delay(root)" in text["repair_facade"]
            and "_legacy._update_manifest = _update_manifest" in text["repair_facade"]
            and "from . import main" in text["repair_main"]
            and "actual_delay_ms: Any | None = None" in text["request_settings"]
            and 'payload["settings_delay_source"] = delay_source'
            in text["request_settings"]
        ),
        "strict-repair-request": (
            "def _validate_repair_request(" in text["repair_facade"]
            and "def _validated_sha256(" in text["repair_facade"]
            and "изменился после создания repair request" in text["repair_facade"]
            and "audio_repair.repair_all должен быть bool" in text["repair_facade"]
            and "manifest.audio_repairs должен быть списком" in text["repair_facade"]
            and "_legacy._checkpoint_ready = _checkpoint_ready" in text["repair_facade"]
            and "_legacy.legacy_repair._load_segments = _load_segments"
            in text["repair_facade"]
        ),
        "serialized-repair-handler": (
            "_DUBFIX_LOCK = asyncio.Lock()" in text["repair_handler"]
            and "async with _DUBFIX_LOCK" in text["repair_handler"]
            and "os.O_CREAT | os.O_EXCL | os.O_WRONLY" in text["repair_handler"]
            and "def _dubfix_process_lock(" in text["repair_handler"]
            and "def load_repair_segments(" in text["repair_handler"]
            and "def _write_repair_request(" in text["repair_handler"]
            and '"segments_sha256": digest' in text["repair_handler"]
            and "_legacy.dubfix_command = dubfix_command" in text["repair_handler"]
        ),
        "canonical-repair-delay": (
            "clean_request_settings.russian_delay_ms(request)" in text["normalizer"]
            and "clean_request_settings.russian_delay_ms(request)" in text["migration"]
            and 'request.get("russian_delay_ms") or 420' not in text["normalizer"]
            and 'request.get("russian_delay_ms") or 420' not in text["migration"]
        ),
        "transactional-repair-preprocess": (
            "strict_core._strict_int(" in text["normalizer"]
            and "strict_core._finite(" in text["normalizer"]
            and "strict_core._mark_and_validate_segments(" in text["normalizer"]
            and "allow_nan=False" in text["normalizer"]
            and "strict_core._strict_int(" in text["migration"]
            and "strict_core._finite(" in text["migration"]
            and "strict_core._mark_and_validate_segments(" in text["migration"]
            and "allow_nan=False" in text["migration"]
        ),
        "canonical-source-identity": (
            'for prefix in ("www.", "m.", "music.")' in text["source_download"]
            and 'host == "youtube-nocookie.com"' in text["source_download"]
            and "канонической ссылкой на один YouTube-ролик" in text["source_download"]
            and "def _project_request_video_id(" in text["source_download"]
            and "Project request и скачиваемый YouTube-ролик имеют разные video ID"
            in text["source_download"]
            and "до yt-dlp" in text["source_facade"]
            and "_legacy.download_source = download_source" in text["source_facade"]
            and "clean_source_download._url_video_id(raw)" in text["wizard_facade"]
            and "_legacy._extract_youtube_video_id = _extract_youtube_video_id"
            in text["wizard_facade"]
        ),
        "atomic-project-request": (
            "def load_request(" in text["project_runtime"]
            and "request.schema_version" in text["project_runtime"]
            and "allow_nan=False" in text["project_runtime"]
            and "os.replace(temporary, path)" in text["project_runtime"]
        ),
        "production-preflight-v2": (
            'POLICY = "dub-production-preflight-v2"' in text["preflight_facade"]
            and "REPORT_SCHEMA = 2" in text["preflight_facade"]
            and '"render_custom"' in text["preflight_facade"]
            and "generic_project_runtime.load_request(root)" in text["preflight_facade"]
            and "def _implementation_identity(" in text["preflight_facade"]
            and "def _cache_hit(" in text["preflight_facade"]
            and "uuid.uuid4().hex" in text["preflight_facade"]
            and "os.fsync(handle.fileno())" in text["preflight_facade"]
            and "PREFLIGHT_HEARTBEAT_SECONDS = 5.0" in text["preflight_facade"]
            and "def _preflight_heartbeat(" in text["preflight_facade"]
            and "clean_runtime_contract._model_manifest(" in text["preflight_facade"]
            and "clean_runtime_contract._voxcpm_runtime(" in text["preflight_facade"]
        ),
        "strict-segment-preflight": (
            "def _strict_int(" in text["core_facade"]
            and "segment[{position}].id" in text["core_facade"]
            and "start_delay_ms" in text["core_facade"]
            and "не может быть bool" in text["core_facade"]
            and "должен быть целым числом" in text["core_facade"]
            and "_legacy._mark_and_validate_segments = _mark_and_validate_segments"
            in text["core_facade"]
        ),
        "child-python-contract": (
            'CHILD_PYTHON_POLICY = "repo-root-pythonpath-and-master-stderr-v1"'
            in text["core_facade"]
            and "def _child_python_env(" in text["core_facade"]
            and "def _run_child_process(" in text["core_facade"]
            and "_legacy.subprocess = _SubprocessProxy()" in text["core_facade"]
        ),
        "facades-fingerprinted": (
            '"tools/voxcpm2/final_media_qa/__init__.py"' in text["runtime_contract"]
            and '"tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/generic_clean_audio_repair_runtime/__main__.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/legacy_segment_migration_v45.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/clean_source_download.py"' in text["runtime_contract"]
            and '"tools/voxcpm2/clean_runtime_contract/__init__.py"'
            in text["runtime_facade"]
            and '"tools/voxcpm2/clean_production_core/__init__.py"'
            in text["runtime_facade"]
            and '"tools/voxcpm2/generic_project_runtime/__init__.py"'
            in text["runtime_facade"]
            and '"tools/voxcpm2/clean_source_download/__init__.py"'
            in text["runtime_facade"]
            and "_legacy._RENDER_MODULES" in text["runtime_facade"]
        ),
        "strict-runtime-numbers": (
            'raise RuntimeError(f"{field} не может быть bool.")'
            in text["runtime_contract"]
            and "not value.is_integer()" in text["runtime_contract"]
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "facade-контракты не прошли: " + ", ".join(failed)
    return True, (
        "post-AAC original-bed v2 zero-safe/short-clip; final report v6; "
        "strict repair hash/scope/seed/checkpoints and cross-process /dubfix; "
        "segment-proven delay with transactional migration/normalizer; "
        "canonical pre-network source identity and atomic project request; "
        "production preflight v2 with full model/runtime fingerprints and heartbeat; "
        "deterministic child Python/master diagnostics; write-through service hooks; "
        "self-fingerprinted compatibility facades; strict runtime numbers"
    )


def _quality_contract(repo: Path) -> tuple[bool, str]:
    base_ok, base_detail = _legacy_quality_without_superseded_worker(repo)
    v46_ok, v46_detail = _v46_static_contract(repo)
    supplemental_ok, supplemental_detail = _supplemental_quality_contract(repo)
    detail = "; ".join((base_detail, v46_detail, supplemental_detail))
    return bool(base_ok and v46_ok and supplemental_ok), detail


# collect_dub_health resolves this global in the legacy module at call time.
_legacy._quality_contract = _quality_contract
collect_dub_health = _legacy.collect_dub_health
dubcheck_command = _legacy.dubcheck_command
register_dub_health_handler = _legacy.register_dub_health_handler

__all__ = [
    "_WORKER_RUNTIME",
    "collect_dub_health",
    "dubcheck_command",
    "register_dub_health_handler",
]
