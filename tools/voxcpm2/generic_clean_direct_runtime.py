#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal ready-SRT runtime with explicit composed production contracts."""
from pathlib import Path
from typing import Any

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_generic_clean_direct_runtime_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing clean direct runtime base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._generic_clean_direct_runtime_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish
from tools.voxcpm2.direct_universal_runtime import install_generic_preflight


production = globals().get("production")
if production is None or getattr(production, "hardened", None) is None:
    raise RuntimeError("Clean direct base did not export production.hardened.")
hardened = production.hardened
hardened.download_source = clean_source_download.download_source
hardened.pipeline.download_source = clean_source_download.download_source



def _continuous_reference_contract(**kwargs: Any) -> Any:
    return continuous_reference_policy.build_calm_references(**kwargs)


def _identity_reference_contract(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    output: Path,
    extended: Path,
) -> Any:
    return controlled_reference_gate.build_or_keep_calm(
        source=source,
        segments=segments,
        output=output,
        identity_reference=extended,
    )


def _expression_contract(**kwargs: Any) -> Any:
    return expressive_continuity.plan_json(**kwargs)


def _resume_contract(*, force_fresh=False) -> bool:
    """Keeping this False makes a late failed segment resumable."""
    if force_fresh:
        raise RuntimeError("Ready-SRT direct route must preserve compatible checkpoints.")
    return False


DIRECT_FORCE_FRESH = _resume_contract()
REFERENCE_BUILDERS = (
    _continuous_reference_contract,
    _identity_reference_contract,
    _expression_contract,
)

install_global_polish()
install_generic_preflight(globals())

if _ORIGINAL_NAME == "__main__":
    _main = globals().get("main")
    if not callable(_main):
        raise RuntimeError("Clean direct runtime base did not export main().")
    _main()

_BASE_ALL = tuple(globals().get('__all__', ()))

from pathlib import Path

import types

from typing import Any

CHECKPOINT_MIGRATION_POLICY = "signature-and-natural-tempo-checkpoint-adoption-v2"

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
    segments_payload = _read_json_value(root / "segments_ru_final.json")
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
    stride = int(clean.clean_runtime_contract.RETRY_SEED_OFFSET)
    accepted_ids: list[int] = []
    accepted_seed_rounds: set[int] = set()

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
            or not _same_number(
                report.get("tail_guard"), segment.get("tail_guard")
            )
            or float(fit.get("tempo") or 999.0)
            > float(direct_io.PREFERRED_MAX_TEMPO) + 1e-6
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
        }
        for key, expected in expected_core.items():
            actual = signature.get(key)
            if isinstance(expected, float):
                if not _same_number(actual, expected):
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
        clean.semantic_tts_guard_v4._retarget(
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

_legacy_checkpoint_prefix = _legacy_checkpoint_prefix

_LEGACY_RESUME_POLICY = CHECKPOINT_MIGRATION_POLICY

def main() -> None:
    _legacy_checkpoint_prefix = _legacy_checkpoint_prefix
    _LEGACY_RESUME_POLICY = CHECKPOINT_MIGRATION_POLICY
    main()

__all__ = sorted(
    set(name for name in _BASE_ALL if not name.startswith("__"))
    | {
        "CHECKPOINT_MIGRATION_POLICY",
        "MAX_ACCEPTED_SEED_ROUNDS",
        "_legacy_checkpoint_prefix",
        "_signature_seed_round",
        "_signature_valid_checkpoint_set",
        "main",
    }
)
