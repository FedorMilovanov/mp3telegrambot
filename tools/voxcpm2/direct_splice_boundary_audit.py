"""Read-only, fail-closed preflight of *source* speech around PCM splice seams.

This is a conservative acoustic risk gate, NOT word-boundary recognition or a
substitute for human listening. No audio is written or modified.

The V20/V22 Piper/MacArthur case exposed a false PASS: sample identity outside
the patched range was checked, but source voice at the range edges was not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import wave
from collections.abc import Sequence
from typing import Any

# An approximately -44 dBFS signal is never assumed to be silence without
# checking local noise floor and listening. Non-silence forces editorial HOLD.
EDGE_RMS_LIMIT = 0.006
EDGE_SECONDS = 0.030


class SpliceAuditError(ValueError):
    """Invalid input or a broken invariance; fail closed, never label PASS."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rms_pcm(raw: bytes, sample_width: int) -> float:
    if not raw:
        raise SpliceAuditError("empty PCM window near boundary")
    if len(raw) % sample_width:
        raise SpliceAuditError("misaligned PCM byte window")
    max_amplitude = float(1 << (8 * sample_width - 1))
    squared = 0.0
    n = len(raw) // sample_width
    for i in range(0, len(raw), sample_width):
        sample = int.from_bytes(raw[i : i + sample_width], "little", signed=True)
        squared += (sample / max_amplitude) ** 2
    return math.sqrt(squared / n)


def _edge_rms(stream: wave.Wave_read, frame: int, *, width_frames: int) -> tuple[float, float]:
    if frame - width_frames < 0 or frame + width_frames > stream.getnframes():
        raise SpliceAuditError("splice too near WAV start/end for edge inspection")
    stream.setpos(frame - width_frames)
    before = _rms_pcm(stream.readframes(width_frames), stream.getsampwidth())
    stream.setpos(frame)
    after = _rms_pcm(stream.readframes(width_frames), stream.getsampwidth())
    return before, after


def _bytewise_same_outside(old: wave.Wave_read, new: wave.Wave_read, spans: list[tuple[int, int]]) -> bool:
    end = old.getnframes()
    gaps = []
    position = 0
    for start, stop in spans:
        gaps.append((position, start))
        position = stop
    gaps.append((position, end))
    for start, stop in gaps:
        old.setpos(start)
        new.setpos(start)
        count = stop - start
        while count:
            size = min(count, 16384)
            if old.readframes(size) != new.readframes(size):
                return False
            count -= size
    return True


def inspect_splices(
    source: Path,
    candidate: Path,
    *,
    source_sha256: str,
    candidate_sha256: str,
    windows: Sequence[tuple[str, float, float]],
) -> dict[str, Any]:
    """Verify immutable PCM and classify voice activity at each splice seam.

    Status can be HOLD_ACTIVE_SOURCE_BOUNDARY or READY_FOR_HUMAN_REVIEW,
    never EDITORIAL_APPROVED. Both source and post-mix AAC still require an
    attributable real human listening witness.
    """
    source = Path(source)
    candidate = Path(candidate)
    if not source.is_file() or not candidate.is_file():
        raise SpliceAuditError("missing source or candidate WAV")
    for supplied, path in (
        (source_sha256, source),
        (candidate_sha256, candidate),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", supplied):
            raise SpliceAuditError("expected SHA-256 must be 64 lowercase hex digits")
        if sha256_file(path) != supplied:
            raise SpliceAuditError(f"source fingerprint drift: {path.name}")
    if not windows:
        raise SpliceAuditError("at least one explicit permitted patch window required")

    with wave.open(str(source), "rb") as old, wave.open(str(candidate), "rb") as new:
        fmt_old = (
            old.getnchannels(),
            old.getsampwidth(),
            old.getframerate(),
            old.getnframes(),
            old.getcomptype(),
        )
        fmt_new = (
            new.getnchannels(),
            new.getsampwidth(),
            new.getframerate(),
            new.getnframes(),
            new.getcomptype(),
        )
        if fmt_old != fmt_new:
            raise SpliceAuditError("format, sample rate or sample count drift")
        if fmt_old[0] != 1 or fmt_old[1] not in (2, 3, 4) or fmt_old[4] != "NONE":
            raise SpliceAuditError("only mono 16/24/32-bit integer PCM WAV supported")

        rate = old.getframerate()
        width_frames = max(1, round(EDGE_SECONDS * rate))
        frames = old.getnframes()
        patches = []
        last_stop = 0
        spans = []
        for patch_id, start, stop in windows:
            if not isinstance(patch_id, str) or not patch_id.strip():
                raise SpliceAuditError("every patch requires a non-empty ID")
            if not all(math.isfinite(x) for x in (start, stop)):
                raise SpliceAuditError("NaN/Infinity time boundary")
            first = round(start * rate)
            last = round(stop * rate)
            if first < last_stop or first < width_frames or last <= first:
                raise SpliceAuditError("overlapping, unordered or invalid patch windows")
            if last + width_frames > frames:
                raise SpliceAuditError("patch runs into end of recording")
            spans.append((first, last))
            left_before, left_after = _edge_rms(old, first, width_frames=width_frames)
            right_before, right_after = _edge_rms(old, last, width_frames=width_frames)
            # Check the SOURCE, never the faded result: crossfades can artificially
            # zero out a cut syllable and create a false clean-boundary PASS.
            level = max(left_before, left_after, right_before, right_after)
            old.setpos(first)
            new.setpos(first)
            if old.readframes(last - first) == new.readframes(last - first):
                raise SpliceAuditError(f"patch did not change any samples: {patch_id}")
            patches.append(
                {
                    "patch_id": patch_id,
                    "start_seconds": start,
                    "end_seconds": stop,
                    "source_rms_30ms": {
                        "left_before": round(left_before, 6),
                        "left_after": round(left_after, 6),
                        "right_before": round(right_before, 6),
                        "right_after": round(right_after, 6),
                    },
                    "edge_activity_hold": level >= EDGE_RMS_LIMIT,
                }
            )
            last_stop = last

        if not _bytewise_same_outside(old, new, spans):
            raise SpliceAuditError("PCM changed OUTSIDE explicitly permitted patch windows")

    any_active = any(item["edge_activity_hold"] for item in patches)
    return {
        "status": "HOLD_ACTIVE_SOURCE_BOUNDARY" if any_active else "READY_FOR_HUMAN_REVIEW",
        "source_sha256": source_sha256,
        "candidate_sha256": candidate_sha256,
        "sample_rate": rate,
        "windows": patches,
        "pcm_outside_windows_byte_identical": True,
        "edge_rms_threshold": EDGE_RMS_LIMIT,
        "edge_window_seconds": EDGE_SECONDS,
        "human_listening_approved": False,
        "post_aac_mix_approved": False,
        "editorial_approved": False,
        "notes": (
            "Silence in 30ms boundary windows does NOT prove intact words; "
            "hold a contextual human A/B before and after the edit, in final AAC. "
            "Noise or speaker speech at the edge requires expanding to complete "
            "phrase boundaries or a documented human review, never a tiny fade fix."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument(
        "--window",
        nargs=3,
        metavar=("PATCH_ID", "START_SEC", "END_SEC"),
        action="append",
        required=True,
    )
    parser.add_argument("--report", type=Path, help="New JSON report; never overwrite")
    args = parser.parse_args()
    try:
        result = inspect_splices(
            args.source,
            args.candidate,
            source_sha256=args.source_sha256,
            candidate_sha256=args.candidate_sha256,
            windows=[(x, float(a), float(b)) for x, a, b in args.window],
        )
    except (SpliceAuditError, OSError, EOFError, ValueError) as error:
        print(json.dumps({"status": "BLOCK_INVALID_INPUT", "error": str(error)}))
        return 2
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        if args.report.exists():
            print(json.dumps({"status": "BLOCK_EXISTING_REPORT"}))
            return 2
        args.report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    # Human review remains mandatory even if edges are quiet.
    return 2 if result["status"].startswith("HOLD_") else 0


if __name__ == "__main__":
    raise SystemExit(main())
