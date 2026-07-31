#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Candidate-stage facade for repairable short continuations.

The sibling module keeps speaker, pitch, source-prosody, cadence and late-tail
checks. This facade changes only one structural decision: a syntactically linked
short cue may reach timeline assembly, where bounded gap compaction and the final
assembled QA can verify it. Wrong endings, excessive fit tempo and noise remain
hard failures.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_source_prosody.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_source_prosody_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить source-prosody ranking: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

CANDIDATE_CONTINUATION_POLICY = "defer-short-continuation-to-timeline-v1"
_legacy_evaluate_candidate_cadence = _legacy.evaluate_candidate_cadence


def _defer_short_continuation(result: dict[str, Any]) -> dict[str, Any]:
    revised = dict(result)
    cadence = str(revised.get("cadence") or "")
    failures = list(revised.get("failures") or [])
    if cadence not in {"continuation", "linked"}:
        return revised
    if "continuation_too_short" not in failures:
        return revised

    remaining = [item for item in failures if item != "continuation_too_short"]
    revised.update(
        failures=remaining,
        hard_ok=not remaining,
        timeline_compaction_required=True,
        candidate_continuation_policy=CANDIDATE_CONTINUATION_POLICY,
        deferred_candidate_failure="continuation_too_short",
    )
    return revised


def evaluate_candidate_cadence(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Defer only the repairable duration-ratio failure to assembled QA."""
    return _defer_short_continuation(
        dict(_legacy_evaluate_candidate_cadence(candidate, segment))
    )


_legacy.evaluate_candidate_cadence = evaluate_candidate_cadence
source_prosody_penalty = _legacy.source_prosody_penalty
candidate_pitch_evidence_ok = _legacy.candidate_pitch_evidence_ok

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "CANDIDATE_CONTINUATION_POLICY",
        "_defer_short_continuation",
        "candidate_pitch_evidence_ok",
        "evaluate_candidate_cadence",
        "source_prosody_penalty",
    }
)
