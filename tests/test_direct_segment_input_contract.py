from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2.direct_max_quality_io import read_segments


def _write(tmp_path: Path, segments: list[dict]) -> Path:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(segments, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )
    return path


def _segment(**changes):
    item = {
        "id": 1,
        "start": 0.0,
        "end": 2.0,
        "text": "Проверка.",
        "tail_guard": 0.22,
        "start_delay_ms": 420,
        "reference_profile": "extended",
    }
    item.update(changes)
    return item


def test_valid_segment_is_normalized(tmp_path: Path) -> None:
    result = read_segments(_write(tmp_path, [_segment()]))
    assert result[0]["id"] == 1
    assert result[0]["start_delay_ms"] == 420
    assert result[0]["start"] == 0.0


@pytest.mark.parametrize("delay", [-1, 1501])
def test_delay_outside_release_contract_is_rejected(
    tmp_path: Path,
    delay: int,
) -> None:
    with pytest.raises(RuntimeError, match="start_delay_ms"):
        read_segments(_write(tmp_path, [_segment(start_delay_ms=delay)]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start", float("nan")),
        ("end", float("inf")),
        ("tail_guard", float("-inf")),
    ],
)
def test_nonfinite_timing_is_rejected(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    with pytest.raises(RuntimeError, match="конечным числом"):
        read_segments(_write(tmp_path, [_segment(**{field: value})]))


def test_negative_start_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="start не может быть отрицательным"):
        read_segments(_write(tmp_path, [_segment(start=-0.01)]))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    segments = [
        _segment(id=7, start=0.0, end=1.0),
        _segment(id=7, start=1.0, end=2.0),
    ]
    with pytest.raises(RuntimeError, match="Повторяющийся ID"):
        read_segments(_write(tmp_path, segments))


def test_variable_delay_cannot_create_hidden_overlap(tmp_path: Path) -> None:
    segments = [
        _segment(id=1, start=0.0, end=2.0, start_delay_ms=1000),
        _segment(id=2, start=2.0, end=4.0, start_delay_ms=0),
    ]
    with pytest.raises(RuntimeError, match="Эффективное пересечение"):
        read_segments(_write(tmp_path, segments))


def test_non_object_segment_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text('["not-an-object"]', encoding="utf-8")
    with pytest.raises(RuntimeError, match="JSON-объектом"):
        read_segments(path)
