"""Offline regressions for SHORTOREZ's existing-renderer 177–300s handoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services.media_delivery_probe import MediaProbe
from services.shortorez_export import export_gap_interval


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _fixture(tmp_path: Path, *, start_ms: int = 10_000, end_ms: int = 215_000) -> dict:
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
    )


async def _run(
    args: dict,
    *,
    output_duration: float | None = None,
    output_width: int = 720,
    output_has_audio: bool = True,
    renderer_success: bool = True,
    source_duration: float = 650.0,
):
    calls: list[tuple] = []

    async def fake_render(source: Path, output: Path, start: float, end: float, **kwargs) -> bool:
        calls.append((source, output, start, end, kwargs))
        if renderer_success:
            output.write_bytes(b"synthetic-render-not-a-real-mp4" * 1000)
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
    [(0, 177_000), (0, 300_000), (10_000, 10_000), (10_000, 190_000), (-1, 200_000)],
)
async def test_range_validation_fail_closed(tmp_path: Path, start: int, end: int) -> None:
    args = _fixture(tmp_path, start_ms=start, end_ms=end)
    if (end - start) > 177_000 and (end - start) < 300_000 and start >= 0:
        pytest.skip("This candidate is intentionally within the factory gap")
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
@pytest.mark.parametrize("field,bad", [
    ("source_video_id", "AAAAAAAAAAA"),
    ("source_channel_id", "UCother"),
    ("source_kind", "en_original"),
    ("start_ms", 10001),
    ("source_sha256", "sha256:" + "0" * 64),
    ("project_key", "legendary-poet"),
])
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
@pytest.mark.parametrize("duration,width,has_audio,reason", [
    (200.0, 720, True, "duration"),
    (205.0, 1920, True, "720x1280"),
    (205.0, 720, False, "audio/video"),
])
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
