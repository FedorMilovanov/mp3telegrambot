#!/usr/bin/env python3
"""Remaster an existing Russian timeline with per-segment delay and optional NVENC."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(
    command: list[str],
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 and not allow_failure:
        tail = (proc.stderr or proc.stdout or "")[-6000:]
        raise RuntimeError(
            "Command failed:\n" + " ".join(command) + "\n\n" + tail
        )
    return proc


def probe_duration(path: Path) -> float:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    value = float(proc.stdout.strip())
    if value <= 0:
        raise RuntimeError(f"Invalid duration for {path}: {value}")
    return value


def load_segments(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments JSON must contain a non-empty list")

    result: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"segment #{index} must be an object")
        start = float(item["start"])
        end = float(item["end"])
        if start < previous_end - 0.001 or end <= start:
            raise RuntimeError(f"invalid or overlapping segment #{index}")
        result.append(
            {
                "id": int(item.get("id", index)),
                "start": start,
                "end": end,
            }
        )
        previous_end = end
    return result


def parse_delays(value: str, count: int) -> list[int]:
    try:
        delays = [
            int(part.strip())
            for part in value.split(",")
            if part.strip()
        ]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "delays must be comma-separated integer milliseconds"
        ) from exc

    if len(delays) != count:
        raise RuntimeError(f"expected {count} delays, got {len(delays)}")
    if any(delay < 0 or delay > 1500 for delay in delays):
        raise RuntimeError("each delay must be within 0..1500 ms")
    return delays


def build_delay_filter(
    segments: list[dict[str, Any]],
    delays_ms: list[int],
    source_duration: float,
) -> str:
    count = len(segments)
    parts = [
        f"[0:a]asplit={count}"
        + "".join(f"[r{i}]" for i in range(count))
    ]
    labels: list[str] = []

    for index, (segment, delay_ms) in enumerate(
        zip(segments, delays_ms, strict=True)
    ):
        absolute_delay_ms = (
            int(round(segment["start"] * 1000.0)) + delay_ms
        )
        label = f"s{index}"
        labels.append(f"[{label}]")
        parts.append(
            f"[r{index}]"
            f"atrim=start={segment['start']:.6f}:end={segment['end']:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"adelay={absolute_delay_ms}:all=1[{label}]"
        )

    parts.append(
        "".join(labels)
        + f"amix=inputs={count}:duration=longest:"
        + "dropout_transition=0:normalize=0,"
        + f"apad=pad_dur={source_duration:.6f},"
        + f"atrim=duration={source_duration:.6f},"
        + "asetpts=N/SR/TB[out]"
    )
    return ";".join(parts)


def nvenc_available() -> bool:
    proc = run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        allow_failure=True,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode == 0 and "h264_nvenc" in text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-video", type=Path, required=True)
    parser.add_argument("--russian-wav", type=Path, required=True)
    parser.add_argument("--segments-json", type=Path, required=True)
    parser.add_argument("--master-script", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--original-gain", type=float, default=0.25)
    parser.add_argument("--delays-ms", default="220,160,100,40")
    parser.add_argument("--nvenc-preset", default="p5")
    parser.add_argument("--nvenc-cq", type=int, default=18)
    parser.add_argument("--video-bitrate", default="8M")
    parser.add_argument("--maxrate", default="14M")
    parser.add_argument("--bufsize", default="28M")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found in PATH")

    source = args.source_video.expanduser().resolve()
    russian = args.russian_wav.expanduser().resolve()
    segments_path = args.segments_json.expanduser().resolve()
    master_script = args.master_script.expanduser().resolve()
    work_dir = args.work_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    for path in (source, russian, segments_path, master_script):
        if not path.is_file():
            raise RuntimeError(f"missing required file: {path}")
    if not 0.0 <= args.original_gain <= 1.0:
        raise RuntimeError("original gain must be within 0.0..1.0")
    if not 0 <= args.nvenc_cq <= 51:
        raise RuntimeError("NVENC CQ must be within 0..51")

    segments = load_segments(segments_path)
    delays_ms = parse_delays(args.delays_ms, len(segments))
    source_duration = probe_duration(source)
    if segments[-1]["end"] > source_duration + 0.25:
        raise RuntimeError("last segment extends beyond source duration")

    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    shifted_wav = work_dir / "macarthur_ru_final_timeline_delayed.wav"
    cpu_master = (
        output_dir / "MacArthur_FINAL_ENG25_DELAYED_VIDEO_COPY.mp4"
    )
    russian_only = (
        output_dir / "MacArthur_FINAL_DELAYED_RUSSIAN_ONLY.mp4"
    )
    nvenc_output = (
        output_dir / "MacArthur_FINAL_ENG25_DELAYED_NVENC.mp4"
    )
    report_path = (
        output_dir / "MacArthur_FINAL_ENG25_DELAYED_NVENC.report.json"
    )

    delay_filter = build_delay_filter(
        segments,
        delays_ms,
        source_duration,
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(russian),
            "-filter_complex",
            delay_filter,
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(shifted_wav),
        ]
    )

    gain_text = f"{args.original_gain:.6f}"
    master_work = work_dir / "master"
    run(
        [
            sys.executable,
            str(master_script),
            "--source-video",
            str(source),
            "--russian-wav",
            str(shifted_wav),
            "--work-dir",
            str(master_work),
            "--mixed-video",
            str(cpu_master),
            "--russian-only-video",
            str(russian_only),
            "--original-level",
            gain_text,
            "--target-i",
            "-14.0",
            "--target-lra",
            "9.0",
            "--target-tp",
            "-1.0",
        ]
    )

    nvenc_status = "not_available"
    nvenc_error = ""
    if nvenc_available():
        nvenc = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(cpu_master),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "h264_nvenc",
                "-gpu",
                "0",
                "-preset",
                args.nvenc_preset,
                "-tune",
                "hq",
                "-rc",
                "vbr",
                "-cq",
                str(args.nvenc_cq),
                "-b:v",
                args.video_bitrate,
                "-maxrate",
                args.maxrate,
                "-bufsize",
                args.bufsize,
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "passthrough",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(nvenc_output),
            ],
            allow_failure=True,
        )
        if nvenc.returncode == 0 and nvenc_output.is_file():
            nvenc_status = "success"
        else:
            nvenc_status = "failed"
            nvenc_error = (
                nvenc.stderr or nvenc.stdout or ""
            )[-6000:]
            if nvenc_output.exists():
                nvenc_output.unlink()

    report = {
        "schema_version": 1,
        "source_video": str(source),
        "source_duration": source_duration,
        "russian_input": str(russian),
        "shifted_russian": str(shifted_wav),
        "original_gain": args.original_gain,
        "segments": [
            {**segment, "delay_ms": delay}
            for segment, delay in zip(
                segments,
                delays_ms,
                strict=True,
            )
        ],
        "cpu_video_copy_master": str(cpu_master),
        "russian_only": str(russian_only),
        "nvenc_output": (
            str(nvenc_output)
            if nvenc_status == "success"
            else None
        ),
        "nvenc_status": nvenc_status,
        "nvenc_error_tail": nvenc_error,
        "nvenc_profile": {
            "encoder": "h264_nvenc",
            "decode": "software",
            "preset": args.nvenc_preset,
            "cq": args.nvenc_cq,
            "bitrate": args.video_bitrate,
            "maxrate": args.maxrate,
            "bufsize": args.bufsize,
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if nvenc_status == "failed":
        print(
            "NVENC failed; the CPU video-copy master remains valid.",
            file=sys.stderr,
        )
    elif nvenc_status == "not_available":
        print(
            "h264_nvenc is not available; "
            "the CPU video-copy master remains valid.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
