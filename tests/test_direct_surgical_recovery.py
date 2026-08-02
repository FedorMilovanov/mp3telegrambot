from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from tools.voxcpm2 import direct_failure_recovery as recovery
from tools.voxcpm2 import direct_timing_guard as guard
from tools.voxcpm2.direct_surgical_guard import install_guard_contract

install_guard_contract()


def test_structured_failure_advances_exactly_once(tmp_path: Path, monkeypatch) -> None:
    calls = []
    item = {"id": 3, "text": "Текст", "start": 0.0, "end": 4.0, "tail_guard": 0.18}

    def original():
        raise guard.RetryableSynthesisFailure(
            "Сегмент #3: measured failure",
            segment=item,
            evidence={"kind": "measured"},
            advance_retry=True,
            failure_kind="measured_timing_failure",
        )

    def invalidate(work_dir, segment, **kwargs):
        calls.append((Path(work_dir), segment, kwargs))
        return {"last_scope_epoch": 2, "epoch": 9}

    namespace = {"main": original, "invalidate_segment_for_retry": invalidate}
    recovery.install_main_failure_recovery(namespace)
    monkeypatch.setattr(sys, "argv", ["renderer", f"--work-dir={tmp_path / 'work'}"])
    with pytest.raises(RuntimeError, match="Retry scope advanced to 2"):
        namespace["main"]()
    assert len(calls) == 1
    assert calls[0][2]["evidence"]["early_stop_kind"] == "measured_timing_failure"


def test_blocked_identical_repeat_never_advances(tmp_path: Path, monkeypatch) -> None:
    calls = []
    item = {"id": 3, "text": "Текст", "start": 0.0, "end": 4.0, "tail_guard": 0.18}

    def original():
        raise guard.RetryableSynthesisFailure(
            "Сегмент #3: unchanged marker",
            segment=item,
            evidence={"kind": "repeat"},
            advance_retry=False,
            failure_kind="unchanged_timing_block",
        )

    namespace = {
        "main": original,
        "invalidate_segment_for_retry": lambda *args, **kwargs: calls.append((args, kwargs)),
    }
    recovery.install_main_failure_recovery(namespace)
    monkeypatch.setattr(sys, "argv", ["renderer", "--work-dir", str(tmp_path / "work")])
    with pytest.raises(guard.RetryableSynthesisFailure, match="unchanged marker"):
        namespace["main"]()
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

    namespace = {"main": original, "invalidate_segment_for_retry": invalidate}
    recovery.install_main_failure_recovery(namespace)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "renderer",
            f"--work-dir={tmp_path / 'work'}",
            f"--segments-json={segments}",
        ],
    )
    with pytest.raises(RuntimeError, match="Retry scope advanced to 1"):
        namespace["main"]()
    assert len(calls) == 1


def test_unrelated_runtime_error_is_not_intercepted() -> None:
    def original():
        raise RuntimeError("ffmpeg missing")

    namespace = {
        "main": original,
        "invalidate_segment_for_retry": lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not run")
        ),
    }
    recovery.install_main_failure_recovery(namespace)
    with pytest.raises(RuntimeError, match="ffmpeg missing"):
        namespace["main"]()
