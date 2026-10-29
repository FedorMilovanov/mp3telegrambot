from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services import shorts_factory_video_quality as video_quality


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _matching_paths(directory: Path, pattern: str) -> list[Path]:
    return list(directory.glob(pattern))


def _write_download_candidates(directory: Path, prefix: str) -> None:
    good = directory / f"{prefix}.good.mkv"
    tall = directory / f"{prefix}.tall.mkv"
    partial = directory / f"{prefix}.part"
    _write_bytes(good, b"good")
    _write_bytes(tall, b"tall")
    _write_bytes(partial, b"partial")
    os.utime(good, (10, 10))
    os.utime(tall, (20, 20))


@pytest.mark.asyncio
async def test_factory_ffprobe_evidence_requires_an_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = AsyncMock()
    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffprobe")
    monkeypatch.setattr(video_quality, "run_cancellable_process", run)

    result = await video_quality._ffprobe_evidence(tmp_path / "missing.mkv")

    assert result == {}
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_ffprobe_evidence_returns_parsed_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "source.mkv"
    _write_bytes(path, b"source")

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout='{"format":{"duration":"12.5"},"streams":[]}',
            stderr="",
        )

    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffprobe")
    monkeypatch.setattr(video_quality, "run_cancellable_process", fake_run)

    result = await video_quality._ffprobe_evidence(path)

    assert result == {"format": {"duration": "12.5"}, "streams": []}


@pytest.mark.asyncio
async def test_factory_media_evidence_logs_worker_side_file_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "source.mkv"
    _write_bytes(path, b"x" * (3 * 1024 * 1024))
    monkeypatch.setattr(
        video_quality,
        "_ffprobe_evidence",
        AsyncMock(return_value={"format": {"duration": "12.5"}, "streams": []}),
    )

    with caplog.at_level(logging.INFO, logger=video_quality.__name__):
        await video_quality.log_factory_media_evidence("test-source", path, video_encode_stages=0)

    assert "Factory media evidence [test-source]" in caplog.text
    assert "size=3.0MB" in caplog.text
    assert "video_encode_stages=0" in caplog.text


def _patch_download_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_probe(path: Path) -> SimpleNamespace:
        return SimpleNamespace(height=2160 if path.stem.endswith("tall") else 1080)

    monkeypatch.setattr(video_quality, "probe_media_async", fake_probe)
    monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: value is not None)
    monkeypatch.setattr(video_quality, "log_factory_media_evidence", AsyncMock())


@pytest.mark.asyncio
async def test_factory_download_selects_newest_deliverable_1080p_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media_id = "media"
    prefix = f"{media_id}_factory_max_source"
    stale = tmp_path / f"{prefix}.stale"
    unrelated = tmp_path / "unrelated.mkv"
    _write_bytes(stale, b"stale")
    _write_bytes(unrelated, b"unrelated")

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        await asyncio.to_thread(_write_download_candidates, tmp_path, prefix)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(video_quality, "run_cancellable_process", fake_run)
    _patch_download_probes(monkeypatch)

    result = await video_quality.download_factory_video_1080("https://example.test/video", media_id, tmp_path)

    assert result == tmp_path / f"{prefix}.good.mkv"
    assert result.exists()
    assert not stale.exists()
    assert unrelated.read_bytes() == b"unrelated"


@pytest.mark.asyncio
async def test_factory_download_rejects_unverified_candidates_and_cleans_only_owned_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_id = "media"
    prefix = f"{media_id}_factory_max_source"
    unrelated = tmp_path / "unrelated.mkv"
    _write_bytes(unrelated, b"unrelated")

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        await asyncio.to_thread(_write_download_candidates, tmp_path, prefix)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(video_quality, "run_cancellable_process", fake_run)
    _patch_download_probes(monkeypatch)
    monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: False)

    with pytest.raises(RuntimeError, match="without a verified Factory"):
        await video_quality.download_factory_video_1080("https://example.test/video", media_id, tmp_path)

    assert await asyncio.to_thread(_matching_paths, tmp_path, f"{prefix}.*") == []
    assert unrelated.read_bytes() == b"unrelated"


@pytest.mark.asyncio
async def test_factory_download_cancellation_cleans_partial_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media_id = "media"
    prefix = f"{media_id}_factory_max_source"
    partial = tmp_path / f"{prefix}.part"

    async def cancelled_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        await asyncio.to_thread(_write_bytes, partial, b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr(video_quality, "run_cancellable_process", cancelled_run)

    with pytest.raises(asyncio.CancelledError):
        await video_quality.download_factory_video_1080("https://example.test/video", media_id, tmp_path)

    assert not partial.exists()


@pytest.mark.asyncio
async def test_factory_normalize_only_copies_video_and_replaces_stale_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    _write_bytes(input_path, b"input")
    _write_bytes(output_path, b"stale")
    calls: list[list[str]] = []

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(list(command))
        await asyncio.to_thread(_write_bytes, output_path, b"normalized")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(video_quality, "run_cancellable_process", fake_run)
    monkeypatch.setattr(video_quality, "probe_media_async", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: True)

    result = await video_quality.normalize_factory_short_audio_copy_video(input_path, output_path)

    assert result is True
    assert output_path.read_bytes() == b"normalized"
    assert len(calls) == 1
    assert calls[0][calls[0].index("-c:v") + 1] == "copy"


@pytest.mark.asyncio
async def test_factory_normalize_only_missing_input_preserves_existing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "output.mp4"
    _write_bytes(output_path, b"existing")
    run = AsyncMock()
    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(video_quality, "run_cancellable_process", run)

    result = await video_quality.normalize_factory_short_audio_copy_video(tmp_path / "missing.mp4", output_path)

    assert result is False
    assert output_path.read_bytes() == b"existing"
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["nonzero", "exception", "cancel", "probe"])
async def test_factory_normalize_only_failure_cleans_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    _write_bytes(input_path, b"input")
    _write_bytes(output_path, b"stale")

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        await asyncio.to_thread(_write_bytes, output_path, b"partial")
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "exception":
            raise RuntimeError("ffmpeg failed")
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0, stderr="failed")

    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(video_quality, "run_cancellable_process", fake_run)
    monkeypatch.setattr(video_quality, "probe_media_async", AsyncMock(return_value=None))
    monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: value is not None)

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await video_quality.normalize_factory_short_audio_copy_video(input_path, output_path)
    else:
        result = await video_quality.normalize_factory_short_audio_copy_video(input_path, output_path)
        assert result is False
    assert not output_path.exists()


def _patch_long_runtime(
    monkeypatch: pytest.MonkeyPatch,
    source_path: Path,
    run: Callable[..., Awaitable[SimpleNamespace]],
    *,
    output_probe: Any,
    encoder: tuple[str, list[str], list[str]],
) -> None:
    async def no_snap(path: Path, target: float, search_window: float = 5.0) -> float:
        return target

    async def fake_probe(path: Path) -> Any:
        if path == source_path:
            return SimpleNamespace(width=3840, height=2160)
        return output_probe

    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(video_quality, "_find_silence_end", no_snap)
    monkeypatch.setattr(video_quality, "probe_media_async", fake_probe)
    monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: value is not None)
    monkeypatch.setattr(video_quality, "_factory_h264_encoder", AsyncMock(return_value=encoder))
    monkeypatch.setattr(video_quality, "run_cancellable_process", run)
    monkeypatch.setattr(video_quality, "log_factory_media_evidence", AsyncMock())


@pytest.mark.asyncio
async def test_factory_long_render_uses_one_h264_pass_and_scales_large_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "long.mp4"
    _write_bytes(source_path, b"source")
    calls: list[list[str]] = []

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(list(command))
        await asyncio.to_thread(_write_bytes, output_path, b"rendered")
        return SimpleNamespace(returncode=0, stderr="")

    _patch_long_runtime(
        monkeypatch,
        source_path,
        fake_run,
        output_probe=SimpleNamespace(width=1920, height=1080),
        encoder=("libx264", ["-crf", "23"], ["-preset", "medium"]),
    )

    result = await video_quality.render_factory_long_h264(source_path, output_path, 10, 70)

    assert result is True
    assert output_path.read_bytes() == b"rendered"
    assert len(calls) == 1
    assert calls[0][calls[0].index("-c:v") + 1] == "libx264"
    assert any(arg.startswith("scale=") for arg in calls[0])


@pytest.mark.asyncio
async def test_factory_long_nvenc_failure_retries_once_with_libx264(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "long.mp4"
    _write_bytes(source_path, b"source")
    monkeypatch.setattr(video_quality, "_H264_NVENC_AVAILABLE", True)
    calls: list[list[str]] = []

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(list(command))
        payload = b"partial" if len(calls) == 1 else b"cpu rendered"
        await asyncio.to_thread(_write_bytes, output_path, payload)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0, stderr="nvenc failed")

    _patch_long_runtime(
        monkeypatch,
        source_path,
        fake_run,
        output_probe=SimpleNamespace(width=1920, height=1080),
        encoder=("h264_nvenc", ["-cq", "25"], ["-preset", "p6"]),
    )

    result = await video_quality.render_factory_long_h264(source_path, output_path, 10, 70)

    assert result is True
    assert output_path.read_bytes() == b"cpu rendered"
    assert len(calls) == 2
    assert calls[0][calls[0].index("-c:v") + 1] == "h264_nvenc"
    assert calls[1][calls[1].index("-c:v") + 1] == "libx264"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["nonzero", "exception", "cancel", "probe", "wide", "tall"])
async def test_factory_long_failure_cleans_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "long.mp4"
    _write_bytes(source_path, b"source")
    _write_bytes(output_path, b"stale")
    output_probes = {
        "wide": SimpleNamespace(width=1922, height=1080),
        "tall": SimpleNamespace(width=1920, height=1082),
    }

    async def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        await asyncio.to_thread(_write_bytes, output_path, b"partial")
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "exception":
            raise RuntimeError("render failed")
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0, stderr="failed")

    _patch_long_runtime(
        monkeypatch,
        source_path,
        fake_run,
        output_probe=output_probes.get(failure),
        encoder=("libx264", ["-crf", "23"], ["-preset", "medium"]),
    )
    if failure not in {"probe", "wide", "tall"}:
        monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: True)

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await video_quality.render_factory_long_h264(source_path, output_path, 10, 70)
    else:
        result = await video_quality.render_factory_long_h264(source_path, output_path, 10, 70)
        assert result is False
    assert not output_path.exists()


@pytest.mark.asyncio
async def test_factory_long_missing_source_preserves_existing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "long.mp4"
    _write_bytes(output_path, b"existing")
    run = AsyncMock()
    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(video_quality, "run_cancellable_process", run)

    result = await video_quality.render_factory_long_h264(tmp_path / "missing.mp4", output_path, 10, 70)

    assert result is False
    assert output_path.read_bytes() == b"existing"
    run.assert_not_awaited()
