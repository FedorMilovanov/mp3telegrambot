#!/usr/bin/env python3
"""Repeatable FFmpeg evidence for generic Montage/normalize video-copy policy.

This is an explicit media verifier, not a normal pytest dependency. It requires
system FFmpeg/FFprobe, generates deterministic local media, executes the real
Montage renderer, and proves compressed video packets are preserved across the
final concat and normalize-only stages.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import services.render_clips_montage as render_clips
import services.shorts_video as shorts_video


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _video_hash(ffmpeg: str, media_path: Path) -> str:
    result = _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(media_path),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_mp4toannexb",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    )
    return result.stdout.strip()


def _concat_video_hash(ffmpeg: str, concat_list: Path) -> str:
    result = _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_mp4toannexb",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    )
    return result.stdout.strip()


def _probe_video(ffprobe: str, media_path: Path) -> dict[str, object]:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media_path),
        ]
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float(fmt.get("duration") or 0.0),
    }


async def _verify(ffmpeg: str, ffprobe: str, workdir: Path) -> dict[str, object]:
    source = workdir / "source.mp4"
    montage = workdir / "montage.mp4"
    normalized = workdir / "normalized.mp4"

    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-y",
            str(source),
        ]
    )

    original_encoder = render_clips._get_video_encoder
    original_static = render_clips._is_static_video
    original_cleanup = render_clips._unlink_render_paths

    async def _not_static(*_args, **_kwargs):
        return False

    render_clips._get_video_encoder = lambda: (
        "libx264",
        ["-crf", "23"],
        ["-preset", "veryfast"],
    )
    render_clips._is_static_video = _not_static
    render_clips._unlink_render_paths = lambda *_paths: None
    try:
        rendered = await render_clips.render_montage_short(
            source,
            montage,
            [
                {"start_seconds": 0.0, "end_seconds": 0.6},
                {"start_seconds": 1.0, "end_seconds": 1.6},
                {"start_seconds": 2.0, "end_seconds": 2.6},
            ],
            visual_mode="crop_zoom",
        )
    finally:
        render_clips._get_video_encoder = original_encoder
        render_clips._is_static_video = original_static
        render_clips._unlink_render_paths = original_cleanup

    if not rendered:
        raise RuntimeError("real Montage render failed")

    concat_list = workdir / "montage_concat.txt"
    parts = [workdir / f"montage_part{index}.mp4" for index in range(3)]
    if not concat_list.exists() or not all(path.exists() for path in parts):
        raise RuntimeError("evidence parts were not preserved")

    # The concat demuxer exposes H.264 in Annex-B form while MP4 stores length-
    # prefixed NAL units. Canonicalize both to Annex-B before hashing so the
    # comparison proves compressed video-packet identity rather than container
    # representation identity.
    concat_source_hash = _concat_video_hash(ffmpeg, concat_list)
    montage_hash = _video_hash(ffmpeg, montage)
    concat_copy_preserved = concat_source_hash == montage_hash
    if not concat_copy_preserved:
        raise RuntimeError("final Montage concat changed compressed video packets")

    normalized_ok = await shorts_video._normalize_audio_copy_video(montage, normalized)
    if not normalized_ok:
        raise RuntimeError("normalize-only video-copy stage failed")
    normalized_hash = _video_hash(ffmpeg, normalized)
    normalize_copy_preserved = montage_hash == normalized_hash
    if not normalize_copy_preserved:
        raise RuntimeError("normalize-only stage changed compressed video packets")

    probe = _probe_video(ffprobe, montage)
    if probe["codec"] != "h264" or probe["width"] != 720 or probe["height"] != 1280:
        raise RuntimeError(f"unexpected Montage media profile: {probe!r}")

    return {
        "source": "deterministic FFmpeg testsrc2 640x360/24 + AAC",
        "montage_codec": probe["codec"],
        "montage_resolution": [probe["width"], probe["height"]],
        "montage_duration": round(float(probe["duration"]), 3),
        "fragment_count": len(parts),
        "final_concat_video_stream_copy": concat_copy_preserved,
        "normalize_video_stream_copy": normalize_copy_preserved,
        "video_hash": montage_hash,
        "pre_subtitle_video_generations_old": 3,
        "pre_subtitle_video_generations_new": 1,
    }


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("FFmpeg and FFprobe are required for this evidence run")

    with tempfile.TemporaryDirectory(prefix="generic-video-copy-evidence-") as temp_dir:
        report = asyncio.run(_verify(ffmpeg, ffprobe, Path(temp_dir)))
    print("GENERIC_VIDEO_COPY_EVIDENCE=" + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
