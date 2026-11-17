"""Offline regressions for SHORTOREZ's existing-renderer 177–300s handoff."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from services.media_delivery_probe import MediaProbe
from services.shortorez_export import export_gap_interval


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _fixture(tmp_path: Path, *, start_ms: int = 10_000, end_ms: int = 215_000) -> dict[str, Any]:
    source = tmp_path / "macarthur.ru_postmix.mp4"
    source.write_bytes(b"synthetic-audio-video-source-bytes")
    source_sha256 = _digest(source.read_bytes())
    evidence = {
        "schema_version": 1,
        "project_key": "lord-god-strength",
        "source_channel_id": "UCeSJsC6go2c9pdJCuUI1BYA",
        "source_video_id": "5nKCs4bkoxE",
        "source_sha256": source_sha256,
        "source_kind": "ru_postmix",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "boundary_review": {
            "status": "heard_on_ru_postmix",
            "reviewer": "Synthetic offline fixture, not real source listening",
            "first_heard_words": "Synthetic beginning",
            "last_heard_words": "Synthetic ending",
        },
    }
    evidence_path = tmp_path / "ru_boundary_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    return {
        "source_path": source,
        "output_dir": tmp_path / "reviewed_outputs",
        "evidence_path": evidence_path,
        "youtube_id": "5nKCs4bkoxE",
        "source_sha256": source_sha256,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "allow_multi_person_blur": True,
    }


def _probe(*, duration: float, width: int = 720, height: int = 1280) -> MediaProbe:
    return MediaProbe(
        duration=duration,
        width=width,
        height=height,
        audio_sample_rate=48_000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
        size_mb=0.1,
        video_sample_aspect_ratio="1:1",
        video_display_rotation=0.0,
    )


async def _run(
    args: dict[str, Any],
    *,
    output_duration: float | None = None,
    output_width: int = 720,
    output_has_audio: bool = True,
    renderer_success: bool = True,
    source_duration: float = 650.0,
) -> tuple[tuple[Path, Path], list[tuple[Path, Path, float, float, dict[str, Any]]]]:
    calls: list[tuple[Path, Path, float, float, dict[str, Any]]] = []

    async def fake_render(source: Path, output: Path, start: float, end: float, **kwargs: Any) -> bool:
        calls.append((source, output, start, end, kwargs))
        if renderer_success:
            await asyncio.to_thread(output.write_bytes, b"synthetic-render-not-a-real-mp4" * 1000)
        return renderer_success

    async def fake_probe(path: Path) -> MediaProbe:
        if path == args["source_path"].resolve():
            return _probe(duration=source_duration)
        duration = output_duration if output_duration is not None else (args["end_ms"] - args["start_ms"]) / 1000
        p = _probe(duration=duration, width=output_width)
        if not output_has_audio:
            return MediaProbe(
                duration=p.duration,
                width=p.width,
                height=p.height,
                has_video=True,
                has_audio=False,
            )
        return p

    outcome = await export_gap_interval(**args, render=fake_render, probe=fake_probe)
    return outcome, calls


@pytest.mark.asyncio
async def test_3_to_5_minute_gap_produces_review_only_9x16_manifest(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    (media, manifest), calls = await _run(args)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert media.is_file() and media.stat().st_size > 0
    assert data["source_video_id"] == "5nKCs4bkoxE"
    assert data["full_video_url"] == "https://www.youtube.com/watch?v=5nKCs4bkoxE"
    assert data["source_start_ms"] == 10_000 and data["source_end_ms"] == 215_000
    assert data["output_sha256"] == _digest(media.read_bytes())
    assert data["output_duration_ms"] == 205_000
    assert (data["output_width"], data["output_height"]) == (720, 1280)
    assert data["speed_percent"] == 100
    assert data["visual_layout"] == "explicit_multi_person_context_only"
    assert data["release_authorized"] is False
    assert data["provider_write_performed"] is False
    assert data["rights_cleared"] is False
    assert data["final_encoded_audio_listened"] is False
    assert data["subtitles_reviewed"] is False
    assert data["visual_safe_zones_reviewed"] is False
    assert calls[0][2:4] == (10.0, 215.0)
    assert calls[0][4] == {"visual_mode": "full_frame_vertical", "snap_to_silence": False}
    assert len(list(args["output_dir"].iterdir())) == 2


@pytest.mark.asyncio
async def test_unreviewed_solo_default_cannot_render_blurred_inset(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args.pop("allow_multi_person_blur")
    with pytest.raises(ValueError, match="shot-aware FULL-BLEED"):
        await _run(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
async def test_boolean_like_truthy_cannot_override_solo_layout(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args["allow_multi_person_blur"] = 1
    with pytest.raises(ValueError, match="FULL-BLEED"):
        await _run(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
async def test_collision_fails_before_any_render_and_preserves_original(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    (media, manifest), _ = await _run(args)
    original = (media.read_bytes(), manifest.read_bytes())
    with pytest.raises(FileExistsError, match="reconciliation"):
        await _run(args)
    assert (media.read_bytes(), manifest.read_bytes()) == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 177_000), (0, 300_000), (10_000, 10_000), (10_000, 187_000), (-1, 200_000)],
)
async def test_range_validation_fail_closed(tmp_path: Path, start: int, end: int) -> None:
    args = _fixture(tmp_path, start_ms=start, end_ms=end)
    with pytest.raises(ValueError):
        await _run(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
async def test_source_sha_mismatch_rejected_before_export(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args["source_path"].write_bytes(b"mutated")
    with pytest.raises(ValueError, match="Source bytes"):
        await _run(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,bad",
    [
        ("source_video_id", "AAAAAAAAAAA"),
        ("source_channel_id", "UCother"),
        ("source_kind", "en_original"),
        ("start_ms", 10001),
        ("source_sha256", "sha256:" + "0" * 64),
        ("project_key", "legendary-poet"),
    ],
)
async def test_boundary_evidence_is_exactly_source_bound(tmp_path: Path, field: str, bad: object) -> None:
    args = _fixture(tmp_path)
    evidence = json.loads(args["evidence_path"].read_text(encoding="utf-8"))
    evidence[field] = bad
    args["evidence_path"].write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="Boundary evidence"):
        await _run(args)


@pytest.mark.asyncio
async def test_missing_real_ru_edge_listening_record_is_not_accepted(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    evidence = json.loads(args["evidence_path"].read_text(encoding="utf-8"))
    evidence["boundary_review"]["status"] = "asr_only"
    args["evidence_path"].write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="listening"):
        await _run(args)


@pytest.mark.asyncio
async def test_owner_source_duration_must_contain_full_interval(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    with pytest.raises(ValueError, match="source duration"):
        await _run(args, source_duration=100.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duration,width,has_audio,reason",
    [
        (200.0, 720, True, "duration"),
        (205.0, 1920, True, "720x1280"),
        (205.0, 720, False, "audio/video"),
    ],
)
async def test_bad_render_cannot_be_transferred(
    tmp_path: Path, duration: float, width: int, has_audio: bool, reason: str
) -> None:
    args = _fixture(tmp_path)
    with pytest.raises(RuntimeError, match=reason):
        await _run(args, output_duration=duration, output_width=width, output_has_audio=has_audio)
    assert list(args["output_dir"].iterdir()) == []


@pytest.mark.asyncio
async def test_failed_renderer_does_not_leave_partial_outputs(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    with pytest.raises(RuntimeError, match="renderer failed"):
        await _run(args, renderer_success=False)
    assert list(args["output_dir"].iterdir()) == []


@pytest.mark.asyncio
async def test_source_mutation_during_render_fails_provenance(tmp_path: Path) -> None:
    args = _fixture(tmp_path)

    async def mutate_source(source: Path, output: Path, start: float, end: float, **kwargs: Any) -> bool:
        await asyncio.to_thread(source.write_bytes, b"concurrently replaced source after initial SHA check")
        await asyncio.to_thread(output.write_bytes, b"synthetic frame bytes" * 1000)
        return True

    async def probed(path: Path) -> MediaProbe:
        return _probe(duration=650.0 if path == args["source_path"].resolve() else 205.0)

    with pytest.raises(RuntimeError, match="changed during rendering"):
        await export_gap_interval(**args, render=mutate_source, probe=probed)
    assert list(args["output_dir"].iterdir()) == []


@pytest.mark.parametrize("multi_person", [False, True])
def test_cli_blur_opt_in_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], multi_person: bool
) -> None:
    from services import shortorez_export as exporter

    calls: list[dict[str, Any]] = []

    async def fake_export(**kwargs: Any) -> tuple[Path, Path]:
        calls.append(kwargs)
        return tmp_path / "preview.mp4", tmp_path / "manifest.json"

    monkeypatch.setattr(exporter, "export_gap_interval", fake_export)
    argv = [
        "--source-path",
        str(tmp_path / "source.mp4"),
        "--source-sha256",
        "sha256:" + "a" * 64,
        "--ru-boundaries-json",
        str(tmp_path / "heard_words.json"),
        "--youtube-id",
        "5nKCs4bkoxE",
        "--start-ms",
        "10000",
        "--end-ms",
        "215000",
        "--output-dir",
        str(tmp_path / "out"),
    ]
    if multi_person:
        argv.append("--multi-person-context-blur")
    assert exporter.main(argv) == 0
    assert len(calls) == 1
    assert calls[0]["allow_multi_person_blur"] is multi_person
    response = json.loads(capsys.readouterr().out)
    assert response["release_authorized"] is False
    assert response["provider_write_performed"] is False


def _solo_plan_file(args: dict[str, Any], tmp_path: Path) -> Path:
    plan = {
        "schema_name": "mp3bot.shortorez-fixed-shot-plan",
        "schema_version": 1,
        "project_key": "lord-god-strength",
        "source_channel_id": "UCeSJsC6go2c9pdJCuUI1BYA",
        "source_video_id": args["youtube_id"],
        "source_sha256": args["source_sha256"],
        "start_ms": args["start_ms"],
        "end_ms": args["end_ms"],
        "source_width": 640,
        "source_height": 358,
        "layout": "solo_static_crop_per_source_camera_shot",
        "camera_cut_review": {
            "status": "original_camera_edits_reviewed",
            "reviewer": "Operator review fixture",
            "method": "Original camera cut checked at the exact adjacent frames",
        },
        "shots": [
            {"start_ms": 10_000, "end_ms": 100_000, "crop_x": 20, "camera_shot_id": "wide"},
            {"start_ms": 100_000, "end_ms": 215_000, "crop_x": 436, "camera_shot_id": "close"},
        ],
    }
    path = tmp_path / "camera_shots.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_reviewed_solo_export_stays_local_and_fullbleed(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args["allow_multi_person_blur"] = False
    shot_file = _solo_plan_file(args, tmp_path)
    args["solo_shot_plan_path"] = shot_file
    calls: list[Any] = []

    async def solo_render(source: Path, output: Path, plan: Any) -> bool:
        calls.append((source, plan))
        await asyncio.to_thread(output.write_bytes, b"fixture:final-vertical-mp4" * 100)
        return True

    async def probed(path: Path) -> MediaProbe:
        if path == args["source_path"].resolve():
            return _probe(duration=650.0, width=640, height=358)
        return _probe(duration=205.0)

    media, receipt = await export_gap_interval(**args, render_solo=solo_render, probe=probed)
    assert await asyncio.to_thread(media.is_file)
    manifest = json.loads(await asyncio.to_thread(receipt.read_text, encoding="utf-8"))
    assert len(calls) == 1
    assert calls[0][1].shots[0].crop_x == 20
    assert calls[0][1].shots[1].crop_x == 436
    assert manifest["render_mode"] == "fixed_source_camera_shots"
    assert manifest["visual_layout"] == "solo_fixed_per_camera_shot_full_bleed"
    assert manifest["camera_shot_count"] == 2
    assert manifest["camera_shot_plan_sha256"] == _digest(await asyncio.to_thread(shot_file.read_bytes))
    assert manifest["final_encoded_audio_listened"] is False
    assert manifest["visual_safe_zones_reviewed"] is False
    assert manifest["rights_cleared"] is False
    assert manifest["release_authorized"] is False
    assert manifest["provider_write_performed"] is False


@pytest.mark.asyncio
async def test_solo_plan_and_context_blur_are_mutually_exclusive(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args["solo_shot_plan_path"] = _solo_plan_file(args, tmp_path)
    with pytest.raises(ValueError, match="exactly one layout"):
        await export_gap_interval(**args)
    assert not await asyncio.to_thread(args["output_dir"].exists)


@pytest.mark.asyncio
async def test_unbound_solo_plan_fails_before_render_or_output(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args["allow_multi_person_blur"] = False
    shot_file = _solo_plan_file(args, tmp_path)
    record = json.loads(await asyncio.to_thread(shot_file.read_text, encoding="utf-8"))
    record["source_sha256"] = "sha256:" + "f" * 64
    await asyncio.to_thread(shot_file.write_text, json.dumps(record), encoding="utf-8")
    args["solo_shot_plan_path"] = shot_file

    async def unexpected_render(*_args: Any) -> bool:
        raise AssertionError("Never render unbound source")

    async def probed(path: Path) -> MediaProbe:
        return _probe(duration=650.0, width=640, height=358)

    with pytest.raises(ValueError, match="source source_sha256"):
        await export_gap_interval(**args, render_solo=unexpected_render, probe=probed)
    assert not await asyncio.to_thread(args["output_dir"].exists)


@pytest.mark.asyncio
async def test_shot_plan_file_replaced_during_solo_render_fails_without_public_output(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args["allow_multi_person_blur"] = False
    shot_file = _solo_plan_file(args, tmp_path)
    args["solo_shot_plan_path"] = shot_file

    async def mutate_plan(_source: Path, output: Path, _plan: Any) -> bool:
        await asyncio.to_thread(output.write_bytes, b"temporary staged preview" * 100)
        await asyncio.to_thread(shot_file.write_text, "concurrently modified", encoding="utf-8")
        return True

    async def probed(path: Path) -> MediaProbe:
        if path == args["source_path"].resolve():
            return _probe(duration=650.0, width=640, height=358)
        return _probe(duration=205.0)

    with pytest.raises(RuntimeError, match="plan changed during rendering"):
        await export_gap_interval(**args, render_solo=mutate_plan, probe=probed)
    assert await asyncio.to_thread(lambda: list(args["output_dir"].iterdir())) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sar", "rotation"),
    [
        ("", None),
        ("N/A", 0.0),
        ("4:3", 0.0),
        ("1:1", 90.0),
        ("1:1", None),
    ],
)
async def test_solo_source_geometry_requires_normalized_square_pixels_before_render(
    tmp_path: Path, sar: str, rotation: float | None
) -> None:
    args = _fixture(tmp_path)
    args["allow_multi_person_blur"] = False
    args["solo_shot_plan_path"] = _solo_plan_file(args, tmp_path)

    async def prohibited_render(*_args: Any) -> bool:
        raise AssertionError("Unsupported pixel geometry must not reach FFmpeg")

    async def probed(_path: Path) -> MediaProbe:
        return MediaProbe(
            duration=650.0,
            width=640,
            height=358,
            audio_sample_rate=48000,
            audio_codec="aac",
            has_video=True,
            has_audio=True,
            size_mb=0.1,
            video_sample_aspect_ratio=sar,
            video_display_rotation=rotation,
        )

    with pytest.raises(ValueError, match="pixel aspect or display rotation"):
        await export_gap_interval(**args, render_solo=prohibited_render, probe=probed)
    assert not await asyncio.to_thread(args["output_dir"].exists)
