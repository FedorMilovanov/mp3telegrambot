#!/usr/bin/env python3
# -*- coding: utf-8
"""Semantic block planning for direct ready-SRT monologue rendering.

The input SRT remains the subtitle authority, while synthesis is planned in
longer, contiguous meaning/breath blocks. Each block is rendered as one complete
candidate; original cue boundaries are restored only for subtitle delivery.
"""
from __future__ import annotations

from typing import Any

from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import source_prosody_policy

POLICY = "semantic-block-continuation-v1"
CONTINUATION_POLICY = "previous-block-prompt-with-fixed-anchor-v1"
MIN_BLOCK_SECONDS = 7.0
TARGET_BLOCK_SECONDS = 10.5
MAX_BLOCK_SECONDS = 15.0
MAX_INTERNAL_GAP_SECONDS = 1.20


def _cue_midpoint(cue: Any) -> float:
    return (float(cue.start) + float(cue.end)) * 0.5


def _attach_source_cues(
    groups: list[dict[str, Any]],
    cues: list[Any],
) -> list[dict[str, Any]]:
    """Attach each original cue to exactly one micro-group by midpoint."""
    result = [dict(group) for group in groups]
    for cue in cues:
        midpoint = _cue_midpoint(cue)
        eligible = [
            (index, group)
            for index, group in enumerate(result)
            if float(group["start"]) - 0.01 <= midpoint <= float(group["end"]) + 0.01
        ]
        if not eligible:
            index = min(
                range(len(result)),
                key=lambda position: abs(
                    midpoint - (float(result[position]["start"]) + float(result[position]["end"])) * 0.5
                ),
            )
        else:
            index = eligible[0][0]
        result[index].setdefault("source_cues", []).append(cue)
    return result


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    gaps = [*(left.get("internal_gaps") or [])]
    gap = max(0.0, float(right["start"]) - float(left["end"]))
    gaps.append(round(gap, 6))
    gaps.extend(right.get("internal_gaps") or [])
    source_cues = [*(left.get("source_cues") or []), *(right.get("source_cues") or [])]
    source_parts = [*(left.get("source_parts") or []), *(right.get("source_parts") or [])]
    if not source_parts:
        source_parts = [str(left.get("source") or ""), str(right.get("source") or "")]
    return {
        "id": left.get("id"),
        "start": round(float(left["start"]), 3),
        "end": round(float(right["end"]), 3),
        "source": " ".join(
            value.strip() for value in (str(left.get("source") or ""), str(right.get("source") or "")) if value.strip()
        ),
        "source_parts": source_parts,
        "source_cues": source_cues,
        "source_cue_count": len(source_cues),
        "internal_gaps": gaps,
        "grouping_policy": str(left.get("grouping_policy") or ""),
        "semantic_block_policy": POLICY,
    }


def group_ready_srt(cues: list[Any]) -> list[dict[str, Any]]:
    """Build 7–15 second blocks without changing the authored SRT text."""
    micro = dub_quality_v4.group_ready_srt_v4(cues, max_seconds=5.4)
    if not micro:
        return []
    current_groups = _attach_source_cues(micro, cues)
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current["semantic_block_policy"] = POLICY
        current["source_cue_count"] = len(current.get("source_cues") or [])
        blocks.append(current)
        current = None

    for group in current_groups:
        if current is None:
            current = dict(group)
            continue
        joined_duration = float(group["end"]) - float(current["start"])
        gap = max(0.0, float(group["start"]) - float(current["end"]))
        current_duration = float(current["end"]) - float(current["start"])
        remaining_after_current = float(current_groups[-1]["end"]) - float(group["start"])
        should_balance = (
            current_duration >= MIN_BLOCK_SECONDS
            and joined_duration > TARGET_BLOCK_SECONDS
            and remaining_after_current >= MIN_BLOCK_SECONDS
        )
        if (
            joined_duration > MAX_BLOCK_SECONDS + 1e-9
            or gap > MAX_INTERNAL_GAP_SECONDS
            or should_balance
        ):
            flush()
            current = dict(group)
        else:
            current = _merge(current, group)
    flush()

    if len(blocks) >= 2:
        tail = blocks[-1]
        previous = blocks[-2]
        combined = float(tail["end"]) - float(previous["start"])
        gap = max(0.0, float(tail["start"]) - float(previous["end"]))
        if (
            float(tail["end"]) - float(tail["start"]) < MIN_BLOCK_SECONDS
            and combined <= MAX_BLOCK_SECONDS
            and gap <= MAX_INTERNAL_GAP_SECONDS
        ):
            blocks[-2] = _merge(previous, tail)
            blocks.pop()

    for index, block in enumerate(blocks, start=1):
        block["id"] = index
        block["semantic_block_id"] = index
        block["semantic_block_duration"] = round(float(block["end"]) - float(block["start"]), 6)
        block["semantic_block_policy"] = POLICY
        block["source_cue_count"] = len(block.get("source_cues") or [])
        if float(block["end"]) - float(block["start"]) > MAX_BLOCK_SECONDS + 1e-9:
            raise RuntimeError("Semantic block exceeded the hard duration ceiling.")
    return blocks


def _subtitle_cues(
    blocks: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> list[pipeline.Cue]:
    delay = max(0, int(delay_ms)) / 1000.0
    result: list[pipeline.Cue] = []
    for block in blocks:
        for cue in block.get("source_cues") or []:
            start = min(float(duration), max(0.0, float(cue.start) + delay))
            end = min(float(duration), max(start + 0.05, float(cue.end) + delay))
            result.append(pipeline.Cue(start, end, str(cue.text or "").strip()))
    return sorted(result, key=lambda cue: (float(cue.start), float(cue.end)))


def build_direct_segments(
    blocks: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    """Render one TTS unit per semantic block, but retain cue-level subtitles."""
    render_blocks = [
        {
            **block,
            # One stable calm enrollment for every production block. Expressive
            # reference construction remains available for experiments, but it
            # must not change the speaker identity between blocks.
            "reference_profile": "extended",
        }
        for block in blocks
    ]
    segments, _block_subtitles = professional_audio_v45.build_direct_segments_v45(
        render_blocks,
        delay_ms=delay_ms,
        duration=duration,
    )
    for segment, block in zip(segments, blocks, strict=True):
        segment.update(
            reference_profile="extended",
            semantic_block_policy=POLICY,
            source_prosody_role=source_prosody_policy.DIAGNOSTIC_ONLY_ROLE,
            semantic_block_id=int(block["semantic_block_id"]),
            source_cue_count=int(block.get("source_cue_count") or 0),
            semantic_block_duration=float(block["semantic_block_duration"]),
            source_parts=list(block.get("source_parts") or []),
        )
    return segments, _subtitle_cues(blocks, delay_ms=delay_ms, duration=duration)


__all__ = [
    "CONTINUATION_POLICY",
    "MAX_BLOCK_SECONDS",
    "MAX_INTERNAL_GAP_SECONDS",
    "MIN_BLOCK_SECONDS",
    "POLICY",
    "TARGET_BLOCK_SECONDS",
    "build_direct_segments",
    "group_ready_srt",
]
