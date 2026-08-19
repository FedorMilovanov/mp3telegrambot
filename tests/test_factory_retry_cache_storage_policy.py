from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import shorts_factory_retry_cache as cache


def test_cache_storage_failure_fails_open_only_after_reverification(monkeypatch, tmp_path):
    prepared = tmp_path / "analysis.aac"
    prepared.write_bytes(b"x" * 4096)

    async def no_cache(*args, **kwargs):
        return None

    async def downloader(*args, **kwargs):
        return prepared

    async def storage_failure(*args, **kwargs):
        raise OSError("cache disk unavailable")

    async def probe(_path):
        return SimpleNamespace(duration=120.0, has_audio=True, audio_sample_rate=48000, audio_codec="aac")

    async def duration(_path):
        return 120.0

    import services.media_delivery_probe as media_probe
    import services.shorts_factory_source as source

    monkeypatch.setattr(cache, "_cached_analysis_audio", no_cache)
    monkeypatch.setattr(cache, "_store_analysis_audio", storage_failure)
    monkeypatch.setattr(media_probe, "probe_media_async", probe)
    monkeypatch.setattr(source, "measure_factory_audio_duration", duration)
    monkeypatch.setattr(source, "factory_audio_probe_is_usable", lambda p: bool(p and p.has_audio))
    monkeypatch.setattr(source, "factory_duration_matches", lambda a, b: abs(a - b) <= 2.0)

    result = asyncio.run(
        cache.download_factory_audio_with_retry_cache(
            "https://example.invalid/video",
            "abc",
            original_downloader=downloader,
            expected_duration=120.0,
        )
    )
    assert result == prepared


def test_cache_storage_failure_still_fails_closed_if_media_no_longer_verifies(monkeypatch, tmp_path):
    prepared = tmp_path / "analysis.aac"
    prepared.write_bytes(b"x" * 4096)

    async def no_cache(*args, **kwargs):
        return None

    async def downloader(*args, **kwargs):
        return prepared

    async def storage_failure(*args, **kwargs):
        raise OSError("cache disk unavailable")

    async def probe(_path):
        return SimpleNamespace(duration=30.0, has_audio=True, audio_sample_rate=48000, audio_codec="aac")

    async def duration(_path):
        return 30.0

    import services.media_delivery_probe as media_probe
    import services.shorts_factory_source as source

    monkeypatch.setattr(cache, "_cached_analysis_audio", no_cache)
    monkeypatch.setattr(cache, "_store_analysis_audio", storage_failure)
    monkeypatch.setattr(media_probe, "probe_media_async", probe)
    monkeypatch.setattr(source, "measure_factory_audio_duration", duration)
    monkeypatch.setattr(source, "factory_audio_probe_is_usable", lambda p: bool(p and p.has_audio))
    monkeypatch.setattr(source, "factory_duration_matches", lambda a, b: abs(a - b) <= 2.0)

    with pytest.raises(RuntimeError, match="mandatory re-verification"):
        asyncio.run(
            cache.download_factory_audio_with_retry_cache(
                "https://example.invalid/video",
                "abc",
                original_downloader=downloader,
                expected_duration=120.0,
            )
        )
