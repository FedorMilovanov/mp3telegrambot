from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.voxcpm2 import direct_max_quality_render as render


def test_build_timeline_invokes_post_assembly_delivery_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "timeline.wav"
    fitted = tmp_path / "segment.wav"
    fitted.write_bytes(b"placeholder")
    segment: dict[str, Any] = {
        "id": 1,
        "start": 0.0,
        "end": 1.5,
        "start_delay_ms": 0,
        "text": "Завершение.",
    }
    calls: list[tuple[Path, object]] = []

    def fake_run(command: list[str], *, capture: bool = False) -> None:
        output.write_bytes(b"timeline")
        return None

    def fake_verify(path: Path, segments: list[tuple[dict[str, Any], Path]]) -> dict[str, Any]:
        calls.append((path, segments))
        return {"passed": True}

    monkeypatch.setattr(render, "run_checked", fake_run)
    monkeypatch.setattr(
        render.direct_timeline_delivery_qa,
        "verify_timeline_delivery",
        fake_verify,
    )

    fitted_segments = [(segment, fitted)]
    render.build_timeline(fitted_segments, output, 1.5)

    assert calls == [(output, fitted_segments)]
