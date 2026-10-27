from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace
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
async def test_factory_ffprobe_evidence_requires_an_existing_file(monkeypatch, tmp_path: Path) -> None:
    run = AsyncMock()
    monkeypatch.setattr(video_quality.shutil, "which", lambda name: "/ffprobe")
    monkeypatch.setattr(video_quality, "run_cancellable_process", run)

    result = await video_quality._ffprobe_evidence(tmp_path / "missing.mkv")

    assert result == {}
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_ffprobe_evidence_returns_parsed_json(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "source.mkv"
    _write_bytes(path, b"source")

    async def fake_run(command, **kwargs):
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
async def test_factory_media_evidence_logs_worker_side_file_size(monkeypatch, tmp_path: Path, caplog) -> None:
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


def _patch_download_probes(monkeypatch) -> None:
    async def fake_probe(path):
        return SimpleNamespace(height=2160 if path.stem.endswith("tall") else 1080)

    monkeypatch.setattr(video_quality, "probe_media_async", fake_probe)
    monkeypatch.setattr(video_quality, "media_probe_is_deliverable", lambda value: value is not None)
    monkeypatch.setattr(video_quality, "log_factory_media_evidence", AsyncMock())


@pytest.mark.asyncio
async def test_factory_download_selects_newest_deliverable_1080p_candidate(monkeypatch, tmp_path: Path) -> None:
    media_id = "media"
    prefix = f"{media_id}_factory_max_source"
    stale = tmp_path / f"{prefix}.stale"
    unrelated = tmp_path / "unrelated.mkv"
    _write_bytes(stale, b"stale")
    _write_bytes(unrelated, b"unrelated")

    async def fake_run(command, **kwargs):
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
    monkeypatch,
    tmp_path: Path,
) -> None:
    media_id = "media"
    prefix = f"{media_id}_factory_max_source"
    unrelated = tmp_path / "unrelated.mkv"
    _write_bytes(unrelated, b"unrelated")

    async def fake_run(command, **kwargs):
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
async def test_factory_download_cancellation_cleans_partial_candidate(monkeypatch, tmp_path: Path) -> None:
    media_id = "media"
    prefix = f"{media_id}_factory_max_source"
    partial = tmp_path / f"{prefix}.part"

    async def cancelled_run(command, **kwargs):
        await asyncio.to_thread(_write_bytes, partial, b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr(video_quality, "run_cancellable_process", cancelled_run)

    with pytest.raises(asyncio.CancelledError):
        await video_quality.download_factory_video_1080("https://example.test/video", media_id, tmp_path)

    assert not partial.exists()
