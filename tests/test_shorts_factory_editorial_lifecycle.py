from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_disk_guard as disk_guard
import services.shorts_factory_editorial_bridge as bridge


def test_video_only_flow_explicitly_releases_audio_ordering_dependency():
    url = "https://example/video-only"
    disk_guard.register_factory_source_info(url, {"duration": 120})
    state = disk_guard._state_for(url)
    assert state is not None
    assert state.audio_done.is_set() is False

    try:
        assert disk_guard.mark_factory_analysis_audio_skipped(url) is True
        assert state.audio_done.is_set() is True
        assert state.audio_error is None
    finally:
        disk_guard._finish_request(url, state)


def test_editorial_mode_calls_video_only_disk_guard_contract():
    source = inspect.getsource(bridge.process_translation_editorial_only)
    assert "mark_factory_analysis_audio_skipped(url)" in source


def test_pending_cleanup_never_evicts_an_active_master(monkeypatch, tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    monkeypatch.setattr(bridge, "PENDING_DIR", pending)
    monkeypatch.setattr(bridge, "cache_ttl_seconds", lambda: 1000.0)
    monkeypatch.setattr(bridge, "cache_max_items", lambda: 2)
    monkeypatch.setattr(bridge.time, "time", lambda: 2000.0)

    active = pending / "active.mp4"
    old = pending / "old.mp4"
    new = pending / "new.mp4"
    for path, modified in ((active, 1100.0), (old, 1200.0), (new, 1900.0)):
        path.write_bytes(b"video")
        os.utime(path, (modified, modified))

    bridge._set_pending_active(active, True)
    try:
        bridge.cleanup_pending_sources()
        assert active.is_file()
        assert old.is_file()
        assert new.is_file()
    finally:
        bridge._set_pending_active(active, False)

    bridge.cleanup_pending_sources()
    assert active.is_file() is False
    assert old.is_file()
    assert new.is_file()


def test_disabled_factory_editorial_pack_does_not_create_handoff(monkeypatch, tmp_path):
    import services.translation_editorial_factory as editorial

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    pending = tmp_path / "pending"
    monkeypatch.setattr(bridge, "PENDING_DIR", pending)
    monkeypatch.setattr(editorial, "factory_editorial_pack_enabled", lambda: False)

    state = {"plan": {"metadata": {"language": "en"}}}
    token = bridge.JOB_STATE.set(state)
    try:
        result = bridge.persist_source_for_editorial(
            source,
            "media",
            original_persist=lambda path, media_id: path,
        )
    finally:
        bridge.JOB_STATE.reset(token)

    assert result == source
    assert "editorial_source" not in state
    assert pending.exists() is False


@pytest.mark.asyncio
async def test_failed_factory_deletes_its_unused_editorial_handoff(monkeypatch, tmp_path):
    pending = tmp_path / "handoff.mp4"
    pending.write_bytes(b"video")
    monkeypatch.setattr(bridge, "cleanup_pending_sources", lambda: None)

    async def original(*args, **kwargs):
        state = bridge.JOB_STATE.get()
        assert state is not None
        state["editorial_source"] = pending
        bridge._set_pending_active(pending, True)
        return False

    update = SimpleNamespace(message=SimpleNamespace())
    result = await bridge.process_factory_with_editorial(
        original,
        "https://example/fail",
        update,
        silent_errors=True,
    )

    assert result is False
    assert pending.exists() is False
    assert bridge._pending_key(pending) not in bridge._ACTIVE_PENDING
