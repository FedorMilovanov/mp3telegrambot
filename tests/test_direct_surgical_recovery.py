from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from tools.voxcpm2 import direct_failure_recovery as recovery
from tools.voxcpm2 import direct_timing_guard as guard


def test_structured_failure_advances_exactly_once(tmp_path: Path, monkeypatch) -> None:
    calls = []
    item = {"id": 3, "text": "Текст", "start": 0.0, "end": 4.0, "tail_guard": 0.18}
    def original():
        raise guard.RetryableSynthesisFailure(
            "Сегмент #3: measured failure", segment=item,
            evidence={"kind": "measured"}, advance_retry=True,
            failure_kind="measured_timing_failure",
        )
    def invalidate(work_dir, segment, **kwargs):
        calls.append((Path(work_dir), segment, kwargs))
        return {"last_scope_epoch": 2}
    monkeypatch.setattr(sys, "argv", ["renderer", f"--work-dir={tmp_path / 'work'}"])
    with pytest.raises(RuntimeError, match="Retry scope advanced to 2"):
        recovery.run_with_failure_recovery(original, invalidate)
    assert len(calls) == 1
    assert calls[0][2]["evidence"]["early_stop_kind"] == "measured_timing_failure"


def test_blocked_identical_repeat_never_advances(tmp_path: Path, monkeypatch) -> None:
    calls = []
    item = {"id": 3, "text": "Текст", "start": 0.0, "end": 4.0, "tail_guard": 0.18}
    def original():
        raise guard.RetryableSynthesisFailure(
            "Сегмент #3: unchanged marker", segment=item,
            evidence={"kind": "repeat"}, advance_retry=False,
            failure_kind="unchanged_timing_block",
        )
    monkeypatch.setattr(sys, "argv", ["renderer", "--work-dir", str(tmp_path / "work")])
    with pytest.raises(guard.RetryableSynthesisFailure, match="unchanged marker"):
        recovery.run_with_failure_recovery(original, lambda *a, **k: calls.append((a, k)))
    assert calls == []


def test_legacy_message_fallback_supports_equals_flags(tmp_path: Path, monkeypatch) -> None:
    segments = tmp_path / "segments.json"
    segments.write_text(json.dumps([{"id": 3, "text": "Текст"}]), encoding="utf-8")
    calls = []
    def original():
        raise RuntimeError("Сегмент #3: адаптивный бюджет 3 кандидатов исчерпан")
    def invalidate(work_dir, segment, **kwargs):
        calls.append((Path(work_dir), segment, kwargs))
        return {"retry_epoch": 1}
    monkeypatch.setattr(sys, "argv", ["renderer", f"--work-dir={tmp_path / 'work'}", f"--segments-json={segments}"])
    with pytest.raises(RuntimeError, match="Retry scope advanced to 1"):
        recovery.run_with_failure_recovery(original, invalidate)
    assert len(calls) == 1


def test_unrelated_runtime_error_is_not_intercepted() -> None:
    def original():
        raise RuntimeError("ffmpeg missing")
    with pytest.raises(RuntimeError, match="ffmpeg missing"):
        recovery.run_with_failure_recovery(original, lambda *a, **k: None)
