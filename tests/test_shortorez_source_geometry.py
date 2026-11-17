"""Fail-closed source pixel geometry for fixed-source-X SOLO SHORTOREZ."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.media_delivery_probe import (
    _probe_from_payload,
    source_video_geometry_is_plain_square_pixels,
)


def _probe(*, sar: str | None = "1:1", tags: object = None, side_data: object = None) -> Any:
    video: dict[str, Any] = {
        "codec_type": "video",
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": sar,
    }
    if tags is not None:
        video["tags"] = tags
    if side_data is not None:
        video["side_data_list"] = side_data
    payload: dict[str, Any] = {
        "format": {"duration": "205.0"},
        "streams": [
            video,
            {"codec_type": "audio", "sample_rate": "48000", "codec_name": "aac"},
        ],
    }
    return _probe_from_payload(Path("synthetic-missing-source.mp4"), payload)


def test_square_pixels_and_untagged_rotation_are_valid_normalized_source() -> None:
    info = _probe()
    assert info.video_sample_aspect_ratio == "1:1"
    assert info.video_display_rotation == 0.0
    assert source_video_geometry_is_plain_square_pixels(info)


@pytest.mark.parametrize("sar", ["", None, "N/A", "0:1", "1:0", "4:3", "16:15", "2", "1:2", "bad"])
def test_non_square_or_unknown_pixel_aspect_holds_solo(sar: str | None) -> None:
    assert not source_video_geometry_is_plain_square_pixels(_probe(sar=sar))


@pytest.mark.parametrize("sar", ["1:1", "2:2", "3:3"])
def test_exact_square_pixel_rationals_are_allowed(sar: str) -> None:
    assert source_video_geometry_is_plain_square_pixels(_probe(sar=sar))


@pytest.mark.parametrize(
    ("tags", "side_data"),
    [
        ({"rotate": "90"}, None),
        ({"rotate": "-90"}, None),
        ({"rotate": "abc"}, None),
        ({"rotate": "nan"}, None),
        (None, [{"rotation": 90}]),
        (None, [{"rotation": "180.0"}]),
        (None, [{"rotation": "-90"}]),
        (None, [{"rotation": "invalid"}]),
        ({"rotate": "90"}, [{"rotation": -90}]),
        (None, {"rotation": 0}),
        (None, ["unparsed display matrix"]),
    ],
)
def test_unapproved_or_ambiguous_rotation_holds_solo(tags: object, side_data: object) -> None:
    assert not source_video_geometry_is_plain_square_pixels(_probe(tags=tags, side_data=side_data))


@pytest.mark.parametrize(
    ("tags", "side_data"),
    [
        ({"rotate": "0"}, None),
        (None, [{"rotation": 0}]),
        ({"rotate": "0.0"}, [{"rotation": 0.0}]),
    ],
)
def test_explicit_zero_rotation_is_accepted(tags: object, side_data: object) -> None:
    assert source_video_geometry_is_plain_square_pixels(_probe(tags=tags, side_data=side_data))
