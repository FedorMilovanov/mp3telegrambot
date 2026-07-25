#!/usr/bin/env python3
"""Lightweight preflight for the local VoxCPM2 production package.

This check deliberately does not import torch or voxcpm. It fails before model
loading when paths, references, source timing, disk space or script syntax are
wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class PreflightError(RuntimeError):
    pass


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise PreflightError(
            f"ffprobe could not read source video: {path}\n{proc.stderr[-1000:]}"
        )
    try:
        value = float(proc.stdout.strip())
    except ValueError as exc:
        raise PreflightError(f"invalid source duration: {proc.stdout!r}") from exc
    if value <= 0:
        raise PreflightError(f"source duration must be positive: {value}")
    return value


def load_segments(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"could not read segments JSON: {path}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise PreflightError("segments JSON must contain a non-empty list")

    result: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise PreflightError(f"segment #{index} is not an object")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PreflightError(f"invalid timing in segment #{index}") from exc
        text = str(item.get("text") or "").strip()
        if start < 0 or end <= start:
            raise PreflightError(f"invalid window in segment #{index}: {start}..{end}")
        if start < previous_end - 0.001:
            raise PreflightError(f"segment #{index} overlaps the previous segment")
        if not text:
            raise PreflightError(f"segment #{index} has empty text")
        result.append({"id": int(item.get("id", index)), "start": start, "end": end})
        previous_end = end
    return result


def model_snapshot_exists(root: Path) -> bool:
    if not root.is_dir():
        return False
    direct = root / "config.json"
    if direct.is_file() and (
        (root / "model.safetensors").is_file()
        or any(root.glob("*.safetensors"))
        or any(root.glob("*.bin"))
    ):
        return True
    for config in root.rglob("config.json"):
        parent = config.parent
        if (
            (parent / "model.safetensors").is_file()
            or any(parent.glob("*.safetensors"))
            or any(parent.glob("*.bin"))
        ):
            return True
    return False


def compile_script(python_exe: Path, script: Path) -> None:
    proc = subprocess.run(
        [str(python_exe), "-m", "py_compile", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise PreflightError(
            f"Python syntax check failed: {script}\n{(proc.stderr or proc.stdout)[-2000:]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-exe", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--source-video", type=Path, required=True)
    parser.add_argument("--segments-json", type=Path, required=True)
    parser.add_argument("--extended-reference", type=Path, required=True)
    parser.add_argument("--composite-reference", type=Path, required=True)
    parser.add_argument("--min-free-gb", type=float, default=12.0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    required_tools = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if required_tools:
        raise PreflightError("missing PATH tools: " + ", ".join(required_tools))

    paths = {
        "python_exe": args.python_exe.resolve(),
        "package_dir": args.package_dir.resolve(),
        "work_root": args.work_root.resolve(),
        "model_root": args.model_root.resolve(),
        "source_video": args.source_video.resolve(),
        "segments_json": args.segments_json.resolve(),
        "extended_reference": args.extended_reference.resolve(),
        "composite_reference": args.composite_reference.resolve(),
    }

    for name in (
        "python_exe",
        "source_video",
        "segments_json",
        "extended_reference",
        "composite_reference",
    ):
        if not paths[name].is_file():
            raise PreflightError(f"missing required file {name}: {paths[name]}")

    synth = paths["package_dir"] / "voxcpm2_final_production.py"
    master = paths["package_dir"] / "master_constant_mix.py"
    for script in (synth, master):
        if not script.is_file():
            raise PreflightError(f"missing production script: {script}")
        compile_script(paths["python_exe"], script)

    if not model_snapshot_exists(paths["model_root"]):
        raise PreflightError(f"VoxCPM2 snapshot not found under: {paths['model_root']}")

    paths["work_root"].mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(paths["work_root"]).free
    free_gb = free_bytes / (1024**3)
    if free_gb < args.min_free_gb:
        raise PreflightError(
            f"not enough free disk: {free_gb:.2f} GiB; required {args.min_free_gb:.2f} GiB"
        )

    source_duration = probe_duration(paths["source_video"])
    segments = load_segments(paths["segments_json"])
    final_end = max(segment["end"] for segment in segments)
    if final_end > source_duration + 0.25:
        raise PreflightError(
            f"segments end at {final_end:.3f}s but source is only {source_duration:.3f}s"
        )

    cuda_hidden = os.environ.get("CUDA_VISIBLE_DEVICES") == "-1"
    if not cuda_hidden:
        raise PreflightError(
            "CUDA_VISIBLE_DEVICES must be -1 before the production process starts"
        )

    report = {
        "ok": True,
        "source_duration": round(source_duration, 6),
        "segment_count": len(segments),
        "last_segment_end": round(final_end, 6),
        "free_disk_gib": round(free_gb, 3),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "python_exe": str(paths["python_exe"]),
        "package_dir": str(paths["package_dir"]),
        "work_root": str(paths["work_root"]),
        "model_root": str(paths["model_root"]),
        "source_video": str(paths["source_video"]),
        "extended_reference": str(paths["extended_reference"]),
        "composite_reference": str(paths["composite_reference"]),
    }

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
