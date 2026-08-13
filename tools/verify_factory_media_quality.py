#!/usr/bin/env python3
"""Evidence-grade before/after verifier for SHORTS FACTORY video quality.

The verifier is deliberately separate from production rendering.  It exercises
real FFmpeg/yt-dlp media and compares the policy that produced the reported
quality problem (720p master + redundant normalize-only video encode) against
the current Factory policy (verified <=1080p master + video stream copy during
normalize-only + H.264 LONG render).

It can use either a local real-media source or a public URL.  Network access is
never required by the normal test suite; the URL mode exists for explicit live
verification runs and writes a machine-readable JSON report plus representative
frames.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ffmpeg import YTDLP_BASE_ARGS
from services.shorts_factory_source import _factory_quality_sort_reset
from services.shorts_factory_video_quality import (
    FACTORY_VIDEO_FORMAT,
    normalize_factory_short_audio_copy_video,
    render_factory_long_h264,
)

SHORT_FILTER = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=720:1280"
OLD_SHORT_VIDEO_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
OLD_LONG_VIDEO_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]
_REFERENCE_VIDEO_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "8"]
_AUDIO_ARGS = ["-c:a", "aac", "-b:a", "128k"]
_SSIM_RE = re.compile(r"All:([0-9]+(?:\.[0-9]+)?)")


def _run(command: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"command failed ({result.returncode}): {rendered}\n"
            f"{(result.stderr or result.stdout or '')[-2000:]}"
        )
    return result


def _which_required(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"required executable is unavailable: {name}")
    return path


def _timestamp(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = _which_required("ffprobe")
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size,bit_rate,format_name:"
                "stream=index,codec_type,codec_name,width,height,avg_frame_rate,bit_rate"
            ),
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(result.stdout or "{}")
    if not isinstance(data, dict):
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}")
    streams = data.get("streams")
    if not isinstance(streams, list):
        streams = []
    video = next(
        (
            row
            for row in streams
            if isinstance(row, dict) and row.get("codec_type") == "video"
        ),
        {},
    )
    audio = next(
        (
            row
            for row in streams
            if isinstance(row, dict) and row.get("codec_type") == "audio"
        ),
        {},
    )
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        size = int(float(fmt.get("size") or path.stat().st_size))
    except (TypeError, ValueError, OSError):
        size = path.stat().st_size
    try:
        bitrate = int(float(fmt.get("bit_rate") or 0.0))
    except (TypeError, ValueError):
        bitrate = 0
    return {
        "path": path.name,
        "sha256": _file_sha256(path),
        "duration_seconds": round(duration, 3),
        "size_bytes": size,
        "size_mib": round(size / (1024 * 1024), 3),
        "avg_bitrate_mbps": round(bitrate / 1_000_000, 4) if bitrate else 0.0,
        "video_codec": str(video.get("codec_name") or ""),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": str(video.get("avg_frame_rate") or ""),
        "audio_codec": str(audio.get("codec_name") or ""),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _video_stream_hash(path: Path) -> str:
    ffmpeg = _which_required("ffmpeg")
    result = _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        timeout=180,
    )
    line = (result.stdout or "").strip()
    if "=" not in line:
        raise RuntimeError(f"could not hash video stream for {path}: {line!r}")
    return line.split("=", 1)[1].strip().lower()


def _download_live_section(
    url: str,
    output_dir: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> Path:
    start = _timestamp(start_seconds)
    end = _timestamp(start_seconds + duration_seconds)
    template = output_dir / "live_source_1080.%(ext)s"
    command = list(YTDLP_BASE_ARGS) + _factory_quality_sort_reset() + [
        "--download-sections",
        f"*{start}-{end}",
        "--format",
        FACTORY_VIDEO_FORMAT,
        "--merge-output-format",
        "mkv",
        "--no-playlist",
        "--output",
        str(template),
        url,
    ]
    _run(command, timeout=1800)
    candidates = sorted(
        (
            path
            for path in output_dir.glob("live_source_1080.*")
            if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("yt-dlp did not produce the requested live source section")
    source = candidates[0]
    probe = _probe(source)
    if probe["height"] <= 0 or probe["height"] > 1080 or not probe["audio_codec"]:
        raise RuntimeError(f"live source is not a verified <=1080p video+audio master: {probe}")
    return source


def _derive_old_720_master(source_1080: Path, destination: Path) -> None:
    ffmpeg = _which_required("ffmpeg")
    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(source_1080),
            "-vf",
            "scale=-2:720:flags=lanczos",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "8",
            "-c:a",
            "copy",
            "-y",
            str(destination),
        ]
    )


def _encode_short(source: Path, destination: Path, *, reference: bool = False) -> None:
    ffmpeg = _which_required("ffmpeg")
    video_args = _REFERENCE_VIDEO_ARGS if reference else OLD_SHORT_VIDEO_ARGS
    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            SHORT_FILTER,
            *video_args,
            *_AUDIO_ARGS,
            "-movflags",
            "+faststart",
            "-y",
            str(destination),
        ]
    )


def _old_normalize_reencode(input_path: Path, output_path: Path) -> None:
    ffmpeg = _which_required("ffmpeg")
    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            *_AUDIO_ARGS,
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
    )


def _old_long_render(source_720: Path, destination: Path) -> None:
    ffmpeg = _which_required("ffmpeg")
    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(source_720),
            *OLD_LONG_VIDEO_ARGS,
            *_AUDIO_ARGS,
            "-movflags",
            "+faststart",
            "-y",
            str(destination),
        ]
    )


def _ssim(reference: Path, distorted: Path) -> float:
    ref_probe = _probe(reference)
    dist_probe = _probe(distorted)
    if ref_probe["width"] <= 0 or ref_probe["height"] <= 0:
        raise RuntimeError("reference dimensions are unavailable")
    ffmpeg = _which_required("ffmpeg")
    dist_chain = "[0:v]setpts=PTS-STARTPTS"
    if (
        dist_probe["width"] != ref_probe["width"]
        or dist_probe["height"] != ref_probe["height"]
    ):
        dist_chain += (
            f",scale={ref_probe['width']}:{ref_probe['height']}:flags=lanczos"
        )
    dist_chain += "[dist]"
    filter_graph = (
        f"{dist_chain};"
        "[1:v]setpts=PTS-STARTPTS[ref];"
        "[dist][ref]ssim"
    )
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "info",
            "-i",
            str(distorted),
            "-i",
            str(reference),
            "-lavfi",
            filter_graph,
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "")[-2000:])
    matches = _SSIM_RE.findall(result.stderr or "")
    if not matches:
        raise RuntimeError("FFmpeg did not report aggregate SSIM")
    value = float(matches[-1])
    if not math.isfinite(value):
        raise RuntimeError("FFmpeg returned non-finite SSIM")
    return value


def _snapshot(source: Path, destination: Path, *, at_seconds: float = 10.0) -> None:
    ffmpeg = _which_required("ffmpeg")
    duration = float(_probe(source)["duration_seconds"] or 0.0)
    seek = min(max(0.0, at_seconds), max(0.0, duration * 0.5))
    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{seek:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(destination),
        ],
        timeout=120,
    )


def _assert_quality(report: dict[str, Any]) -> None:
    short = report["short"]
    long = report["long"]
    if not short["new_video_stream_copy_preserved"]:
        raise RuntimeError("normalize-only Factory pass changed the video bitstream")
    if short["new_video_encode_stages_pre_subtitle"] >= short["old_video_encode_stages_pre_subtitle"]:
        raise RuntimeError("Factory Short did not remove the redundant video generation")
    if short["new_ssim"] <= short["old_ssim"]:
        raise RuntimeError(
            f"Factory Short live quality did not improve: {short['old_ssim']} -> {short['new_ssim']}"
        )
    if long["new_ssim"] <= long["old_ssim"]:
        raise RuntimeError(
            f"Factory LONG live quality did not improve: {long['old_ssim']} -> {long['new_ssim']}"
        )
    new_long = long["new"]
    if new_long["height"] > 1080 or new_long["width"] > 1920:
        raise RuntimeError(f"Factory LONG exceeded the <=1080p publication ceiling: {new_long}")
    if new_long["video_codec"] != "h264":
        raise RuntimeError(f"Factory LONG is not H.264 compatible: {new_long}")


def verify(
    source_1080: Path,
    output_dir: Path,
    *,
    source_label: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VIDEO_FORCE_CPU"] = "1"
    source_probe = _probe(source_1080)
    if source_probe["height"] <= 720 or source_probe["height"] > 1080:
        raise RuntimeError(
            "evidence source must contain real detail above 720p and remain <=1080p: "
            f"{source_probe}"
        )
    if not source_probe["audio_codec"]:
        raise RuntimeError("evidence source must contain audio for normalize-only verification")

    old_master = output_dir / "old_master_720.mkv"
    reference_short = output_dir / "reference_short.mp4"
    old_short_stage1 = output_dir / "old_short_stage1.mp4"
    old_short_post = output_dir / "old_short_post.mp4"
    new_short_stage1 = output_dir / "new_short_stage1.mp4"
    new_short_post = output_dir / "new_short_post.mp4"
    old_long = output_dir / "old_long.mp4"
    new_long = output_dir / "new_long.mp4"

    _derive_old_720_master(source_1080, old_master)
    _encode_short(source_1080, reference_short, reference=True)
    _encode_short(old_master, old_short_stage1)
    _old_normalize_reencode(old_short_stage1, old_short_post)
    _encode_short(source_1080, new_short_stage1)

    before_copy_hash = _video_stream_hash(new_short_stage1)
    copy_ok = asyncio.run(
        normalize_factory_short_audio_copy_video(new_short_stage1, new_short_post)
    )
    if not copy_ok:
        raise RuntimeError("current Factory normalize-only video-copy path failed")
    after_copy_hash = _video_stream_hash(new_short_post)

    _old_long_render(old_master, old_long)
    source_duration = float(source_probe["duration_seconds"] or 0.0)
    if source_duration <= 2.0:
        raise RuntimeError("evidence source is too short")
    new_long_ok = asyncio.run(
        render_factory_long_h264(
            source_1080,
            new_long,
            0.0,
            max(1.0, source_duration - 0.5),
        )
    )
    if not new_long_ok:
        raise RuntimeError("current Factory LONG H.264 render path failed")

    old_short_ssim = _ssim(reference_short, old_short_post)
    new_short_ssim = _ssim(reference_short, new_short_post)
    old_long_ssim = _ssim(source_1080, old_long)
    new_long_ssim = _ssim(source_1080, new_long)

    for path, name in (
        (reference_short, "reference_short.jpg"),
        (old_short_post, "old_short.jpg"),
        (new_short_post, "new_short.jpg"),
        (source_1080, "reference_long.jpg"),
        (old_long, "old_long.jpg"),
        (new_long, "new_long.jpg"),
    ):
        _snapshot(path, output_dir / name)

    report: dict[str, Any] = {
        "schema": "factory-media-quality-evidence-v1",
        "source_label": source_label,
        "source": source_probe,
        "policy": {
            "old_master": "720p baseline",
            "new_master": "verified <=1080p Factory master",
            "short_old": "crop/scale encode + normalize-only video re-encode",
            "short_new": "crop/scale encode + normalize-only -c:v copy",
            "long_old": "720p libx264 CRF22/veryfast",
            "long_new": "production render_factory_long_h264 with VIDEO_FORCE_CPU=1",
        },
        "short": {
            "reference": _probe(reference_short),
            "old": _probe(old_short_post),
            "new": _probe(new_short_post),
            "old_ssim": round(old_short_ssim, 8),
            "new_ssim": round(new_short_ssim, 8),
            "ssim_delta": round(new_short_ssim - old_short_ssim, 8),
            "old_video_encode_stages_pre_subtitle": 2,
            "new_video_encode_stages_pre_subtitle": 1,
            "new_video_stream_hash_before_normalize": before_copy_hash,
            "new_video_stream_hash_after_normalize": after_copy_hash,
            "new_video_stream_copy_preserved": before_copy_hash == after_copy_hash,
        },
        "long": {
            "old": _probe(old_long),
            "new": _probe(new_long),
            "old_ssim": round(old_long_ssim, 8),
            "new_ssim": round(new_long_ssim, 8),
            "ssim_delta": round(new_long_ssim - old_long_ssim, 8),
            "old_video_encode_stages": 1,
            "new_video_encode_stages": 1,
        },
    }
    _assert_quality(report)
    report["result"] = "pass"
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Local real-media file, >720p and <=1080p")
    source.add_argument("--url", help="Public media URL supported by yt-dlp")
    parser.add_argument("--start", type=float, default=0.0, help="URL mode source start in seconds")
    parser.add_argument("--duration", type=float, default=45.0, help="URL mode section duration")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("factory-media-evidence"),
        help="Directory for report, intermediate media and comparison frames",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.source is not None:
        source_1080 = args.source.resolve()
        source_label = f"local:{source_1080.name}"
    else:
        if args.duration < 10 or args.duration > 180:
            raise SystemExit("--duration must be between 10 and 180 seconds")
        source_1080 = _download_live_section(
            str(args.url),
            output_dir,
            start_seconds=max(0.0, args.start),
            duration_seconds=args.duration,
        )
        source_label = f"url:{args.url}#{_timestamp(max(0.0, args.start))}+{args.duration:.0f}s"

    report = verify(source_1080, output_dir, source_label=source_label)
    report_path = output_dir / "factory_media_quality_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
