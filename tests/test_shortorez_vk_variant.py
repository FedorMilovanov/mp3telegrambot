"""Deterministic offline acceptance for SHORTOREZ VK speed derivation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from services.media_delivery_probe import MediaProbe
from services.shortorez_export import _sha256
from services.shortorez_vk_variant import export_vk_derivative, select_vk_speed_percent


def _media(duration_ms: int, *, has_audio: bool = True, width: int = 720) -> MediaProbe:
    return MediaProbe(
        duration=duration_ms / 1000.0,
        width=width,
        height=1280,
        audio_sample_rate=48_000 if has_audio else 0,
        audio_codec="aac" if has_audio else "",
        has_video=True,
        has_audio=has_audio,
        size_mb=0.1,
    )


def _reviewed_base(tmp_path: Path, *, interval_ms: int = 205_000) -> dict[str, Any]:
    master_hash = "sha256:" + "f" * 64
    path = tmp_path / f"shortorez_5nKCs4bkoxE_10000-{10_000 + interval_ms}_{master_hash[7:19]}_vertical.mp4"
    path.write_bytes(b"offline synthetic base mp4, not a real recording" * 1000)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "shortorez_review_only",
        "project_key": "lord-god-strength",
        "source_channel_id": "UCeSJsC6go2c9pdJCuUI1BYA",
        "source_video_id": "5nKCs4bkoxE",
        "full_video_url": "https://www.youtube.com/watch?v=5nKCs4bkoxE",
        "source_kind": "ru_postmix",
        "source_sha256": master_hash,
        "source_start_ms": 10_000,
        "source_end_ms": 10_000 + interval_ms,
        "boundary_review": {
            "reviewer": "synthetic test; not a real review",
            "first_heard_words": "synthetic beginning",
            "last_heard_words": "synthetic ending",
        },
        "output_file": path.name,
        "output_sha256": _sha256(path),
        "output_duration_ms": interval_ms,
        "output_width": 720,
        "output_height": 1280,
        "speed_percent": 100,
        "render_mode": "existing_full_frame_vertical",
        "release_authorized": False,
        "provider_write_performed": False,
    }
    evidence = tmp_path / "shortorez_1x_handoff.json"
    evidence.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "base_mp4": path,
        "base_manifest_json": evidence,
        "output_dir": tmp_path / "separate_vk_review",
    }


async def _render_stub(
    args: dict[str, Any],
    *,
    result_ms: int | None = None,
    result_has_audio: bool = True,
    result_width: int = 720,
    base_probe_ms: int | None = None,
    change_base_during_transform: bool = False,
    transformer_success: bool = True,
) -> tuple[tuple[Path, Path], list[tuple[bool, float]]]:
    calls: list[tuple[bool, float]] = []
    doc = json.loads(args["base_manifest_json"].read_text(encoding="utf-8"))
    base_duration_ms = doc["source_end_ms"] - doc["source_start_ms"]

    async def transform(source: Path, output: Path, *, normalize_audio: bool, speed: float) -> bool:
        calls.append((normalize_audio, speed))
        if change_base_during_transform:
            await asyncio.to_thread(source.write_bytes, b"changed during render")
        if transformer_success:
            await asyncio.to_thread(output.write_bytes, b"offline synthetic speed transform" * 1000)
        return transformer_success

    async def probe(path: Path) -> MediaProbe:
        if path == args["base_mp4"].resolve():
            return _media(base_probe_ms if base_probe_ms is not None else base_duration_ms)
        duration = result_ms if result_ms is not None else (
            base_duration_ms * 100 + select_vk_speed_percent(base_duration_ms) - 1
        ) // select_vk_speed_percent(base_duration_ms)
        return _media(duration, has_audio=result_has_audio, width=result_width)

    output = await export_vk_derivative(**args, transform=transform, probe=probe)
    return output, calls


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (177_001, 105),
        (189_000, 105),
        (189_001, 110),
        (198_000, 110),
        (198_001, 115),
        (207_000, 115),
        (207_001, 120),
        (216_000, 120),
    ],
)
def test_exact_video_manager_speed_ladder(duration_ms: int, expected: int) -> None:
    assert select_vk_speed_percent(duration_ms) == expected


@pytest.mark.parametrize("duration_ms", [0, 177_000, 216_001, 300_000, True, 190_000.0])
def test_refuses_unsupported_or_unfit_clip(duration_ms: Any) -> None:
    with pytest.raises(ValueError):
        select_vk_speed_percent(duration_ms)


@pytest.mark.asyncio
async def test_genuine_handoff_shape_and_original_untouched(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path)
    original_sha = _sha256(args["base_mp4"])
    (vk_mp4, vk_handoff), calls = await _render_stub(args)
    doc = json.loads(vk_handoff.read_text(encoding="utf-8"))
    assert calls == [(False, 1.15)]
    assert doc["kind"] == "shortorez_vk_speed_review_only"
    assert doc["source_video_id"] == "5nKCs4bkoxE"
    assert doc["speed_percent"] == 115
    assert doc["source_start_ms"] == 10_000
    assert doc["source_end_ms"] == 215_000
    assert doc["projected_review_duration_ms"] == 178_261
    assert doc["output_duration_ms"] == 178_261
    assert doc["base_sha256"] == original_sha == _sha256(args["base_mp4"])
    assert doc["output_sha256"] == _sha256(vk_mp4)
    assert doc["full_video_url"] == "https://www.youtube.com/watch?v=5nKCs4bkoxE"
    assert doc["rights_cleared"] is False
    assert doc["final_encoded_audio_listened"] is False
    assert doc["visual_safe_zones_reviewed"] is False
    assert doc["release_authorized"] is False
    assert doc["provider_write_performed"] is False
    assert len(list(args["output_dir"].iterdir())) == 2


@pytest.mark.asyncio
async def test_collision_cannot_overwrite_or_republish(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path)
    (vk_mp4, vk_handoff), _ = await _render_stub(args)
    original = (vk_mp4.read_bytes(), vk_handoff.read_bytes())
    with pytest.raises(FileExistsError, match="already exists"):
        await _render_stub(args)
    assert (vk_mp4.read_bytes(), vk_handoff.read_bytes()) == original


@pytest.mark.asyncio
async def test_changed_base_sha_blocks_before_transcode(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path)
    args["base_mp4"].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA256"):
        await _render_stub(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("source_channel_id", "some-other-channel"),
        ("source_video_id", "ABCDEFGHIJK"),
        ("source_kind", "en_original"),
        ("output_file", "fake_file.mp4"),
        ("source_start_ms", 10_001),
        ("release_authorized", True),
        ("speed_percent", 115),
        ("render_mode", "unknown"),
        ("full_video_url", "https://youtube.com/watch?v=ABCDEFGHIJK"),
        ("project_key", "legendary-poet"),
    ],
)
async def test_rejects_mismatched_base_manifest(tmp_path: Path, key: str, bad: Any) -> None:
    args = _reviewed_base(tmp_path)
    doc = json.loads(args["base_manifest_json"].read_text(encoding="utf-8"))
    doc[key] = bad
    args["base_manifest_json"].write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        await _render_stub(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
async def test_ineligible_full_thought_is_not_silently_trimmed(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path, interval_ms=225_000)
    with pytest.raises(ValueError, match="another cut"):
        await _render_stub(args)
    assert not args["output_dir"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_ms", "has_audio", "width", "message"),
    [
        (180_001, True, 720, "180s"),
        (170_000, True, 720, "180s"),
        (178_261, False, 720, "audible media"),
        (178_261, True, 1920, "orientation"),
    ],
)
async def test_transcode_safety_fails_closed(
    tmp_path: Path, result_ms: int, has_audio: bool, width: int, message: str
) -> None:
    args = _reviewed_base(tmp_path)
    with pytest.raises(RuntimeError, match=message):
        await _render_stub(args, result_ms=result_ms, result_has_audio=has_audio, result_width=width)
    assert list(args["output_dir"].iterdir()) == []


@pytest.mark.asyncio
async def test_changed_base_during_transcode_fails_sha_provenance(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path)
    with pytest.raises(RuntimeError, match="changed while producing"):
        await _render_stub(args, change_base_during_transform=True)
    assert list(args["output_dir"].iterdir()) == []


@pytest.mark.asyncio
async def test_failed_owned_transform_makes_no_output(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path)
    with pytest.raises(RuntimeError, match="transform failed"):
        await _render_stub(args, transformer_success=False)
    assert list(args["output_dir"].iterdir()) == []


@pytest.mark.asyncio
async def test_base_audio_and_duration_must_be_real(tmp_path: Path) -> None:
    args = _reviewed_base(tmp_path)
    with pytest.raises(ValueError, match="recorded duration"):
        await _render_stub(args, base_probe_ms=201_000)
    assert not args["output_dir"].exists()
