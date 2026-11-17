"""Persisted Shorts layout must not silently become blurred Q&A mode."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast

import pytest

from core import database


def test_trim_store_requires_a_mode_and_new_schema_has_no_blur_default() -> None:
    parameter = inspect.signature(database.short_trim_save).parameters["visual_mode"]
    assert parameter.default is inspect.Parameter.empty
    source = Path(database.__file__).read_text(encoding="utf-8")
    assert "visual_mode   TEXT," in source
    assert "visual_mode   TEXT DEFAULT 'full_frame_vertical'" not in source


@pytest.mark.parametrize("mode", ["", "auto", "blur", cast(str, None)])
def test_invalid_trim_layout_is_rejected_before_database_write(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    def forbidden_connection() -> None:
        raise AssertionError("Invalid mode reached SQLite")

    monkeypatch.setattr(database, "_db_conn", forbidden_connection)
    with pytest.raises(ValueError, match="explicit supported visual_mode"):
        database.short_trim_save(
            short_id="new-short",
            video_path="original.mp4",
            start_seconds=0,
            end_seconds=20,
            visual_mode=mode,
        )


class _RecordingConnection:
    def __init__(self) -> None:
        self.bindings: tuple[Any, ...] | None = None
        self.committed = False

    def __enter__(self) -> _RecordingConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, values: tuple[Any, ...] | None = None) -> None:
        if "INSERT OR REPLACE INTO short_trims" in query:
            self.bindings = values

    def commit(self) -> None:
        self.committed = True


@pytest.mark.parametrize("mode", ["crop_zoom", "full_frame_vertical"])
def test_explicit_trim_layout_is_persisted_unchanged(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    connection = _RecordingConnection()
    monkeypatch.setattr(database, "_db_conn", lambda: connection)
    database.short_trim_save(
        short_id="one-reviewed-short",
        video_path="original.mp4",
        start_seconds=0,
        end_seconds=20,
        visual_mode=mode,
    )
    assert connection.committed is True
    assert connection.bindings is not None
    assert connection.bindings[:5] == ("one-reviewed-short", "original.mp4", 0, 20, mode)
