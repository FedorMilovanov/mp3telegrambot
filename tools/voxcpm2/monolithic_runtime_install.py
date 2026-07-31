#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install monolithic renderer/master routing and fail-closed identity QA."""
from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any

POLICY = "monolithic-ready-srt-runtime-routing-v2"
FAIL_CLOSED_IDENTITY_POLICY = "cross-language-prosody-cannot-override-identity-v1"
MASTER_NAME = "master_monolithic_mix.py"
RENDERER_NAME = "voxcpm2_cpu_shorts_production.py"
ABSOLUTE_GLOBAL_F0_LIMIT_ST = 8.4
ABSOLUTE_ADJACENT_F0_RATIO = (0.62, 1.62)
ABSOLUTE_ADJACENT_P90_RATIO = (0.58, 1.72)
_INSTALLED = False


def _basename(value: Any) -> str:
    return re.split(r"[\\/]", str(value or ""))[-1].casefold()


def _renderer_paths(repo: Path) -> tuple[Path, Path]:
    root = Path(repo).resolve()
    renderer = (
        root
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / RENDERER_NAME
    )
    master = root / "tools" / "voxcpm2" / MASTER_NAME
    if not renderer.is_file() or not master.is_file():
        raise RuntimeError(
            "Monolithic production renderer/master не найдены: "
            f"renderer={renderer}; master={master}"
        )
    return renderer, master


def _is_master_command(command: Any) -> bool:
    return bool(
        isinstance(command, (list, tuple))
        and len(command) >= 2
        and _basename(command[0]).startswith("python")
        and _basename(command[1]) in {MASTER_NAME.casefold(), "master_constant_mix.py"}
    )


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _ratio(value: float, reference: float) -> float:
    return value / reference if value > 0.0 and reference > 0.0 else 0.0


def _semitones(value: float, reference: float) -> float | None:
    ratio = _ratio(value, reference)
    return 12.0 * math.log2(ratio) if ratio > 0.0 else None


def _append_failure(row: dict[str, Any], reason: str) -> None:
    failures = row.setdefault("failures", [])
    if reason not in failures:
        failures.append(reason)
    row["passed"] = False


def enforce_fail_closed_identity(
    rows: list[dict[str, Any]],
    *,
    baseline_f0: float,
) -> list[dict[str, Any]]:
    """Reapply speaker-identity pitch limits without cross-language exceptions.

    Source prosody remains attached as advisory evidence, but English timing or
    lexical stress can never widen the Russian hard limits.
    """
    baseline = _finite(baseline_f0)
    for index, row in enumerate(rows):
        row["fail_closed_identity_policy"] = FAIL_CLOSED_IDENTITY_POLICY
        transition = row.get("source_relative_transition")
        if isinstance(transition, dict):
            transition["absolute_gate_override_allowed"] = False
            transition["role"] = "ranking_and_diagnostics_only"

        pitch = row.get("pitch") if isinstance(row.get("pitch"), dict) else {}
        global_jump = _semitones(_finite(pitch.get("f0_median")), baseline)
        row["fail_closed_global_f0_jump_st"] = global_jump
        row["fail_closed_global_f0_limit_st"] = ABSOLUTE_GLOBAL_F0_LIMIT_ST
        if global_jump is not None and abs(global_jump) > ABSOLUTE_GLOBAL_F0_LIMIT_ST:
            _append_failure(row, "global_voice_f0_outlier_fail_closed")

        if index == 0:
            continue
        previous_pitch = (
            rows[index - 1].get("pitch")
            if isinstance(rows[index - 1].get("pitch"), dict)
            else {}
        )
        median_ratio = _ratio(
            _finite(pitch.get("f0_median")),
            _finite(previous_pitch.get("f0_median")),
        )
        p90_ratio = _ratio(
            _finite(pitch.get("f0_p90")),
            _finite(previous_pitch.get("f0_p90")),
        )
        row["fail_closed_neighbour_f0_median_ratio"] = median_ratio
        row["fail_closed_neighbour_f0_p90_ratio"] = p90_ratio
        if median_ratio and not (
            ABSOLUTE_ADJACENT_F0_RATIO[0]
            <= median_ratio
            <= ABSOLUTE_ADJACENT_F0_RATIO[1]
        ):
            _append_failure(row, "adjacent_voice_pitch_discontinuity_fail_closed")
        if p90_ratio and not (
            ABSOLUTE_ADJACENT_P90_RATIO[0]
            <= p90_ratio
            <= ABSOLUTE_ADJACENT_P90_RATIO[1]
        ):
            _append_failure(row, "adjacent_voice_range_discontinuity_fail_closed")
    return rows


def _install_fail_closed_timeline() -> None:
    from tools.voxcpm2 import direct_timeline_delivery_qa as timeline

    if getattr(timeline, "_FAIL_CLOSED_IDENTITY_INSTALLED", False):
        return
    original = timeline._sequence_checks

    def fail_closed_sequence_checks(rows: list[dict[str, Any]]) -> dict[str, float]:
        result = dict(original(rows))
        enforce_fail_closed_identity(
            rows,
            baseline_f0=_finite(result.get("baseline_f0_median")),
        )
        result["fail_closed_identity_policy"] = FAIL_CLOSED_IDENTITY_POLICY
        result["absolute_global_f0_limit_st"] = ABSOLUTE_GLOBAL_F0_LIMIT_ST
        return result

    timeline._sequence_checks = fail_closed_sequence_checks
    timeline.FAIL_CLOSED_IDENTITY_POLICY = FAIL_CLOSED_IDENTITY_POLICY
    timeline._FAIL_CLOSED_IDENTITY_INSTALLED = True


def install() -> None:
    global _INSTALLED
    from tools.voxcpm2 import clean_production_core

    legacy = getattr(clean_production_core, "_legacy", None)
    if legacy is None:
        raise RuntimeError("Clean production core не предоставляет runtime facade.")
    legacy._renderer_paths = _renderer_paths
    clean_production_core._renderer_paths = _renderer_paths
    clean_production_core._is_master_command = _is_master_command
    _install_fail_closed_timeline()
    _INSTALLED = True


__all__ = [
    "ABSOLUTE_ADJACENT_F0_RATIO",
    "ABSOLUTE_ADJACENT_P90_RATIO",
    "ABSOLUTE_GLOBAL_F0_LIMIT_ST",
    "FAIL_CLOSED_IDENTITY_POLICY",
    "MASTER_NAME",
    "POLICY",
    "RENDERER_NAME",
    "_install_fail_closed_timeline",
    "_is_master_command",
    "_renderer_paths",
    "enforce_fail_closed_identity",
    "install",
]
