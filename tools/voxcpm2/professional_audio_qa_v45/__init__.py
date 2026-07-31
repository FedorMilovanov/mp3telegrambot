#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade adding cheap timing repair before model regeneration.

The sibling module remains the independent semantic/acoustic/voice QA. This
facade changes only the recovery order: a phrase that passes every non-timing
check but starts late inside its own SRT window is shifted as PCM and verified
again. Only defects that survive this deterministic repair reach synthesis.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

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
_legacy_verify_timeline_v45 = _legacy.verify_timeline_v45


def _pre_repair_report_path(report_path: Path) -> Path:
    return report_path.with_name(report_path.stem + ".pre_onset_repair.json")


def verify_timeline_v45(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    """Run independent QA, repair timing-only onsets, then verify once more."""
    failed, report = _legacy_verify_timeline_v45(timeline, segments, report_path)
    if not failed:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_repair_attempted"] = False
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    repairable = timeline_onset_repair.repairable_segment_ids(report)
    if not repairable:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
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
        report["timing_repair_attempted"] = True
        report["timing_repair"] = repair
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    repaired_failed, repaired_report = _legacy_verify_timeline_v45(
        timeline,
        segments,
        report_path,
    )
    repaired_report.update(
        timing_repair_policy=TIMING_REPAIR_POLICY,
        timing_repair_attempted=True,
        timing_repair=repair,
        pre_timing_repair_report=str(pre_repair_path),
        timing_repair_resolved_segment_ids=sorted(set(failed) - set(repaired_failed)),
        timing_repair_remaining_failed_segment_ids=repaired_failed,
    )
    report_path.write_text(
        json.dumps(repaired_report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return repaired_failed, repaired_report


_legacy.verify_timeline_v45 = verify_timeline_v45

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "TIMING_REPAIR_POLICY",
        "verify_timeline_v45",
    }
)
