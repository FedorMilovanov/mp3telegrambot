#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ready-SRT runtime facade with signature-based checkpoint migration.

The sibling runtime already seeds resumable markers before clean production. Its
historical migration accepted only an incomplete prefix and used the old 1.35
preferred tempo boundary. This facade accepts a complete contiguous checkpoint
set as well, but only after validating every synthesis-relevant signature and
artifact against the current project. QA/orchestration upgrades therefore do not
force hours of identical model inference.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "generic_clean_direct_runtime.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._generic_clean_direct_runtime_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить ready-SRT runtime: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

CHECKPOINT_MIGRATION_POLICY = "signature-verified-complete-checkpoint-adoption-v1"
MAX_ACCEPTED_SEED_ROUNDS = 12


def _signature_seed_round(actual: Any, base_seed: int, stride: int) -> int | None:
    try:
        value = int(actual)
    except (TypeError, ValueError, OverflowError):
        return None
    delta = value - int(base_seed)
    if stride <= 0 or delta < 0 or delta % stride:
        return None
    round_index = delta // stride
    return round_index if round_index <= MAX_ACCEPTED_SEED_ROUNDS else None


def _signature_valid_checkpoint_set(
    root: Path,
    request: dict[str, Any],
) -> list[int]:
    segments_payload = _legacy._read_json_value(root / "segments_ru_final.json")
    if not isinstance(segments_payload, list) or len(segments_payload) < 1:
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
    stride = int(_legacy.clean.clean_runtime_contract.RETRY_SEED_OFFSET)
    accepted_ids: list[int] = []
    accepted_seed_rounds: set[int] = set()

    for path in checkpoint_paths:
        payload = _legacy._read_json(path)
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
            report.get("renderer_policy") != _legacy.direct_io.POLICY
            or report.get("selected_raw_pitch_evidence_ok") is not True
            or not isinstance(fit, dict)
            or not _legacy._same_number(report.get("start"), segment.get("start"))
            or not _legacy._same_number(report.get("end"), segment.get("end"))
            or not _legacy._same_number(
                report.get("tail_guard"), segment.get("tail_guard")
            )
            or float(fit.get("tempo") or 999.0)
            > float(_legacy.direct_io.MAX_TEMPO) + 1e-6
        ):
            return []

        expected_core = {
            "policy": _legacy.direct_io.POLICY,
            "text": str(segment.get("text") or ""),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "tail_guard": float(segment["tail_guard"]),
            "start_delay_ms": int(segment.get("start_delay_ms", 0)),
            "reference_profile": profile,
            "expression": _legacy._expected_expression(segment),
            "steps": steps,
            "cfg": cfg,
        }
        for key, expected in expected_core.items():
            actual = signature.get(key)
            if isinstance(expected, float):
                if not _legacy._same_number(actual, expected):
                    return []
            elif actual != expected:
                return []
        seed_round = _signature_seed_round(
            signature.get("base_seed"),
            base_seed,
            stride,
        )
        if seed_round is None:
            return []
        accepted_seed_rounds.add(seed_round)
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
    if accepted_ids[-1] > len(segments_payload):
        return []

    if accepted_seed_rounds != {0}:
        # The direct renderer expects the request base seed in checkpoint
        # signatures. Retarget metadata only; fitted WAVs remain byte-identical.
        _legacy.clean.semantic_tts_guard_v4._retarget(
            segment_work,
            good_ids=accepted_ids,
            failed_ids=[],
            new_base_seed=base_seed,
        )
    return accepted_ids


def _legacy_checkpoint_prefix(
    root: Path,
    request: dict[str, Any],
) -> list[int]:
    return _signature_valid_checkpoint_set(root, request)


_legacy._legacy_checkpoint_prefix = _legacy_checkpoint_prefix
_legacy._LEGACY_RESUME_POLICY = CHECKPOINT_MIGRATION_POLICY


def main() -> None:
    _legacy._legacy_checkpoint_prefix = _legacy_checkpoint_prefix
    _legacy._LEGACY_RESUME_POLICY = CHECKPOINT_MIGRATION_POLICY
    _legacy.main()


__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "CHECKPOINT_MIGRATION_POLICY",
        "MAX_ACCEPTED_SEED_ROUNDS",
        "_legacy_checkpoint_prefix",
        "_signature_seed_round",
        "_signature_valid_checkpoint_set",
        "main",
    }
)
