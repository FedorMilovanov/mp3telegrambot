from __future__ import annotations

import os

import services.shorts_factory_overload_runtime as overload


def test_active_orphan_cache_file_survives_cleanup_until_last_holder_releases(
    monkeypatch, tmp_path
):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(overload, "FACTORY_CACHE_DIR", cache)
    monkeypatch.setattr(overload, "cache_ttl_seconds", lambda: 3600.0)
    monkeypatch.setattr(overload, "cache_max_items", lambda: 1)

    media = cache / "active.flac"
    media.write_bytes(b"lossless")

    overload._set_cache_path_active(media, True)
    overload._set_cache_path_active(media, True)
    try:
        overload.cleanup_retry_cache()
        assert media.is_file()

        overload._set_cache_path_active(media, False)
        overload.cleanup_retry_cache()
        assert media.is_file()
    finally:
        overload._set_cache_path_active(media, False)

    overload.cleanup_retry_cache()
    assert media.exists() is False


def test_active_metadata_temp_is_not_removed_by_concurrent_cleanup(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(overload, "FACTORY_CACHE_DIR", cache)

    temp = cache / "entry.deadbeef.tmp"
    temp.write_text("pending metadata", encoding="utf-8")
    overload._set_cache_path_active(temp, True)
    try:
        overload.cleanup_retry_cache()
        assert temp.is_file()
    finally:
        overload._set_cache_path_active(temp, False)

    overload.cleanup_retry_cache()
    assert temp.exists() is False


def test_cache_active_refcount_returns_to_zero(tmp_path):
    media = tmp_path / "same.flac"
    key = overload._cache_path_key(media)
    overload._set_cache_path_active(media, True)
    overload._set_cache_path_active(media, True)
    try:
        assert overload._ACTIVE_CACHE_PATHS[key] == 2
        overload._set_cache_path_active(media, False)
        assert overload._ACTIVE_CACHE_PATHS[key] == 1
    finally:
        overload._set_cache_path_active(media, False)
    assert key not in overload._ACTIVE_CACHE_PATHS
