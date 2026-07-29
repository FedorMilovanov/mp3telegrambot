#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade that extends the Dub Studio health contract.

The large, proven command implementation remains in ``handlers/dub_health.py``.
This package shadows it for normal imports, preserves its handlers, and adds
checks for compatibility packages introduced after the legacy health matrix.
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

_legacy_quality_contract = _legacy._quality_contract


def _supplemental_quality_contract(repo: Path) -> tuple[bool, str]:
    repo = Path(repo)
    voxcpm = repo / "tools" / "voxcpm2"
    paths = {
        "zero_safe_qa": voxcpm / "final_media_qa" / "__init__.py",
        "repair_facade": voxcpm / "generic_clean_audio_repair_runtime" / "__init__.py",
        "repair_main": voxcpm / "generic_clean_audio_repair_runtime" / "__main__.py",
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "normalizer": voxcpm / "clean_segment_normalizer.py",
        "migration": voxcpm / "legacy_segment_migration_v45.py",
        "source_download": voxcpm / "clean_source_download.py",
        "wizard_facade": repo / "handlers" / "dub_wizard" / "__init__.py",
    }
    text = {
        name: path.read_text(encoding="utf-8") if path.is_file() else ""
        for name, path in paths.items()
    }
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
        "canonical-repair-delay": (
            "clean_request_settings.russian_delay_ms(request)"
            in text["normalizer"]
            and "clean_request_settings.russian_delay_ms(request)"
            in text["migration"]
            and 'request.get("russian_delay_ms") or 420' not in text["normalizer"]
            and 'request.get("russian_delay_ms") or 420' not in text["migration"]
        ),
        "canonical-source-identity": (
            'for prefix in ("www.", "m.", "music.")' in text["source_download"]
            and 'host == "youtube-nocookie.com"' in text["source_download"]
            and "канонической ссылкой на один YouTube-ролик"
            in text["source_download"]
            and "def _project_request_video_id(" in text["source_download"]
            and "Project request и скачиваемый YouTube-ролик имеют разные video ID"
            in text["source_download"]
            and "clean_source_download._url_video_id(raw)" in text["wizard_facade"]
            and "_legacy._extract_youtube_video_id = _extract_youtube_video_id"
            in text["wizard_facade"]
        ),
        "facades-fingerprinted": (
            '"tools/voxcpm2/final_media_qa/__init__.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/generic_clean_audio_repair_runtime/__main__.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/legacy_segment_migration_v45.py"'
            in text["runtime_contract"]
            and '"tools/voxcpm2/clean_source_download.py"'
            in text["runtime_contract"]
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
        "repair manifest uses segment-proven delay; migration/normalizer preserve 0 ms; "
        "wizard/download share canonical URL+metadata+request identity; "
        "compatibility facades and migration fingerprinted; strict bool/fraction settings"
    )


def _quality_contract(repo: Path) -> tuple[bool, str]:
    base_ok, base_detail = _legacy_quality_contract(repo)
    supplemental_ok, supplemental_detail = _supplemental_quality_contract(repo)
    detail = base_detail + "; " + supplemental_detail
    return bool(base_ok and supplemental_ok), detail


# collect_dub_health resolves this global in the legacy module at call time.
_legacy._quality_contract = _quality_contract
collect_dub_health = _legacy.collect_dub_health
dubcheck_command = _legacy.dubcheck_command
register_dub_health_handler = _legacy.register_dub_health_handler

__all__ = [
    "collect_dub_health",
    "dubcheck_command",
    "register_dub_health_handler",
]
