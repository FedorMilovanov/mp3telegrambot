#!/usr/bin/env python3
"""Run the deterministic full-FFmpeg Shorts Factory before/after benchmark.

This is the CI-safe companion to ``verify_factory_media_quality.py``. It creates
an 8-second 1920x1080 high-detail moving source with deterministic audio, then
runs the same evidence verifier that can also be pointed at operator-provided
real media. No provider/network dependency is involved, so a benchmark failure
means the media policy or local FFmpeg surface changed rather than YouTube/Vimeo
availability changing.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from tools.verify_factory_media_quality import verify


def _generate_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=8",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "8",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-y",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout or "")[-3000:])
    if not path.is_file() or path.stat().st_size <= 1024:
        raise RuntimeError("benchmark source was not created")


def _compact(report: dict) -> dict:
    return {
        "source": "deterministic FFmpeg testsrc2 1920x1080/30 + AAC",
        "source_resolution": [report["source"]["width"], report["source"]["height"]],
        "short_old_ssim": report["short"]["old_ssim"],
        "short_new_ssim": report["short"]["new_ssim"],
        "short_ssim_delta": report["short"]["ssim_delta"],
        "short_old_mib": report["short"]["old"]["size_mib"],
        "short_new_mib": report["short"]["new"]["size_mib"],
        "short_stream_copy": report["short"]["new_video_stream_copy_preserved"],
        "short_old_encode_stages": report["short"]["old_video_encode_stages_pre_subtitle"],
        "short_new_encode_stages": report["short"]["new_video_encode_stages_pre_subtitle"],
        "long_old_ssim": report["long"]["old_ssim"],
        "long_new_ssim": report["long"]["new_ssim"],
        "long_ssim_delta": report["long"]["ssim_delta"],
        "long_old_mib": report["long"]["old"]["size_mib"],
        "long_new_mib": report["long"]["new"]["size_mib"],
        "long_old_mbps": report["long"]["old"]["avg_bitrate_mbps"],
        "long_new_mbps": report["long"]["new"]["avg_bitrate_mbps"],
        "long_new_resolution": [
            report["long"]["new"]["width"],
            report["long"]["new"]["height"],
        ],
        "long_new_codec": report["long"]["new"]["video_codec"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("factory-media-evidence"),
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["VIDEO_FORCE_CPU"] = "1"
    source = output_dir / "benchmark_source_1080.mp4"
    _generate_source(source)
    report = verify(
        source,
        output_dir,
        source_label="deterministic:testsrc2-1920x1080-30fps-8s",
    )
    report_path = output_dir / "factory_media_quality_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    compact = _compact(report)
    print("FACTORY_FULL_FFMPEG_EVIDENCE=" + json.dumps(compact, ensure_ascii=False, sort_keys=True))
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
