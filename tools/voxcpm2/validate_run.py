#!/usr/bin/env python3
"""Validate a segmented VoxCPM2 run without importing torch or VoxCPM.

The validator checks the JSON report, segment files, timing coverage, tempo
limits, CUDA safety flag and final duration. It is intentionally lightweight so
it can run in CI or from the normal bot environment.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


class ValidationError(RuntimeError):
    pass


def _number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} is not numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{name} is not finite: {value!r}")
    return result


def validate_report(
    report: dict[str, Any],
    *,
    require_files: bool = True,
    duration_tolerance: float = 0.10,
    min_tempo: float = 0.65,
    max_tempo: float = 1.65,
) -> list[str]:
    warnings: list[str] = []

    if report.get("cuda_available") is not False:
        raise ValidationError(
            "CPU safety check failed: cuda_available must be exactly false"
        )

    source_duration = _number(report.get("video_duration"), "video_duration")
    final_duration = _number(
        report.get("final_audio_duration"), "final_audio_duration"
    )

    if source_duration <= 0 or final_duration <= 0:
        raise ValidationError("source and final durations must be positive")

    if abs(source_duration - final_duration) > duration_tolerance:
        raise ValidationError(
            "final audio duration mismatch: "
            f"source={source_duration:.3f}, final={final_duration:.3f}, "
            f"tolerance={duration_tolerance:.3f}"
        )

    segments = report.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValidationError("segments must be a non-empty list")

    seen_ids: set[int] = set()
    previous_end = 0.0

    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            raise ValidationError(f"segment #{index} is not an object")

        try:
            segment_id = int(segment.get("id", index))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid segment id at #{index}") from exc

        if segment_id in seen_ids:
            raise ValidationError(f"duplicate segment id: {segment_id}")
        seen_ids.add(segment_id)

        start = _number(segment.get("start"), f"segment {segment_id} start")
        end = _number(segment.get("end"), f"segment {segment_id} end")
        target = _number(
            segment.get("target_duration"),
            f"segment {segment_id} target_duration",
        )
        raw = _number(
            segment.get("raw_duration"),
            f"segment {segment_id} raw_duration",
        )
        fitted = _number(
            segment.get("fitted_duration"),
            f"segment {segment_id} fitted_duration",
        )
        tempo = _number(segment.get("tempo"), f"segment {segment_id} tempo")

        if start < previous_end - 0.001:
            raise ValidationError(f"segment {segment_id} overlaps previous segment")
        if end <= start:
            raise ValidationError(f"segment {segment_id} has invalid time window")
        if abs((end - start) - target) > 0.02:
            raise ValidationError(
                f"segment {segment_id} target duration disagrees with window"
            )
        if raw <= 0 or fitted <= 0:
            raise ValidationError(f"segment {segment_id} has non-positive audio")
        if abs(fitted - target) > duration_tolerance:
            raise ValidationError(
                f"segment {segment_id} fitted duration mismatch: "
                f"target={target:.3f}, fitted={fitted:.3f}"
            )
        if not min_tempo <= tempo <= max_tempo:
            raise ValidationError(
                f"segment {segment_id} tempo {tempo:.3f} outside "
                f"{min_tempo:.3f}..{max_tempo:.3f}"
            )

        calculated_tempo = raw / target
        if abs(calculated_tempo - tempo) > 0.01:
            raise ValidationError(
                f"segment {segment_id} tempo field is inconsistent with durations"
            )

        text = str(segment.get("text") or "").strip()
        if not text:
            raise ValidationError(f"segment {segment_id} text is empty")

        for field in ("raw_path", "fitted_path"):
            path_text = str(segment.get(field) or "").strip()
            if not path_text:
                raise ValidationError(f"segment {segment_id} missing {field}")
            if require_files and not Path(path_text).is_file():
                raise ValidationError(
                    f"segment {segment_id} file does not exist: {path_text}"
                )

        if tempo < 0.80 or tempo > 1.25:
            warnings.append(
                f"segment {segment_id}: tempo {tempo:.3f} needs listening review"
            )

        previous_end = end

    output_text = str(report.get("output") or "").strip()
    if not output_text:
        raise ValidationError("report output path is missing")
    if require_files and not Path(output_text).is_file():
        raise ValidationError(f"final timeline file does not exist: {output_text}")

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="segmented run JSON report")
    parser.add_argument(
        "--no-file-check",
        action="store_true",
        help="validate structure and values without checking local paths",
    )
    parser.add_argument("--duration-tolerance", type=float, default=0.10)
    parser.add_argument("--min-tempo", type=float, default=0.65)
    parser.add_argument("--max-tempo", type=float, default=1.65)
    args = parser.parse_args()

    try:
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        if not isinstance(report, dict):
            raise ValidationError("report root must be an object")
        warnings = validate_report(
            report,
            require_files=not args.no_file_check,
            duration_tolerance=args.duration_tolerance,
            min_tempo=args.min_tempo,
            max_tempo=args.max_tempo,
        )
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    print("VALID: segmented VoxCPM2 report passed all hard checks")
    for warning in warnings:
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
