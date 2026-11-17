"""Reviewed operator camera-shot plan for an OFFLINE solo SHORTOREZ export.

No shot tracking, no interpolated crop, no blurred backdrop, no publication.
A review record is an operator assertion, NOT independent proof of camera cuts.
The encoded artifact still requires independent beginning/middle/end + seam QA.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.async_process import run_cancellable_process

logger = logging.getLogger(__name__)
_SCHEMA = "mp3bot.shortorez-fixed-shot-plan"
_MAX_SHOTS = 128


@dataclass(frozen=True)
class ReviewedShot:
    start_ms: int
    end_ms: int
    crop_x: int
    camera_shot_id: str


@dataclass(frozen=True)
class ReviewedSoloPlan:
    crop_width: int
    source_height: int
    reviewer: str
    shots: tuple[ReviewedShot, ...]


def _exact_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"Shot plan {field} must be an integer")
    return value


def validate_reviewed_solo_plan(
    record: object,
    *,
    youtube_id: str,
    source_sha256: str,
    start_ms: int,
    end_ms: int,
    source_width: int,
    source_height: int,
) -> ReviewedSoloPlan:
    """Check exact source binding, full coverage and one fixed X per camera take."""
    if not isinstance(record, Mapping) or record.get("schema_name") != _SCHEMA or record.get("schema_version") != 1:
        raise ValueError("A version 1 reviewed fixed-camera-shot plan is required")
    identity: dict[str, object] = {
        "project_key": "lord-god-strength",
        "source_channel_id": "UCeSJsC6go2c9pdJCuUI1BYA",
        "source_video_id": youtube_id,
        "source_sha256": source_sha256,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "source_width": source_width,
        "source_height": source_height,
        "layout": "solo_static_crop_per_source_camera_shot",
    }
    for field, expected in identity.items():
        actual = record.get(field)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"Shot plan is not bound to reviewed source {field}")

    review = record.get("camera_cut_review")
    if not isinstance(review, Mapping) or review.get("status") != "original_camera_edits_reviewed":
        raise ValueError("Missing operator camera-cut review")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 180:
        raise ValueError("Camera-cut reviewer must be recorded")
    notes = review.get("method")
    if not isinstance(notes, str) or len(notes.strip()) < 12 or len(notes) > 500:
        raise ValueError("Camera-cut review method must be described")

    if (
        type(source_width) is not int
        or type(source_height) is not int
        or min(source_width, source_height) < 64
        or source_width % 2
        or source_height % 2
    ):
        raise ValueError("Square-pixel even source dimensions are required")
    # Closest even pixel width to the 9:16 source crop, within ~1 source pixel.
    crop_width = (source_height * 9 + 8) // 16
    crop_width += crop_width % 2
    if crop_width > source_width:
        raise ValueError("Source width is too narrow for a full-bleed 9:16 crop")
    raw_shots = record.get("shots")
    if not isinstance(raw_shots, list) or not 1 <= len(raw_shots) <= _MAX_SHOTS:
        raise ValueError("Shot plan requires 1–128 reviewed camera shots")

    cursor = start_ms
    shots: list[ReviewedShot] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_shots):
        if not isinstance(raw, Mapping) or set(raw) != {"start_ms", "end_ms", "crop_x", "camera_shot_id"}:
            raise ValueError(f"Shot {index} must contain exact time, crop X and camera ID fields")
        begin = _exact_int(raw["start_ms"], f"{index}.start_ms")
        end = _exact_int(raw["end_ms"], f"{index}.end_ms")
        x = _exact_int(raw["crop_x"], f"{index}.crop_x")
        shot_id = raw["camera_shot_id"]
        if not isinstance(shot_id, str) or not shot_id.strip() or len(shot_id) > 80 or shot_id in seen_ids:
            raise ValueError(f"Shot {index} needs a distinct reviewed camera ID")
        if begin != cursor or end <= begin or end > end_ms:
            raise ValueError(f"Shot {index} does not exactly cover the next source time window")
        if x < 0 or x % 2 or x + crop_width > source_width:
            raise ValueError(f"Shot {index} has invalid even fixed crop X for the source")
        shots.append(ReviewedShot(begin, end, x, shot_id))
        seen_ids.add(shot_id)
        cursor = end
    if cursor != end_ms:
        raise ValueError("Shot plan does not cover the entire heard Russian interval")
    return ReviewedSoloPlan(crop_width, source_height, reviewer.strip(), tuple(shots))


def build_solo_ffmpeg_command(source: Path, output: Path, plan: ReviewedSoloPlan, *, ffmpeg: str) -> list[str]:
    """One immutable crop per source shot; video cuts at source PTS, audio one continuous trim."""
    first, last = plan.shots[0].start_ms, plan.shots[-1].end_ms
    filters: list[str] = []
    for index, shot in enumerate(plan.shots):
        start = shot.start_ms / 1000
        end = shot.end_ms / 1000
        filters.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"crop={plan.crop_width}:{plan.source_height}:{shot.crop_x}:0,"
            f"scale=720:1280:flags=lanczos,setsar=1,format=yuv420p[v{index}]"
        )
    inputs = "".join(f"[v{index}]" for index in range(len(plan.shots)))
    filters.append(f"{inputs}concat=n={len(plan.shots)}:v=1:a=0[v]")
    # Audio is never trimmed or restarted at the camera edits. Do not use -shortest.
    filters.append(
        f"[0:a]atrim=start={first / 1000:.3f}:end={last / 1000:.3f},asetpts=PTS-STARTPTS[a]"
    )
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-i", str(source), "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output),
    ]


async def render_reviewed_solo_shots(source: Path, output: Path, plan: ReviewedSoloPlan) -> bool:
    """Cancellable process-tree-owned FFmpeg; no existing output is overwritten."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or output.exists():
        return False
    command = build_solo_ffmpeg_command(source, output, plan, ffmpeg=ffmpeg)
    process = await run_cancellable_process(command, timeout=1800, text=True)
    if process.returncode != 0:
        logger.warning("SHORTOREZ solo shot render failed: %s", (process.stderr or "")[-500:])
        return False
    return output.is_file() and output.stat().st_size > 0


__all__ = [
    "ReviewedShot", "ReviewedSoloPlan", "validate_reviewed_solo_plan",
    "build_solo_ffmpeg_command", "render_reviewed_solo_shots",
]
