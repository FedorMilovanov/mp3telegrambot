from __future__ import annotations
from typing import Any

import json
from pathlib import Path
import sys

import pytest

from tools.voxcpm2 import direct_failure_recovery as recovery


def test_early_budget_failure_advances_retry_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    segments = tmp_path / "segments.json"
    segments.write_text(json.dumps([{"id": 3, "text": "Текст"}]), encoding="utf-8")
    work = tmp_path / "work"
    calls: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []

    def original() -> None:
        raise RuntimeError("Сегмент #3: адаптивный бюджет 3 кандидатов исчерпан")

    def invalidate(work_dir: str | Path, segment: dict[str, Any], **kwargs: Any) -> dict[str, int]:
        calls.append((Path(work_dir), segment, kwargs))
        return {"retry_epoch": 1}

    monkeypatch.setattr(
        sys,
        "argv",
        ["renderer", "--work-dir", str(work), "--segments-json", str(segments)],
    )
    with pytest.raises(RuntimeError, match="Retry scope advanced to 1"):
        recovery.run_with_failure_recovery(original, invalidate)
    assert len(calls) == 1
    assert calls[0][1]["id"] == 3


def test_unrelated_runtime_error_is_not_intercepted() -> None:
    def original() -> None:
        raise RuntimeError("ffmpeg missing")

    def invalidate(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("must not run")

    with pytest.raises(RuntimeError, match="ffmpeg missing"):
        recovery.run_with_failure_recovery(original, invalidate)
