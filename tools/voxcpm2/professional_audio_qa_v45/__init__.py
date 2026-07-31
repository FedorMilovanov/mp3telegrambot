#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade adding cheap timing repair before model regeneration.

The sibling module remains the independent semantic/acoustic/voice QA. This
facade changes only the recovery order: a phrase that passes every non-timing
check but starts late inside its own SRT window is shifted as PCM and only its
timing is remeasured. Speech content, voice evidence and synthesis checkpoints
remain untouched. Only defects that survive this repair reach the model again.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2 import timeline_onset_repair

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "professional_audio_qa_v45.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._professional_audio_qa_v45_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить independent audio QA: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

TIMING_REPAIR_POLICY = "cheap-timeline-onset-before-resynthesis-v1"
TIMING_RECHECK_POLICY = "repaired-windows-only-no-repeat-asr-v1"
_legacy_verify_timeline_v45 = _legacy.verify_timeline_v45


def _pre_repair_report_path(report_path: Path) -> Path:
    return report_path.with_name(report_path.stem + ".pre_onset_repair.json")


def _segment_map(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(item["id"]): item
        for item in segments
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }


def _check_map(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item["id"]): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }


def _all_non_timing_checks_pass(check: dict[str, Any]) -> bool:
    for name in ("semantic", "acoustic", "continuity_v45", "voice_match_v45"):
        component = check.get(name)
        if isinstance(component, dict) and component.get("passed") is False:
            return False
    return True


def _remeasure_repaired_timing(
    timeline: Path,
    segments: list[dict[str, Any]],
    report: dict[str, Any],
    repaired_ids: list[int],
) -> tuple[list[int], dict[str, Any]]:
    samples, sample_rate = sf.read(str(timeline), dtype="float32", always_2d=False)
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.reshape(-1)
    rate = max(1, int(sample_rate))
    segment_by_id = _segment_map(segments)
    check_by_id = _check_map(report)

    for segment_id in repaired_ids:
        segment = segment_by_id.get(segment_id)
        check = check_by_id.get(segment_id)
        if not isinstance(segment, dict) or not isinstance(check, dict):
            continue
        previous = check.get("timing") if isinstance(check.get("timing"), dict) else {}
        delay = max(0, int(segment.get("start_delay_ms", 0) or 0)) / 1000.0
        start = max(0.0, float(segment.get("start", 0.0)) + delay)
        duration = max(
            0.35,
            float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)),
        )
        left = max(0, int(round(start * rate)))
        right = min(len(audio), int(round((start + duration) * rate)))
        clip = audio[left:right]
        timing = _legacy.semantic_tts_guard_v4.measure_timing_quality(
            clip,
            rate,
            max_onset_ms=int(previous.get("max_onset_ms", 220) or 220),
            min_trailing_ms=int(previous.get("min_trailing_ms", 45) or 45),
        )
        timing["recheck_policy"] = TIMING_RECHECK_POLICY
        timing["previous_onset_ms"] = previous.get("onset_ms")
        timing["previous_trailing_ms"] = previous.get("trailing_ms")
        check["timing"] = timing
        check["passed"] = bool(
            timing.get("passed") and _all_non_timing_checks_pass(check)
        )

    failed = sorted(
        int(segment_id)
        for segment_id, check in check_by_id.items()
        if check.get("passed") is not True
    )
    report["failed_segment_ids"] = failed
    report["passed"] = not failed
    return failed, report


def verify_timeline_v45(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    """Run independent QA, repair timing-only onsets, then remeasure those IDs."""
    failed, report = _legacy_verify_timeline_v45(timeline, segments, report_path)
    if not failed:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_recheck_policy"] = TIMING_RECHECK_POLICY
        report["timing_repair_attempted"] = False
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    repairable = timeline_onset_repair.repairable_segment_ids(report)
    if not repairable:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_recheck_policy"] = TIMING_RECHECK_POLICY
        report["timing_repair_attempted"] = False
        report["timing_repairable_segment_ids"] = []
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    pre_repair_path = _pre_repair_report_path(report_path)
    pre_repair_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    repair = timeline_onset_repair.repair_timeline_onsets(
        timeline,
        segments,
        report,
        report_path=report_path.with_name(report_path.stem + ".onset_repair.json"),
    )
    repaired_ids = [int(value) for value in repair.get("repaired_segment_ids", [])]
    if not repaired_ids:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_recheck_policy"] = TIMING_RECHECK_POLICY
        report["timing_repair_attempted"] = True
        report["timing_repair"] = repair
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    repaired_failed, repaired_report = _remeasure_repaired_timing(
        timeline,
        segments,
        report,
        repaired_ids,
    )
    repaired_report.update(
        timing_repair_policy=TIMING_REPAIR_POLICY,
        timing_recheck_policy=TIMING_RECHECK_POLICY,
        timing_repair_attempted=True,
        timing_repair=repair,
        pre_timing_repair_report=str(pre_repair_path),
        timing_repair_resolved_segment_ids=sorted(set(failed) - set(repaired_failed)),
        timing_repair_remaining_failed_segment_ids=repaired_failed,
        repeated_semantic_asr=False,
        repeated_voice_analysis=False,
    )
    report_path.write_text(
        json.dumps(repaired_report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return repaired_failed, repaired_report


_legacy.verify_timeline_v45 = verify_timeline_v45


class _WriteThroughModule(types.ModuleType):
    """Keep QA monkeypatches synchronized with the sibling implementation."""

    def __setattr__(self, name: str, value: Any) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str) -> Any:
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "TIMING_RECHECK_POLICY",
        "TIMING_REPAIR_POLICY",
        "_remeasure_repaired_timing",
        "verify_timeline_v45",
    }
)
