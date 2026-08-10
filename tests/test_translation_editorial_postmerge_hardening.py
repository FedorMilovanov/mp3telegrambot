from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import services.translation_editorial as editorial
import services.translation_editorial_composition as composition


def _composition_document(piece_ids: list[str]) -> dict:
    pieces = []
    for index, piece_id in enumerate(piece_ids):
        start = float(index * 20)
        pieces.append(
            {
                "piece_id": piece_id,
                "kind": "short",
                "assembly_mode": "continuous",
                "segments": [
                    {"start_seconds": start, "end_seconds": start + 10.0}
                ],
            }
        )
    document = {
        "schema_name": composition.COMPOSITION_SCHEMA_NAME,
        "schema_version": composition.COMPOSITION_SCHEMA_VERSION,
        "source": {
            "local_path": "translated-clean.mp4",
            "sha256": "sha256:" + "a" * 64,
            "bytes": 4096,
            "duration_seconds": 120.0,
            "review_pack_id": "",
            "review_sha256": "",
        },
        "release_target": {},
        "pieces": pieces,
    }
    document["composition_id"] = composition.composition_id(document)
    return document


def test_composition_rejects_windows_reserved_piece_id() -> None:
    errors = composition.validate_composition_document(_composition_document(["CON"]))

    assert any("reserved Windows filename" in error for error in errors)


def test_composition_rejects_case_insensitive_output_collision() -> None:
    errors = composition.validate_composition_document(
        _composition_document(["Short", "short"])
    )

    assert any("case-insensitive filesystem" in error for error in errors)


def test_drop_span_budget_rejects_individual_and_total_destructive_removal() -> None:
    individual = editorial.validate_drop_span_budget(
        [{"type": "drop_span", "start_seconds": 10.0, "end_seconds": 19.0}],
        100.0,
    )
    assert any("maximum automatic drop" in error for error in individual)

    total = editorial.validate_drop_span_budget(
        [
            {"type": "drop_span", "start_seconds": 10.0, "end_seconds": 12.0},
            {"type": "drop_span", "start_seconds": 20.0, "end_seconds": 22.0},
            {"type": "drop_span", "start_seconds": 30.0, "end_seconds": 32.0},
        ],
        100.0,
    )
    assert editorial.drop_span_budget_seconds(100.0) == 5.0
    assert any("source budget is 5.000s" in error for error in total)


def test_review_validation_enforces_full_sermon_total_drop_budget() -> None:
    pack_id = "sha256:" + "b" * 64
    manifest = {
        "review_pack_id": pack_id,
        "source": {"duration_seconds": 100.0},
        "candidates": {},
    }
    review = {
        "schema_name": editorial.REVIEW_SCHEMA_NAME,
        "schema_version": editorial.REVIEW_SCHEMA_VERSION,
        "review_pack_id": pack_id,
        "full_sermon": {
            "verdict": "repair",
            "issues": [
                {
                    "start_seconds": 10.0,
                    "end_seconds": 12.0,
                    "severity": "major",
                    "action": {"type": "drop_span"},
                },
                {
                    "start_seconds": 20.0,
                    "end_seconds": 22.0,
                    "severity": "major",
                    "action": {"type": "drop_span"},
                },
                {
                    "start_seconds": 30.0,
                    "end_seconds": 32.0,
                    "severity": "major",
                    "action": {"type": "drop_span"},
                },
            ],
        },
        "candidate_reviews": [],
    }

    errors = editorial.validate_review_document(review, manifest)

    assert any("full_sermon: merged automatic drop removal" in error for error in errors)


@pytest.mark.asyncio
async def test_apply_safe_repairs_blocks_oversized_drop_before_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media" * 600)
    output = tmp_path / "clean.mp4"
    ffmpeg_called = False

    async def fake_probe(_path: Path):
        return SimpleNamespace(duration=100.0)

    async def fail_if_ffmpeg_runs(*_args, **_kwargs):
        nonlocal ffmpeg_called
        ffmpeg_called = True
        raise AssertionError("FFmpeg must not run for an unsafe drop plan")

    monkeypatch.setattr(editorial.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(editorial, "probe_media_async", fake_probe)
    monkeypatch.setattr(editorial, "media_probe_is_deliverable", lambda probe: probe is not None)
    monkeypatch.setattr(editorial, "run_cancellable_process", fail_if_ffmpeg_runs)

    with pytest.raises(ValueError, match="unsafe automatic drop_span repair"):
        await editorial.apply_safe_repairs(
            source_video_path=source,
            output_path=output,
            duration=100.0,
            repairs=[
                {"type": "drop_span", "start_seconds": 10.0, "end_seconds": 19.0}
            ],
        )

    assert ffmpeg_called is False
    assert not output.exists()
