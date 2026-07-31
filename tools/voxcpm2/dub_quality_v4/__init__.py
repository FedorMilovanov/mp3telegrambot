#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semantic-breath grouping facade for ready-SRT production.

The sibling module keeps established splitting/reference helpers. Its historical
ready-SRT policy merged only sub-1.15-second cues, leaving many 1–2 second calls
that inevitably sounded like separately acted inserts. This facade performs a
bounded dynamic-programming partition into natural 3–5 second breaths while
preserving every word and all outer timing anchors.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import re
import sys
import types
from typing import Any

from tools.voxcpm2 import russian_pronunciation

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_quality_v4.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._dub_quality_v4_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub quality helpers: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "ready-srt-semantic-breath-grouping-v1"
TARGET_SECONDS = 4.15
MIN_PREFERRED_SECONDS = 2.35
MAX_INTERNAL_GAP_SECONDS = 0.38
PREFERRED_INTERNAL_GAP_SECONDS = 0.24
MAX_WORDS_PER_SECOND = 5.45
_MAX_GROUP_SLACK = 0.04


def _normal_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _words(value: Any) -> int:
    return len(re.findall(r"\w+", _normal_text(value), flags=re.UNICODE))


def _sentence_end(value: Any) -> bool:
    return bool(re.search(r"[.!?…][\s\"'»”)]*$", _normal_text(value)))


def _protected_final_pronunciation(value: Any) -> bool:
    prepared = russian_pronunciation.prepare_segment({"text": _normal_text(value)})
    return bool(prepared.get("stress_evidence_required"))


def _atoms(cues: list[Any], *, max_seconds: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for cue_index, cue in enumerate(
        sorted(cues, key=lambda item: (float(item.start), float(item.end))),
        start=1,
    ):
        parts = _legacy._split_timed_text(
            float(cue.start),
            float(cue.end),
            str(cue.text or ""),
            max_seconds=float(max_seconds),
        )
        for part_index, part in enumerate(parts, start=1):
            text = _normal_text(part.text)
            if not text or float(part.end) <= float(part.start):
                continue
            result.append(
                {
                    "start": float(part.start),
                    "end": float(part.end),
                    "source": text,
                    "source_cue": cue_index,
                    "source_part": part_index,
                }
            )
    return result


def _candidate(
    atoms: list[dict[str, Any]],
    left: int,
    right: int,
    *,
    max_seconds: float,
) -> dict[str, Any] | None:
    selected = atoms[left:right + 1]
    start = float(selected[0]["start"])
    end = float(selected[-1]["end"])
    duration = end - start
    if duration <= 0.0 or duration > float(max_seconds) + _MAX_GROUP_SLACK:
        return None
    gaps = [
        max(0.0, float(selected[index + 1]["start"]) - float(selected[index]["end"]))
        for index in range(len(selected) - 1)
    ]
    if gaps and max(gaps) > MAX_INTERNAL_GAP_SECONDS:
        return None
    # A final-stress acoustic rule can verify only a final lexical word. Keep
    # that word at the end of its synthesis breath.
    if any(_protected_final_pronunciation(item["source"]) for item in selected[:-1]):
        return None

    text = _normal_text(" ".join(str(item["source"]) for item in selected))
    words = _words(text)
    rate = words / max(0.35, duration)
    if rate > MAX_WORDS_PER_SECOND:
        return None

    target = min(TARGET_SECONDS, max(1.6, float(max_seconds) - 0.35))
    cost = ((duration - target) / max(1.0, target)) ** 2
    if duration < MIN_PREFERRED_SECONDS:
        cost += ((MIN_PREFERRED_SECONDS - duration) * 1.55) ** 2
    if len(selected) == 1:
        cost += 0.22
        lowered = text.casefold().replace("ё", "е")
        if text.endswith(":") or "стих:" in lowered:
            cost += 2.8
        if "смеется" in lowered or "смеюсь" in lowered:
            cost += 1.4
    if gaps:
        cost += sum(
            max(0.0, gap - PREFERRED_INTERNAL_GAP_SECONDS) * 1.8
            for gap in gaps
        )
    if _sentence_end(text):
        cost -= 0.10
    else:
        cost += 0.14
    # Fewer independent model calls are preferred, but only weakly: timing and
    # semantic boundaries still dominate.
    cost += 0.035
    return {
        "start": start,
        "end": end,
        "source": text,
        "duration": duration,
        "word_rate": rate,
        "cost": cost,
        "source_cue_count": len({int(item["source_cue"]) for item in selected}),
        "source_parts": [
            {
                "cue": int(item["source_cue"]),
                "part": int(item["source_part"]),
                "start": float(item["start"]),
                "end": float(item["end"]),
                "text": str(item["source"]),
            }
            for item in selected
        ],
        "internal_gaps": gaps,
    }


def group_ready_srt_v4(cues: list[Any], *, max_seconds: float = 7.0) -> list[dict[str, Any]]:
    """Partition ready SRT into natural bounded breaths with exact text coverage."""
    limit = float(max_seconds)
    if not math.isfinite(limit) or limit < 0.35:
        raise RuntimeError("max_seconds для ready-SRT grouping некорректен.")
    atoms = _atoms(cues, max_seconds=limit)
    if not atoms:
        return []

    count = len(atoms)
    best_cost = [float("inf")] * (count + 1)
    best_next = [-1] * (count + 1)
    best_group: list[dict[str, Any] | None] = [None] * (count + 1)
    best_cost[count] = 0.0

    for left in range(count - 1, -1, -1):
        for right in range(left, count):
            candidate = _candidate(atoms, left, right, max_seconds=limit)
            if candidate is None:
                if float(atoms[right]["end"]) - float(atoms[left]["start"]) > limit + _MAX_GROUP_SLACK:
                    break
                continue
            total = float(candidate["cost"]) + best_cost[right + 1]
            if total < best_cost[left] - 1e-12:
                best_cost[left] = total
                best_next[left] = right + 1
                best_group[left] = candidate

    if best_next[0] < 0:
        raise RuntimeError("Не удалось построить физически допустимые semantic breaths из SRT.")

    groups: list[dict[str, Any]] = []
    cursor = 0
    while cursor < count:
        candidate = best_group[cursor]
        next_cursor = best_next[cursor]
        if not isinstance(candidate, dict) or next_cursor <= cursor:
            raise RuntimeError("Повреждён dynamic-programming plan ready-SRT grouping.")
        groups.append(
            {
                "id": len(groups) + 1,
                "start": round(float(candidate["start"]), 3),
                "end": round(float(candidate["end"]), 3),
                "source": str(candidate["source"]),
                "grouping_policy": POLICY,
                "source_cue_count": int(candidate["source_cue_count"]),
                "source_parts": candidate["source_parts"],
                "internal_gaps": [round(float(value), 6) for value in candidate["internal_gaps"]],
                "word_rate": round(float(candidate["word_rate"]), 6),
            }
        )
        cursor = next_cursor

    source_text = _normal_text(" ".join(str(item["source"]) for item in atoms))
    grouped_text = _normal_text(" ".join(str(item["source"]) for item in groups))
    if source_text != grouped_text:
        raise RuntimeError("Semantic-breath grouping изменил текст готового SRT.")
    if any(
        float(item["end"]) - float(item["start"]) > limit + _MAX_GROUP_SLACK + 1e-9
        for item in groups
    ):
        raise RuntimeError("Semantic-breath grouping создал слишком длинную реплику.")
    return groups


_legacy.group_ready_srt_v4 = group_ready_srt_v4


class _WriteThroughModule(types.ModuleType):
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
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "MAX_INTERNAL_GAP_SECONDS",
        "MAX_WORDS_PER_SECOND",
        "MIN_PREFERRED_SECONDS",
        "POLICY",
        "PREFERRED_INTERNAL_GAP_SECONDS",
        "TARGET_SECONDS",
        "group_ready_srt_v4",
    }
)
