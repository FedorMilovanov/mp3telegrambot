from __future__ import annotations

import json
import time
from pathlib import Path

from services import shorts_factory_retry_cache as retry_cache
from services import shorts_factory_source as source


def test_ffmpeg_progress_duration_beats_raw_aac_metadata_estimate() -> None:
    progress = "\n".join(
        [
            "out_time_us=1000000",
            "progress=continue",
            "out_time_us=3263701000",
            "out_time=00:54:23.701000",
            "progress=end",
        ]
    )
    assert source._progress_duration_seconds(progress) == 3263.701
    assert source.factory_duration_matches(3263.701, 3264.0)
    assert not source.factory_duration_matches(3263.701, 33038.803596)


def test_duration_gate_rejects_large_false_raw_aac_estimate() -> None:
    assert source.factory_duration_matches(3263.680, 3264.0)
    assert not source.factory_duration_matches(33038.803596, 3264.0)
    assert not source.factory_duration_matches(3263.701, 0.0)


def test_retry_cache_policy_v2_requires_verified_duration() -> None:
    assert "v2" in retry_cache.FACTORY_CACHE_POLICY
    assert retry_cache._payload_duration_is_valid(
        {"verified_duration_seconds": 3263.701},
        3264.0,
    )
    assert not retry_cache._payload_duration_is_valid(
        {"duration_seconds": 33038.803596},
        3264.0,
    )


def test_cleanup_removes_legacy_v1_false_duration_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(retry_cache, "FACTORY_CACHE_DIR", tmp_path)
    media = tmp_path / "legacy.aac"
    media.write_bytes(b"aac" * 500)
    meta = tmp_path / "legacy.json"
    meta.write_text(
        json.dumps(
            {
                "policy": "lossless-analysis-retry-cache-v1",
                "created_at": time.time(),
                "filename": media.name,
                "size_bytes": media.stat().st_size,
                "duration_seconds": 33038.803596,
                "sha256": "legacy",
            }
        ),
        encoding="utf-8",
    )

    retry_cache.cleanup_retry_cache()

    assert not meta.exists()
    assert not media.exists()


def test_cleanup_keeps_v2_verified_duration_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(retry_cache, "FACTORY_CACHE_DIR", tmp_path)
    media = tmp_path / "verified.aac"
    media.write_bytes(b"aac" * 500)
    meta = tmp_path / "verified.json"
    meta.write_text(
        json.dumps(
            {
                "policy": retry_cache.FACTORY_CACHE_POLICY,
                "created_at": time.time(),
                "filename": media.name,
                "size_bytes": media.stat().st_size,
                "duration_seconds": 3263.701,
                "verified_duration_seconds": 3263.701,
                "expected_duration_seconds": 3264.0,
                "ffprobe_duration_seconds": 33038.803596,
                "sha256": "verified",
            }
        ),
        encoding="utf-8",
    )

    retry_cache.cleanup_retry_cache()

    assert meta.exists()
    assert media.exists()
