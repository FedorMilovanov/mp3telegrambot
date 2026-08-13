from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_RUN_BENCHMARK = (
    os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    and os.name != "nt"
    and sys.version_info[:2] == (3, 11)
)


@pytest.mark.skipif(
    not _RUN_BENCHMARK,
    reason="one-shot full FFmpeg quality benchmark runs only on GitHub Python 3.11",
)
def test_factory_full_ffmpeg_before_after_benchmark(tmp_path):
    """One-shot objective video-generation benchmark; remove after evidence is recorded."""
    source = tmp_path / "high_detail_1080.mp4"
    generate = subprocess.run(
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
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert generate.returncode == 0, (generate.stderr or generate.stdout)[-5000:]

    output = tmp_path / "factory-media-evidence"
    process = subprocess.run(
        [
            sys.executable,
            "tools/verify_factory_media_quality.py",
            "--source",
            str(source),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=2100,
        env={**os.environ, "VIDEO_FORCE_CPU": "1"},
    )
    assert process.returncode == 0, (process.stderr or process.stdout)[-5000:]

    report_path = output / "factory_media_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["result"] == "pass"

    compact = {
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
    rendered = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    print(f"FACTORY_FULL_FFMPEG_EVIDENCE={rendered}")

    summary = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("\n## Factory full FFmpeg before/after evidence\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(compact, ensure_ascii=False, indent=2))
            handle.write("\n```\n")
