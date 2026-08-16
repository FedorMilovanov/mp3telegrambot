#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure universal helpers and generic timing preflight for VoxCPM2 production.

Direct CLI state and candidate/retry wrappers are owned by the canonical CLI.
This module exposes shared calculations plus the still-separate generic preflight.
"""
from __future__ import annotations

import json
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from tools.voxcpm2 import direct_timing_guard

POLICY = "voxcpm2-universal-production-hardening-v1"
_PROGRESS_POLICY = "candidate-aware-project-progress-v1"
_MODEL_TQDM_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)*\s*\d{1,3}%\|.*\|\s*\d+/\d+\s*\["
)


def _segments_by_id(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in segments:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        result[segment_id] = item
    return result


def _progress_value(
    *, position: int, total: int, attempt: int, max_attempts: int,
) -> int:
    total_value = max(1, int(total))
    position_value = max(1, min(int(position), total_value))
    attempts_value = max(1, int(max_attempts))
    attempt_value = max(1, min(int(attempt), attempts_value))
    fraction = (position_value - 1) + (attempt_value - 1) / attempts_value
    return max(8, min(86, 8 + round(fraction / total_value * 78)))


def install_generic_preflight(namespace: MutableMapping[str, Any]) -> None:
    """Run final semantic-block timing validation before references and model."""
    original = namespace.get("_run_clean_voxcpm_and_master")
    if not callable(original):
        raise RuntimeError(
            "generic_clean_direct_runtime не содержит _run_clean_voxcpm_and_master."
        )
    clean = namespace["clean"]
    direct_io = namespace["direct_io"]
    read_json_value = namespace["_read_json_value"]

    def _run_clean_voxcpm_and_master(**kwargs: Any) -> Path:
        root = Path(kwargs["root"]).resolve()
        request = dict(kwargs["request"])
        duration = float(kwargs["duration"])
        segments_json = Path(kwargs["segments_json"]).resolve()
        settings = clean.clean_runtime_contract.normalize_settings(
            request, duration=duration,
        )
        backend = clean.get_backend(
            request.get("speech_backend") or clean.DEFAULT_BACKEND_ID
        )
        if backend.backend_id == "voxcpm2":
            repo = Path(namespace["__file__"]).resolve().parents[2]
            runtime = backend.runtime_paths(repo, request)
            model_path = backend.discover_model(runtime.archive_root)
            model_config = model_path / "config.json"
            if not model_config.is_file():
                raise RuntimeError(
                    f"Не найден config.json выбранной TTS-модели: {model_config}"
                )
            segments_payload = read_json_value(segments_json)
            if not isinstance(segments_payload, list):
                raise RuntimeError(
                    "segments_ru_final.json повреждён до timing preflight."
                )
            speech_options = request.get("speech_options") or {}
            if not isinstance(speech_options, dict):
                raise RuntimeError("speech_options должен быть JSON-объектом.")
            context = {
                "policy": POLICY,
                "backend": backend.backend_id,
                "adapter_policy": backend.adapter_policy,
                "cfg": float(settings["cfg"]),
                "steps": int(settings["steps"]),
                "base_seed": int(settings["base_seed"]),
                "max_tempo": float(direct_io.MAX_TEMPO),
                "model_config_sha256": direct_io.sha256_file(model_config),
                "speech_model_profile": str(
                    request.get("speech_model_profile") or ""
                ),
                "speech_profile_fingerprint": str(
                    request.get("speech_profile_fingerprint") or ""
                ),
                "speech_options": speech_options,
            }
            work_dir = root / "segment_work"
            direct_timing_guard.write_signature_context(work_dir, context)
            report = direct_timing_guard.run_pre_model_guard(
                segments_payload,
                work_dir=work_dir,
                max_tempo=direct_io.MAX_TEMPO,
                signature_context=context,
            )
            namespace["production"].log(
                "universal timing preflight passed before voice references/model: "
                f"warnings={report.get('warning_ids') or []}"
            )
        return original(**kwargs)

    namespace["_run_clean_voxcpm_and_master"] = _run_clean_voxcpm_and_master


__all__ = ['POLICY', 'install_generic_preflight']
