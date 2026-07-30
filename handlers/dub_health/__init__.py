#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade extending the durable Dub Studio health contract.

The proven command and environment checks remain in ``handlers/dub_health.py``.
This facade keeps those checks, replaces only the superseded worker-v4.5 source
assertion, and verifies the active package layers that Python resolves before
the sibling legacy files.
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

_WORKER_RUNTIME = "dub-worker-quality-v4.7"
from services import dub_studio_runtime as _supervisor  # noqa: E402

_supervisor._WORKER_RUNTIME = _WORKER_RUNTIME
_legacy._WORKER_RUNTIME = _WORKER_RUNTIME
_legacy_quality_contract = _legacy._quality_contract


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _texts(repo: Path) -> dict[str, str]:
    repo = Path(repo)
    voxcpm = repo / "tools" / "voxcpm2"
    paths = {
        "worker": voxcpm / "dub_worker_hardened.py",
        "worker_facade": voxcpm / "dub_worker_hardened" / "__init__.py",
        "worker_main": voxcpm / "dub_worker_hardened" / "__main__.py",
        "preflight": voxcpm / "dub_job_preflight.py",
        "preflight_facade": voxcpm / "dub_job_preflight" / "__init__.py",
        "core_facade": voxcpm / "clean_production_core" / "__init__.py",
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "runtime_facade": voxcpm / "clean_runtime_contract" / "__init__.py",
        "direct_cli": voxcpm / "direct_max_quality_cli.py",
        "analysis_facade": voxcpm / "direct_max_quality_analysis" / "__init__.py",
        "render_facade": voxcpm / "direct_max_quality_render" / "__init__.py",
        "cadence_facade": voxcpm / "direct_russian_cadence" / "__init__.py",
        "tail_artifact": voxcpm / "direct_tail_artifact.py",
        "delivery_qa": voxcpm / "direct_timeline_delivery_qa.py",
        "encoded_delivery_qa": voxcpm / "final_encoded_delivery_qa.py",
        "zero_safe_qa": voxcpm / "final_media_qa" / "__init__.py",
        "repair_facade": voxcpm / "generic_clean_audio_repair_runtime" / "__init__.py",
        "repair_main": voxcpm / "generic_clean_audio_repair_runtime" / "__main__.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "normalizer": voxcpm / "clean_segment_normalizer.py",
        "migration": voxcpm / "legacy_segment_migration_v45.py",
        "source_download": voxcpm / "clean_source_download.py",
        "source_facade": voxcpm / "clean_source_download" / "__init__.py",
        "project_runtime": voxcpm / "generic_project_runtime" / "__init__.py",
        "supervisor_facade": repo / "services" / "dub_studio_runtime" / "__init__.py",
        "title_facade": repo / "services" / "dub_title_policy" / "__init__.py",
        "wizard_facade": repo / "handlers" / "dub_wizard" / "__init__.py",
        "repair_handler": repo / "handlers" / "dub_audio_repair" / "__init__.py",
        "health_facade": Path(__file__).resolve(),
    }
    return {name: _read(path) for name, path in paths.items()}


def _has(text: dict[str, str], file_key: str, *markers: str) -> bool:
    value = text.get(file_key, "")
    return bool(value) and all(marker in value for marker in markers)


def _v47_static_contract(repo: Path) -> tuple[bool, str]:
    text = _texts(repo)
    checks = {
        "worker-agent-v47": _has(
            text,
            "worker_facade",
            '_RUNTIME_VERSION = "dub-worker-quality-v4.7"',
            "_legacy._RUNTIME_VERSION = _RUNTIME_VERSION",
            'DELIVERY_RESILIENCE_POLICY = "cadence-tail-fit-adaptive-resume-v1"',
            "def _execute_job_with_cancellable_preflight(",
            "_legacy.worker.execute_job = _execute_job_with_cancellable_preflight",
        ) and _has(
            text,
            "worker",
            "from tools.voxcpm2 import dub_job_preflight",
            "def _execute_job_with_preflight(",
            "worker.execute_job = _execute_job_with_preflight",
        ),
        "worker-package-cancel-root": _has(
            text,
            "worker_facade",
            'CANCELLATION_POLICY = "preflight-cancel-before-runner-v1"',
            'STORE_ROOT_POLICY = "explicit-worker-root-propagation-v2"',
            "with _store_root_environment(store):",
            "reason = _stop_reason(store, job_id)",
            "_legacy._ORIGINAL_EXECUTE_JOB(store, worker_id, job)",
            "_legacy.install_hardening()",
        ) and _has(text, "worker_main", "from . import main", "main()"),
        "production-preflight-v2": _has(
            text,
            "preflight",
            'POLICY = "dub-production-preflight-v1"',
        ) and _has(
            text,
            "preflight_facade",
            'POLICY = "dub-production-preflight-v2"',
            "REPORT_SCHEMA = 2",
            '"render_custom"',
            "generic_project_runtime.load_request(root)",
            "def _implementation_identity(",
            "def _cache_hit(",
            "PREFLIGHT_HEARTBEAT_SECONDS = 5.0",
            "def _preflight_heartbeat(",
            "clean_runtime_contract._model_manifest(",
            "clean_runtime_contract._voxcpm_runtime(",
            "uuid.uuid4().hex",
            "os.fsync(handle.fileno())",
            "*clean_runtime_contract._RENDER_MODULES",
            "*clean_runtime_contract._RELEASE_MODULES",
            '"tools/voxcpm2/dub_job_preflight/__init__.py"',
            "recipe.work_root",
            'status="busy"',
        ),
        "worker-runtime-sync": _has(
            text,
            "health_facade",
            '_WORKER_RUNTIME = "dub-worker-quality-v4.7"',
            "_supervisor._WORKER_RUNTIME = _WORKER_RUNTIME",
            "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME",
        ) and _has(
            text,
            "supervisor_facade",
            '_WORKER_RUNTIME = "dub-worker-quality-v4.7"',
            "class _WriteThroughModule",
            "_module.__class__ = _WriteThroughModule",
        ),
        "long-form-direct-resilience": _has(
            text,
            "analysis_facade",
            'FIT_TEMPO_POLICY = "candidate-fit-tempo-hard-gate-v1"',
            "tempo <= float(MAX_TEMPO) + 1e-9",
        ) and _has(
            text,
            "render_facade",
            'ADAPTIVE_RETRY_POLICY = "direct-candidate-adaptive-retry-v1"',
            "if attempt == 4:",
            "if attempt == 5:",
        ) and _has(
            text,
            "cadence_facade",
            'DELIVERY_POLICY = "russian-ending-and-emphasis-hard-gate-v1"',
            'failures.append("terminal_not_resolved")',
            'failures.append("firm_terminal_not_resolved")',
            'failures.append("emphasis_too_early")',
        ) and _has(
            text,
            "direct_cli",
            "MAX_CANDIDATE_ATTEMPTS = 5",
            "for attempt_index in range(1, MAX_CANDIDATE_ATTEMPTS + 1):",
            '"candidate_contract": {',
            '"selected_required_tempo"',
            "build_timeline(fitted_segments, output, float(args.video_duration))",
        ) and _has(
            text,
            "tail_artifact",
            'POLICY = "late-broadband-tail-v2"',
            '"artifact_type": "late_broadband_burst"',
        ) and _has(
            text,
            "delivery_qa",
            'POLICY = "assembled-russian-delivery-v1"',
            "verify_timeline_delivery",
            "invalidated_for_retry",
        ) and _has(
            text,
            "encoded_delivery_qa",
            'POLICY = "post-aac-russian-delivery-v1"',
            "MAX_SEGMENT_WINDOW_SECONDS = 30.0",
            "def verify_final_encoded_russian(",
            "decode only the final SRT window",
        ),
        "title-health-write-through": _has(
            text,
            "title_facade",
            "_legacy._patch_health = _patch_health",
            "legacy_health.collect_dub_health = wrapped",
        ),
        "child-python-contract": _has(
            text,
            "core_facade",
            'CHILD_PYTHON_POLICY = "repo-root-pythonpath-master-stderr-and-post-aac-v2"',
            "def _child_python_env(",
            "def _is_master_release_command(",
            "def _verify_post_aac_master_output(",
            "final_encoded_delivery_qa.verify_final_encoded_russian(",
            "def _run_child_process(",
            "_legacy.subprocess = _SubprocessProxy()",
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "v4.7-контракты не прошли: " + ", ".join(failed)
    return True, (
        "worker v4.7/preflight v2; cancellation and explicit root; "
        "fit-aware adaptive retries; Russian ending/emphasis gates; late-tail, assembled and post-AAC QA; "
        "full implementation/model/runtime cache; deterministic child imports"
    )


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
    return True, "все legacy-контракты активны; worker-v45 заменён v4.7 preflight"


def _supplemental_quality_contract(repo: Path) -> tuple[bool, str]:
    text = _texts(repo)
    checks = {
        "zero-safe-post-aac-v2": _has(
            text,
            "zero_safe_qa",
            'ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v2"',
            'REPORT_SCHEMA = "dub-final-media-qa-v6"',
            "absolute_level_mode",
            "local_required_windows",
            "zero-safe two-branch regression",
        ),
        "strict-repair-request": _has(
            text,
            "repair_facade",
            "def _validate_repair_request(",
            "def _validated_sha256(",
            "изменился после создания repair request",
            "audio_repair.repair_all должен быть bool",
            "manifest.audio_repairs должен быть списком",
            "_legacy._checkpoint_ready = _checkpoint_ready",
            "_legacy.legacy_repair._load_segments = _load_segments",
            "def _dominant_segment_delay(",
            "actual_delay_ms=_dominant_segment_delay(root)",
            "_legacy._update_manifest = _update_manifest",
        ) and _has(text, "repair_main", "from . import main"),
        "serialized-repair-handler": _has(
            text,
            "repair_handler",
            "_DUBFIX_LOCK = asyncio.Lock()",
            "async with _DUBFIX_LOCK",
            "os.O_CREAT | os.O_EXCL | os.O_WRONLY",
            "def _dubfix_process_lock(",
            "def load_repair_segments(",
            "def _write_repair_request(",
            '"segments_sha256": digest',
            "_legacy.dubfix_command = dubfix_command",
        ),
        "truthful-request-settings": _has(
            text,
            "request_settings",
            "actual_delay_ms: Any | None = None",
            'payload["settings_delay_source"] = delay_source',
        ),
        "transactional-repair-preprocess": _has(
            text,
            "normalizer",
            "clean_request_settings.russian_delay_ms(request)",
            "strict_core._strict_int(",
            "strict_core._finite(",
            "strict_core._mark_and_validate_segments(",
            "allow_nan=False",
        ) and _has(
            text,
            "migration",
            "clean_request_settings.russian_delay_ms(request)",
            "strict_core._strict_int(",
            "strict_core._finite(",
            "strict_core._mark_and_validate_segments(",
            "allow_nan=False",
        ) and 'request.get("russian_delay_ms") or 420' not in text["normalizer"]
        and 'request.get("russian_delay_ms") or 420' not in text["migration"],
        "canonical-source-identity": _has(
            text,
            "source_download",
            'for prefix in ("www.", "m.", "music.")',
            'host == "youtube-nocookie.com"',
            "канонической ссылкой на один YouTube-ролик",
            "def _project_request_video_id(",
            "Project request и скачиваемый YouTube-ролик имеют разные video ID",
        ) and _has(
            text,
            "source_facade",
            "до yt-dlp",
            "_legacy.download_source = download_source",
        ) and _has(
            text,
            "wizard_facade",
            "clean_source_download._url_video_id(raw)",
            "_legacy._extract_youtube_video_id = _extract_youtube_video_id",
        ),
        "atomic-project-request": _has(
            text,
            "project_runtime",
            'POLICY = "generic-project-runtime-write-through-v2"',
            "def validate_request_payload(",
            "request.schema_version",
            "uuid.uuid4().hex",
            "os.fsync(handle.fileno())",
            "allow_nan=False",
            "os.replace(temporary, path)",
            "class _WriteThroughModule",
            "_module.__class__ = _WriteThroughModule",
            "_legacy.validate_request_payload = validate_request_payload",
        ) and _has(
            text,
            "wizard_facade",
            "def _write_request(",
            "generic_project_runtime.validate_request_payload(payload)",
            "generic_project_runtime.save_json(destination, validated)",
            "_legacy._write_request = _write_request",
            "_legacy._create_generic_project = _create_generic_project",
        ),
        "strict-segment-preflight": _has(
            text,
            "core_facade",
            "def _strict_int(",
            "segment[{position}].id",
            "start_delay_ms",
            "не может быть bool",
            "должен быть целым числом",
            "_legacy._mark_and_validate_segments = _mark_and_validate_segments",
        ),
        "facades-fingerprinted": _has(
            text,
            "runtime_contract",
            '"tools/voxcpm2/final_media_qa/__init__.py"',
            '"tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py"',
            '"tools/voxcpm2/generic_clean_audio_repair_runtime/__main__.py"',
            '"tools/voxcpm2/legacy_segment_migration_v45.py"',
            '"tools/voxcpm2/clean_source_download.py"',
        ) and _has(
            text,
            "runtime_facade",
            '"tools/voxcpm2/clean_runtime_contract/__init__.py"',
            '"tools/voxcpm2/clean_production_core/__init__.py"',
            '"tools/voxcpm2/generic_project_runtime/__init__.py"',
            '"tools/voxcpm2/clean_source_download/__init__.py"',
            '"tools/voxcpm2/direct_max_quality_analysis/__init__.py"',
            '"tools/voxcpm2/direct_max_quality_render/__init__.py"',
            '"tools/voxcpm2/direct_russian_cadence/__init__.py"',
            '"tools/voxcpm2/direct_tail_artifact.py"',
            '"tools/voxcpm2/direct_timeline_delivery_qa.py"',
            '"tools/voxcpm2/final_encoded_delivery_qa.py"',
            "_legacy._RENDER_MODULES",
            "_legacy._RELEASE_MODULES",
        ),
        "strict-runtime-numbers": _has(
            text,
            "runtime_contract",
            'raise RuntimeError(f"{field} не может быть bool.")',
            "not value.is_integer()",
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "facade-контракты не прошли: " + ", ".join(failed)
    return True, (
        "zero-safe final QA; strict repair/source/segment/project contracts; "
        "truthful 0-ms settings; transactional preprocessing; clean adapters write through; "
        "wizard request barrier; cadence/tail/fit/post-AAC gates fingerprinted"
    )


def _quality_contract(repo: Path) -> tuple[bool, str]:
    base_ok, base_detail = _legacy_quality_without_superseded_worker(repo)
    v47_ok, v47_detail = _v47_static_contract(repo)
    supplemental_ok, supplemental_detail = _supplemental_quality_contract(repo)
    detail = "; ".join((base_detail, v47_detail, supplemental_detail))
    return bool(base_ok and v47_ok and supplemental_ok), detail


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
