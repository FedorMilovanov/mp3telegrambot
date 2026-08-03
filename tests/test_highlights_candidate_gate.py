from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from services import highlights_candidate_gate as gate


@pytest.mark.asyncio
async def test_probe_timeout_becomes_structured_fail_closed_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["ffmpeg", "probe"], timeout=90)

    monkeypatch.setattr(gate, "refine_highlights_candidate", _timeout)

    candidate, report = await gate.verify_highlights_candidate(
        tmp_path / "source.mp4",
        {"fragments": []},
    )

    assert candidate is None
    assert report["accepted"] is False
    assert report["reason"] == "probe_render_timeout"
    assert report["detail"] == "timeout=90"


@pytest.mark.asyncio
async def test_unreaped_child_becomes_distinct_structured_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _unreaped(*args, **kwargs):
        raise RuntimeError("child process did not stop after terminate/kill")

    monkeypatch.setattr(gate, "refine_highlights_candidate", _unreaped)

    candidate, report = await gate.verify_highlights_candidate(
        tmp_path / "source.mp4",
        {"fragments": []},
    )

    assert candidate is None
    assert report["reason"] == "probe_process_not_reaped"
    assert "terminate/kill" in report["detail"]


@pytest.mark.asyncio
async def test_unexpected_quality_failure_is_structured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _broken(*args, **kwargs):
        raise OSError("temporary storage unavailable")

    monkeypatch.setattr(gate, "refine_highlights_candidate", _broken)

    candidate, report = await gate.verify_highlights_candidate(
        tmp_path / "source.mp4",
        {"fragments": []},
    )

    assert candidate is None
    assert report["reason"] == "quality_gate_error:OSError"
    assert report["detail"] == "temporary storage unavailable"


@pytest.mark.asyncio
async def test_cancellation_is_never_converted_to_quality_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(gate, "refine_highlights_candidate", _cancel)

    with pytest.raises(asyncio.CancelledError):
        await gate.verify_highlights_candidate(
            tmp_path / "source.mp4",
            {"fragments": []},
        )


@pytest.mark.asyncio
async def test_normal_quality_result_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected_candidate = {"title": "verified"}
    expected_report = {"accepted": True, "reason": "accepted"}

    async def _accepted(*args, **kwargs):
        return expected_candidate, expected_report

    monkeypatch.setattr(gate, "refine_highlights_candidate", _accepted)

    candidate, report = await gate.verify_highlights_candidate(
        tmp_path / "source.mp4",
        {"fragments": []},
    )

    assert candidate is expected_candidate
    assert report is expected_report


def test_active_highlights_pipeline_uses_structured_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "pipelines" / "montage.py").read_text(encoding="utf-8")

    assert (
        "from services.highlights_candidate_gate import "
        "verify_highlights_candidate"
    ) in source
    assert "await verify_highlights_candidate(" in source
    assert "from services.highlights_quality import refine_highlights_candidate" not in source
