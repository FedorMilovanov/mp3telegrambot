"""Operator-only VK native Clip variant from an existing SHORTOREZ review MP4.

Reuses the owned pitch-preserving postprocess_short video/audio transformer.
No Bot handlers, provider calls, scheduled jobs, public captions, or releases.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from services.media_delivery_probe import MediaProbe, media_probe_is_deliverable, probe_media_async
from services.shortorez_export import (
    GAP_MAX_MS_EXCLUSIVE,
    GAP_MIN_MS_EXCLUSIVE,
    PILOT_CHANNEL,
    PILOT_PROJECT,
    _sha256,
    _validate_request,
)

VK_NATIVE_CLIP_MAX_MS = 180_000
ALLOWED_SPEED_PERCENTS = (105, 110, 115, 120)
_MAX_DRIFT_MS = 1_500

Transformer = Callable[..., Awaitable[bool]]
Prober = Callable[[Path], Awaitable[MediaProbe | None]]


def select_vk_speed_percent(reviewed_duration_ms: int) -> int:
    """Match Video Manager's smallest-fitting native VK speed proposal.

    A 1.00x variant already fits VK if its duration <=180s and should be
    rendered by the existing normal-speed 9:16 pipeline, not this derivative.
    Nothing beyond 1.20x is allowed. No semantic trimming is attempted.
    """
    if type(reviewed_duration_ms) is not int or not (
        GAP_MIN_MS_EXCLUSIVE < reviewed_duration_ms < GAP_MAX_MS_EXCLUSIVE
    ):
        raise ValueError("VK derivative requires one reviewed Factory-gap interval")
    for percent in ALLOWED_SPEED_PERCENTS:
        projected_ms = (reviewed_duration_ms * 100 + percent - 1) // percent
        if projected_ms <= VK_NATIVE_CLIP_MAX_MS:
            return percent
    raise ValueError("This complete passage cannot fit a native VK Clip at <=1.20x; select another cut")


def _read_base_manifest(
    raw: Any,
    *,
    base_mp4: Path,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("Expected original SHORTOREZ review-only manifest v1")
    if raw.get("kind") != "shortorez_review_only":
        raise ValueError("VK derivative requires original SHORTOREZ review-only output")
    if raw.get("project_key") != PILOT_PROJECT or raw.get("source_channel_id") != PILOT_CHANNEL:
        raise ValueError("Source account/project identity does not match the pilot")
    video_id = raw.get("source_video_id")
    source_hash = raw.get("source_sha256")
    start_ms = raw.get("source_start_ms")
    end_ms = raw.get("source_end_ms")
    # Strict schema validation before type-sensitive provenance checks.
    if not isinstance(video_id, str) or not isinstance(source_hash, str):
        raise ValueError("Base manifest must contain exact string source identity and digest")
    if not isinstance(start_ms, int) or isinstance(start_ms, bool):
        raise ValueError("Base manifest start must be integer milliseconds")
    if not isinstance(end_ms, int) or isinstance(end_ms, bool):
        raise ValueError("Base manifest end must be integer milliseconds")
    _validate_request(video_id, source_hash, start_ms, end_ms)
    expected_base_file = (
        f"shortorez_{video_id}_{start_ms}-{end_ms}_{source_hash[7:19]}_vertical.mp4"
    )
    if (
        raw.get("source_kind") != "ru_postmix"
        or raw.get("full_video_url") != f"https://www.youtube.com/watch?v={video_id}"
        or base_mp4.name != expected_base_file
        or raw.get("output_file") != expected_base_file
        or raw.get("speed_percent") != 100
        or raw.get("render_mode") != "existing_full_frame_vertical"
        or raw.get("output_width") != 720
        or raw.get("output_height") != 1280
        or raw.get("provider_write_performed") is not False
        or raw.get("release_authorized") is not False
    ):
        raise ValueError("Base preview provenance or non-publication state does not match")
    review = raw.get("boundary_review")
    if not isinstance(review, Mapping):
        raise ValueError("Base preview lacks source-bound RU edge evidence")
    for key in ("reviewer", "first_heard_words", "last_heard_words"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError(f"Base preview has no recorded RU edge {key}")
    base_hash = raw.get("output_sha256")
    if not isinstance(base_hash, str):
        raise ValueError("Base MP4 has no SHA256 digest")
    # Use the existing exact SHA/YT validators; no second ad-hoc regex.
    _validate_request(video_id, base_hash, start_ms, end_ms)
    if type(raw.get("output_duration_ms")) is not int or raw["output_duration_ms"] <= 0:
        raise ValueError("Base preview has no measured media duration")
    return dict(raw)


async def export_vk_derivative(
    *,
    base_mp4: Path,
    base_manifest_json: Path,
    output_dir: Path,
    transform: Transformer | None = None,
    probe: Prober | None = None,
) -> tuple[Path, Path]:
    """Produce exactly one offline VK-speed review artifact, without uploads.

    The existing 1x base is preserved for TG/Instagram. The derived artifact
    always needs NEW encoded-audio listening and visual/subtitle review, and
    neither this command nor its manifest grants publishing authorization.
    """
    source = await asyncio.to_thread(Path(base_mp4).resolve, strict=True)
    if not await asyncio.to_thread(source.is_file) or source.suffix.lower() != ".mp4":
        raise ValueError("Expected an existing reviewed MP4")
    raw = json.loads(await asyncio.to_thread(Path(base_manifest_json).read_text, encoding="utf-8"))
    base = _read_base_manifest(raw, base_mp4=source)
    true_sha = await asyncio.to_thread(_sha256, source)
    if true_sha != base["output_sha256"]:
        raise ValueError("Base reviewed MP4 bytes differ from the recorded SHA256")

    source_duration_ms = base["source_end_ms"] - base["source_start_ms"]
    speed_percent = select_vk_speed_percent(source_duration_ms)
    actual_probe = probe or probe_media_async
    before = await actual_probe(source)
    if not media_probe_is_deliverable(before):
        raise ValueError("Base reviewed MP4 does not contain proven audio/video")
    assert before is not None
    if (before.width, before.height) != (720, 1280):
        raise ValueError("Base MP4 must remain 720x1280 9:16")
    before_ms = round(before.duration * 1000)
    if abs(before_ms - base["output_duration_ms"]) > _MAX_DRIFT_MS:
        raise ValueError("Base media probe differs from its recorded duration")
    if abs(before_ms - source_duration_ms) > _MAX_DRIFT_MS:
        raise ValueError("Base MP4 duration differs from source-reviewed complete interval")

    root = await asyncio.to_thread(Path(output_dir).resolve)
    name = (
        f"shortorez_vk_{base['source_video_id']}_{base['source_start_ms']}-"
        f"{base['source_end_ms']}_{speed_percent}p_{true_sha[7:19]}"
    )
    final_mp4 = root / f"{name}.mp4"
    final_json = root / f"{name}_handoff.json"
    if final_mp4 == source:
        raise ValueError("Refusing to overwrite original 1.00x base")
    if await asyncio.to_thread(final_mp4.exists) or await asyncio.to_thread(final_json.exists):
        raise FileExistsError("VK derivative already exists; inspect/verify receipt instead of replay")
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)

    if transform is None:
        # Existing, single owned FFmpeg implementation, with pitch-preserving atempo/setpts.
        from services.shorts_video import postprocess_short

        transform = postprocess_short

    with tempfile.TemporaryDirectory(prefix=".shortorez-vk-", dir=root) as temp:
        staged_mp4 = Path(temp) / "vk_speed.mp4"
        staged_json = Path(temp) / "vk_handoff.json"
        ok = await transform(
            source,
            staged_mp4,
            normalize_audio=False,
            speed=speed_percent / 100.0,
        )
        if not ok or not await asyncio.to_thread(staged_mp4.is_file):
            raise RuntimeError("Owned pitch-preserving transform failed")
        after = await actual_probe(staged_mp4)
        if not media_probe_is_deliverable(after):
            raise RuntimeError("VK output is missing video or audible media stream")
        assert after is not None
        if (after.width, after.height) != (720, 1280):
            raise RuntimeError("VK output lost the approved 9:16 base orientation")
        final_ms = round(after.duration * 1000)
        projected_base_ms = (before_ms * 100 + speed_percent - 1) // speed_percent
        projected_review_ms = (source_duration_ms * 100 + speed_percent - 1) // speed_percent
        if (
            final_ms > VK_NATIVE_CLIP_MAX_MS
            or final_ms < 3_000
            or abs(final_ms - projected_base_ms) > _MAX_DRIFT_MS
            or abs(final_ms - projected_review_ms) > _MAX_DRIFT_MS
        ):
            raise RuntimeError("Final encoded VK duration violates source-bound 180s contract")
        if await asyncio.to_thread(_sha256, source) != true_sha:
            raise RuntimeError("Original 1.00x base changed while producing the VK derivative")
        output_sha = await asyncio.to_thread(_sha256, staged_mp4)
        result: dict[str, Any] = {
            "schema_version": 1,
            "kind": "shortorez_vk_speed_review_only",
            "project_key": PILOT_PROJECT,
            "source_channel_id": PILOT_CHANNEL,
            "source_video_id": base["source_video_id"],
            "full_video_url": base["full_video_url"],
            "source_kind": "ru_postmix",
            "source_master_sha256": base["source_sha256"],
            "source_start_ms": base["source_start_ms"],
            "source_end_ms": base["source_end_ms"],
            "base_mp4_file": source.name,
            "base_sha256": true_sha,
            "base_duration_ms": before_ms,
            "speed_percent": speed_percent,
            "projected_review_duration_ms": projected_review_ms,
            "output_file": final_mp4.name,
            "output_sha256": output_sha,
            "output_duration_ms": final_ms,
            "output_width": after.width,
            "output_height": after.height,
            "render_mode": "existing_short_postprocess_atempo_setpts",
            "preserves_audio_pitch_by_filter": True,
            "subtitles_reviewed": False,
            "final_encoded_audio_listened": False,
            "visual_safe_zones_reviewed": False,
            "rights_cleared": False,
            "release_authorized": False,
            "provider_write_performed": False,
        }
        await asyncio.to_thread(
            staged_json.write_text,
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # Two no-overwrite hardlinks in one directory. If the second fails,
        # roll back our media link; never truncate or replace a prior artifact.
        await asyncio.to_thread(os.link, staged_mp4, final_mp4)
        try:
            await asyncio.to_thread(os.link, staged_json, final_json)
        except BaseException:
            await asyncio.to_thread(final_mp4.unlink, missing_ok=True)
            raise
    return final_mp4, final_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SHORTOREZ offline VK native Clip speed derivation, never publish")
    parser.add_argument("--base-mp4", required=True, type=Path)
    parser.add_argument("--base-handoff-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    mp4, manifest = asyncio.run(
        export_vk_derivative(
            base_mp4=args.base_mp4,
            base_manifest_json=args.base_handoff_json,
            output_dir=args.output_dir,
        )
    )
    print(
        json.dumps(
            {
                "status": "vk_speed_preview_for_review",
                "media_path": str(mp4),
                "manifest_path": str(manifest),
                "provider_write_performed": False,
                "release_authorized": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
