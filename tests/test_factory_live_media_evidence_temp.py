from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_RUN_LIVE = (
    os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    and os.name != "nt"
    and sys.version_info[:2] == (3, 11)
)


@pytest.mark.skipif(not _RUN_LIVE, reason="one-shot real-media evidence runs only on GitHub Python 3.11")
def test_factory_live_real_sermon_before_after(tmp_path):
    """One-shot evidence gate; remove after the recorded CI run succeeds."""
    output = tmp_path / "factory-media-evidence"
    video_url = "https://vimeo.com/" + "350667440"
    command = [
        sys.executable,
        "tools/verify_factory_media_quality.py",
        "--url",
        video_url,
        "--start",
        "600",
        "--duration",
        "45",
        "--output-dir",
        str(output),
    ]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=2100,
        env={**os.environ, "VIDEO_FORCE_CPU": "1", "YTDLP_FRAGMENTS": "2"},
    )
    assert process.returncode == 0, (process.stderr or process.stdout)[-5000:]

    report_path = output / "factory_media_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["result"] == "pass"

    compact = {
        "source": report["source_label"],
        "short_old_ssim": report["short"]["old_ssim"],
        "short_new_ssim": report["short"]["new_ssim"],
        "short_ssim_delta": report["short"]["ssim_delta"],
        "short_old_mib": report["short"]["old"]["size_mib"],
        "short_new_mib": report["short"]["new"]["size_mib"],
        "short_stream_copy": report["short"]["new_video_stream_copy_preserved"],
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
    print(f"FACTORY_LIVE_EVIDENCE={rendered}")

    summary = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("\n## Factory real-sermon before/after evidence\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(compact, ensure_ascii=False, indent=2))
            handle.write("\n```\n")
