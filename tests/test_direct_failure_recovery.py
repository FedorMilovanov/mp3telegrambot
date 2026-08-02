from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from tools.voxcpm2 import direct_failure_recovery as recovery


def test_early_budget_failure_advances_retry_once(tmp_path: Path, monkeypatch) -> None:
    segments = tmp_path / "segments.json"
    segments.write_text(json.dumps([{"id": 3, "text": "Текст"}]), encoding="utf-8")
    work = tmp_path / "work"
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
        ["renderer", "--work-dir", str(work), "--segments-json", str(segments)],
    )
    with pytest.raises(RuntimeError, match="Retry scope advanced to 1"):
        namespace["main"]()
    assert len(calls) == 1
    assert calls[0][1]["id"] == 3


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
