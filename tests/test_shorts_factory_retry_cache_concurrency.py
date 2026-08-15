from __future__ import annotations

import services.shorts_factory_retry_cache as retry_cache


def test_active_orphan_cache_file_survives_cleanup_until_last_holder_releases(
    monkeypatch, tmp_path
):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(retry_cache, "FACTORY_CACHE_DIR", cache)
    monkeypatch.setattr(retry_cache, "cache_ttl_seconds", lambda: 3600.0)
    monkeypatch.setattr(retry_cache, "cache_max_items", lambda: 1)

    media = cache / "active.flac"
    media.write_bytes(b"lossless")

    retry_cache._set_cache_path_active(media, True)
    retry_cache._set_cache_path_active(media, True)
    try:
        retry_cache.cleanup_retry_cache()
        assert media.is_file()

        retry_cache._set_cache_path_active(media, False)
        retry_cache.cleanup_retry_cache()
        assert media.is_file()
    finally:
        retry_cache._set_cache_path_active(media, False)

    retry_cache.cleanup_retry_cache()
    assert media.exists() is False


def test_active_metadata_temp_is_not_removed_by_concurrent_cleanup(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(retry_cache, "FACTORY_CACHE_DIR", cache)

    temp = cache / "entry.deadbeef.tmp"
    temp.write_text("pending metadata", encoding="utf-8")
    retry_cache._set_cache_path_active(temp, True)
    try:
        retry_cache.cleanup_retry_cache()
        assert temp.is_file()
    finally:
        retry_cache._set_cache_path_active(temp, False)

    retry_cache.cleanup_retry_cache()
    assert temp.exists() is False


def test_cache_active_refcount_returns_to_zero(tmp_path):
    media = tmp_path / "same.flac"
    key = retry_cache._cache_path_key(media)
    retry_cache._set_cache_path_active(media, True)
    retry_cache._set_cache_path_active(media, True)
    try:
        assert retry_cache._ACTIVE_CACHE_PATHS[key] == 2
        retry_cache._set_cache_path_active(media, False)
        assert retry_cache._ACTIVE_CACHE_PATHS[key] == 1
    finally:
        retry_cache._set_cache_path_active(media, False)
    assert key not in retry_cache._ACTIVE_CACHE_PATHS
