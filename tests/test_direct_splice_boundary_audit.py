"""Regression on cut-off old syllables in the 2007 Piper/MacArthur V20/V22 case."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import wave

import pytest

from tools.voxcpm2.direct_splice_boundary_audit import (
    SpliceAuditError,
    inspect_splices,
    sha256_file,
)


RATE = 8000


def _wav(path: Path, speech: list[tuple[float, float, float]], *, bits: int = 16) -> None:
    width = bits // 8
    samples: list[bytes] = []
    for i in range(RATE * 4):
        t = i / RATE
        amp = sum(volume * math.sin(2 * math.pi * 180 * t) for start, end, volume in speech if start <= t < end)
        level = round(max(-1.0, min(0.999, amp)) * ((1 << (bits - 1)) - 1))
        samples.append(level.to_bytes(width, "little", signed=True))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setframerate(RATE)
        out.setsampwidth(width)
        out.writeframes(b"".join(samples))


def _review(
    tmp_path: Path,
    *,
    source: list[tuple[float, float, float]],
    extra: list[tuple[float, float, float]] | None = None,
    bits: int = 16,
) -> dict[str, Any]:
    old = tmp_path / "old.wav"
    new = tmp_path / "new.wav"
    _wav(old, source, bits=bits)
    _wav(
        new,
        [z for z in source if z[1] <= 1.0 or z[0] >= 2.0] + [(1.15, 1.85, 0.25)] + (extra or []),
        bits=bits,
    )
    return inspect_splices(
        old,
        new,
        source_sha256=sha256_file(old),
        candidate_sha256=sha256_file(new),
        windows=[("retake_whole_sentence", 1.0, 2.0)],
    )


def test_quiet_edges_are_only_ready_for_human_review_not_editorial_pass(
    tmp_path: Path,
) -> None:
    report = _review(tmp_path, source=[(1.2, 1.8, 0.3)])
    assert report["status"] == "READY_FOR_HUMAN_REVIEW"
    assert report["pcm_outside_windows_byte_identical"] is True
    assert report["human_listening_approved"] is False
    assert report["post_aac_mix_approved"] is False
    assert report["editorial_approved"] is False


@pytest.mark.parametrize("old_voice_span", [(0.8, 1.2), (1.8, 2.2)])
def test_fail_closed_when_old_voice_crosses_splice_edge(tmp_path: Path, old_voice_span: tuple[float, float]) -> None:
    # The old voice is on *both* sides of one cut; a 20ms fade would
    # otherwise retain an unfinished old syllable just outside the slot.
    a, b = old_voice_span
    old = tmp_path / "old.wav"
    new = tmp_path / "new.wav"
    _wav(old, [(a, b, 0.3)])
    # Simulate replacement with new voice only inside the allowed window.
    _wav(new, [(a, min(b, 1.0), 0.3)] if a < 1.0 else [(2.0, b, 0.3)])
    # Overwrite in the same slot via a candidate file to preserve old voice
    # outside; add new TTS speech to the otherwise quiet interior.
    with wave.open(str(new), "rb") as f:
        params = f.getparams()
        raw = bytearray(f.readframes(f.getnframes()))
    with wave.open(str(tmp_path / "voice.wav"), "wb") as f:
        f.setparams(params)
        f.writeframes(
            b"".join(
                round(0.25 * math.sin(i * 2 * math.pi * 180 / RATE) * 32767).to_bytes(2, "little", signed=True)
                for i in range(round(0.20 * RATE), round(0.80 * RATE))
            )
        )
    with wave.open(str(tmp_path / "voice.wav"), "rb") as f:
        insert = f.readframes(f.getnframes())
    raw[int(1.20 * RATE) * 2 : int(1.80 * RATE) * 2] = insert
    with wave.open(str(new), "wb") as f:
        f.setparams(params)
        f.writeframes(raw)
    report = inspect_splices(
        old,
        new,
        source_sha256=sha256_file(old),
        candidate_sha256=sha256_file(new),
        windows=[("unsafe", 1.0, 2.0)],
    )
    assert report["status"] == "HOLD_ACTIVE_SOURCE_BOUNDARY"
    assert report["windows"][0]["edge_activity_hold"] is True


@pytest.mark.parametrize("bits", [16, 24, 32])
def test_pcm_depths_are_supported(tmp_path: Path, bits: int) -> None:
    report = _review(tmp_path, source=[(1.2, 1.8, 0.3)], bits=bits)
    assert report["status"] == "READY_FOR_HUMAN_REVIEW"


def test_outside_window_change_is_blocked(tmp_path: Path) -> None:
    old = tmp_path / "old.wav"
    new = tmp_path / "new.wav"
    _wav(old, [(1.2, 1.8, 0.3)])
    _wav(new, [(1.15, 1.85, 0.3), (2.5, 2.7, 0.1)])
    with pytest.raises(SpliceAuditError, match="OUTSIDE"):
        inspect_splices(
            old,
            new,
            source_sha256=sha256_file(old),
            candidate_sha256=sha256_file(new),
            windows=[("primary", 1, 2)],
        )


def test_hash_drift_blocks(tmp_path: Path) -> None:
    old = tmp_path / "old.wav"
    new = tmp_path / "new.wav"
    _wav(old, [(1.2, 1.8, 0.3)])
    _wav(new, [(1.1, 1.9, 0.2)])
    with pytest.raises(SpliceAuditError, match="fingerprint drift"):
        inspect_splices(
            old,
            new,
            source_sha256="0" * 64,
            candidate_sha256=sha256_file(new),
            windows=[("primary", 1, 2)],
        )


def test_overlapping_windows_blocked(tmp_path: Path) -> None:
    old = tmp_path / "old.wav"
    new = tmp_path / "new.wav"
    _wav(old, [(1.3, 1.7, 0.3)])
    _wav(new, [(1.3, 1.7, 0.2)])
    with pytest.raises(SpliceAuditError, match="overlapping"):
        inspect_splices(
            old,
            new,
            source_sha256=sha256_file(old),
            candidate_sha256=sha256_file(new),
            windows=[("one", 1, 2), ("two", 1.5, 2.5)],
        )
