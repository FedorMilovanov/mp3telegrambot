"""Offline, source-bound, shot-static SOLO SHORTOREZ visual-plan tests."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services import shortorez_shot_plan as solo

SOURCE_SHA = "sha256:" + "a" * 64
SOURCE_ID = "5nKCs4bkoxE"
START, END = 10_000, 215_000
WIDTH, HEIGHT = 640, 358


def _raw_plan() -> dict[str, Any]:
    return {
        "schema_name": "mp3bot.shortorez-fixed-shot-plan",
        "schema_version": 1,
        "project_key": "lord-god-strength",
        "source_channel_id": "UCeSJsC6go2c9pdJCuUI1BYA",
        "source_video_id": SOURCE_ID,
        "source_sha256": SOURCE_SHA,
        "start_ms": START,
        "end_ms": END,
        "source_width": WIDTH,
        "source_height": HEIGHT,
        "layout": "solo_static_crop_per_source_camera_shot",
        "camera_cut_review": {
            "status": "original_camera_edits_reviewed",
            "reviewer": "Reviewer of original camera edits",
            "method": "Visual source frame samples before and after each camera cut",
        },
        "shots": [
            {"start_ms": START, "end_ms": 110_000, "crop_x": 40, "camera_shot_id": "front"},
            {"start_ms": 110_000, "end_ms": END, "crop_x": 430, "camera_shot_id": "side"},
        ],
    }


def _validated(record: dict[str, Any]) -> solo.ReviewedSoloPlan:
    return solo.validate_reviewed_solo_plan(
        record,
        youtube_id=SOURCE_ID,
        source_sha256=SOURCE_SHA,
        start_ms=START,
        end_ms=END,
        source_width=WIDTH,
        source_height=HEIGHT,
    )


def test_reviewed_source_shots_cover_every_millisecond_with_one_crop_per_take() -> None:
    planned = _validated(_raw_plan())
    assert planned.crop_width == 202
    assert planned.source_height == 358
    assert [(shot.start_ms, shot.end_ms, shot.crop_x) for shot in planned.shots] == [
        (10_000, 110_000, 40),
        (110_000, 215_000, 430),
    ]


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("project_key", "legendary-poet"),
        ("source_video_id", "AAAAAAAAAAA"),
        ("source_sha256", "sha256:" + "b" * 64),
        ("source_channel_id", "OTHER"),
        ("source_width", 1920),
        ("source_height", 1080),
        ("start_ms", 10_001),
        ("end_ms", 214_999),
        ("layout", "full_frame_vertical"),
        ("schema_version", True),
    ],
)
def test_cross_source_or_cross_project_plan_is_rejected(field: str, new_value: object) -> None:
    review = _raw_plan()
    review[field] = new_value
    with pytest.raises(ValueError, match="source|version 1"):
        _validated(review)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_ms", 10_001),
        ("end_ms", 214_999),
        ("crop_x", -2),
        ("crop_x", 439),
        ("crop_x", 440),
        ("crop_x", False),
        ("crop_x", "40"),
        ("camera_shot_id", "side"),
    ],
)
def test_gap_overlap_crop_and_nonunique_shot_id_fail_closed(field: str, value: object) -> None:
    plan = _raw_plan()
    plan["shots"][0][field] = value
    with pytest.raises(ValueError):
        _validated(plan)


def test_plan_with_uncovered_tail_or_extra_fields_is_rejected() -> None:
    plan = _raw_plan()
    plan["shots"][1]["end_ms"] = END - 50
    with pytest.raises(ValueError, match="cover"):
        _validated(plan)

    extra = _raw_plan()
    extra["shots"][0]["follow_face"] = True
    with pytest.raises(ValueError, match="exact time"):
        _validated(extra)


@pytest.mark.parametrize("bad", [[], "not-list", [None], [{}] * 129])
def test_shot_evidence_count_and_shape_are_required(bad: object) -> None:
    plan = _raw_plan()
    plan["shots"] = bad
    with pytest.raises(ValueError):
        _validated(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "auto_detected"),
        ("reviewer", ""),
        ("method", "AI guessed"),
    ],
)
def test_operator_review_cannot_be_absent_or_model_only(field: str, value: object) -> None:
    plan = _raw_plan()
    plan["camera_cut_review"][field] = value
    with pytest.raises(ValueError):
        _validated(plan)


def test_ffmpeg_has_fixed_x_per_shot_exact_camera_cuts_and_continuous_audio() -> None:
    plan = _validated(_raw_plan())
    cmd = solo.build_solo_ffmpeg_command(Path("original.mp4"), Path("staged.mp4"), plan, ffmpeg="ffmpeg")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=10.000:end=110.000" in graph
    assert "trim=start=110.000:end=215.000" in graph
    assert "crop=202:358:40:0" in graph
    assert "crop=202:358:430:0" in graph
    assert "[v0][v1]concat=n=2:v=1:a=0[v]" in graph
    assert graph.count("[0:a]atrim=") == 1
    assert "atrim=start=10.000:end=215.000,asetpts=PTS-STARTPTS[a]" in graph
    assert "gblur" not in graph
    assert "overlay" not in graph
    assert "zoompan" not in graph
    assert "cropdetect" not in graph
    assert "-shortest" not in cmd
    assert cmd[cmd.index("-map") + 1] == "[v]"
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "[a]"
    assert cmd[-1] == "staged.mp4"


@pytest.mark.asyncio
async def test_solo_renderer_is_cancellable_process_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _validated(_raw_plan())
    seen: list[list[str]] = []

    async def process(command: list[str], **kwargs: object) -> SimpleNamespace:
        seen.append(command)
        assert kwargs == {"timeout": 1800, "text": True}
        await asyncio.to_thread(Path(command[-1]).write_bytes, b"synthetic-final-mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(solo.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(solo, "run_cancellable_process", process)
    output = tmp_path / "stage.mp4"
    assert await solo.render_reviewed_solo_shots(tmp_path / "input.mp4", output, plan) is True
    assert len(seen) == 1 and seen[0][-1] == str(output)


@pytest.mark.asyncio
async def test_solo_renderer_cannot_overwrite_existing_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _validated(_raw_plan())
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"do-not-overwrite")
    monkeypatch.setattr(solo.shutil, "which", lambda _: "ffmpeg")

    async def unexpected_process(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise AssertionError("Must not dispatch FFmpeg for existing output")

    monkeypatch.setattr(solo, "run_cancellable_process", unexpected_process)
    assert await solo.render_reviewed_solo_shots(tmp_path / "source.mp4", output, plan) is False
    assert output.read_bytes() == b"do-not-overwrite"
