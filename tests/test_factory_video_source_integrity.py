from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import shorts_factory_source as source


def _deliverable_probe(duration: float):
    return SimpleNamespace(
        duration=duration,
        width=1920,
        height=1080,
        has_video=True,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="aac",
    )


def test_factory_video_rejects_incomplete_source_and_aborts_missing_fragments(
    monkeypatch, tmp_path
):
    seen: list[str] = []

    async def fake_run(command, **_kwargs):
        seen.extend(map(str, command))
        (tmp_path / "abc_factory_max_source.mkv").write_bytes(b"x" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(_path):
        return _deliverable_probe(80.0)

    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)
    monkeypatch.setattr(source, "media_probe_is_deliverable", lambda _probe: True)
    monkeypatch.setattr(source, "ensure_factory_video_space", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="without a probed maximum-quality"):
        asyncio.run(
            source.download_factory_video_source(
                "https://example.invalid/video",
                "abc",
                tmp_path,
                expected_duration=120.0,
            )
        )

    assert "--abort-on-unavailable-fragments" in seen
    assert "bestvideo+bestaudio/best" in seen


def test_factory_video_accepts_duration_verified_source(monkeypatch, tmp_path):
    async def fake_run(command, **_kwargs):
        path = tmp_path / "abc_factory_max_source.mkv"
        path.write_bytes(b"x" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(_path):
        return _deliverable_probe(119.9)

    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)
    monkeypatch.setattr(source, "media_probe_is_deliverable", lambda _probe: True)
    monkeypatch.setattr(source, "ensure_factory_video_space", lambda *a, **k: None)

    path = asyncio.run(
        source.download_factory_video_source(
            "https://example.invalid/video",
            "abc",
            tmp_path,
            expected_duration=120.0,
        )
    )
    assert path.name == "abc_factory_max_source.mkv"
