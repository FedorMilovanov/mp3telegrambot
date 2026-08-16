from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.voxcpm2 import clean_production_core as core


def _valid_segments() -> list[dict]:
    return [
        {
            "id": "1",
            "start": "0.0",
            "end": "1.5",
            "start_delay_ms": "0",
            "text": "  Проверяем   реплику.  ",
        }
    ]


def test_valid_segment_fields_are_canonicalized_before_render() -> None:
    segments = _valid_segments()
    core._mark_and_validate_segments(segments, duration="3.0")
    assert segments == [
        {
            "id": 1,
            "start": 0.0,
            "end": 1.5,
            "start_delay_ms": 0,
            "text": "Проверяем реплику.",
            "production_policy": core.POLICY,
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", True),
        ("id", 1.5),
        ("start", True),
        ("start", float("nan")),
        ("end", float("inf")),
        ("start_delay_ms", True),
        ("start_delay_ms", 1.5),
        ("start_delay_ms", 1501),
    ],
)
def test_ambiguous_segment_numbers_fail_closed(field: str, value) -> None:
    segments = _valid_segments()
    segments[0][field] = value
    with pytest.raises(RuntimeError):
        core._mark_and_validate_segments(segments, duration=3.0)


def test_non_object_and_duplicate_segments_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="JSON-объектом"):
        core._mark_and_validate_segments(["bad"], duration=3.0)

    segments = _valid_segments()
    duplicate = copy.deepcopy(segments[0])
    duplicate.update(start=1.5, end=2.5)
    segments.append(duplicate)
    with pytest.raises(RuntimeError, match="Повторный ID"):
        core._mark_and_validate_segments(segments, duration=3.0)


