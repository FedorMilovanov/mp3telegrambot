from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import services.project_runtime_hardening as hardening


def test_bounded_lru_cache_evicts_oldest_and_refreshes_reads():
    cache = hardening.BoundedLRUDict(max_entries=8)
    for index in range(8):
        cache[index] = str(index)
    assert cache[0] == "0"  # refresh key 0
    cache[8] = "8"
    assert 0 in cache
    assert 1 not in cache
    assert len(cache) == 8


def test_atomic_singleton_reuses_own_pid_and_rejects_live_foreign_pid(tmp_path, monkeypatch):
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(hardening, "_LOCK_PATH", lock)
    monkeypatch.setattr(hardening, "_EARLY_LOCK_ACQUIRED", False)
    monkeypatch.setattr(hardening, "_pid_is_running", lambda pid: pid == 999999)

    assert hardening.acquire_early_singleton() is True
    assert lock.read_text(encoding="utf-8") == str(os.getpid())
    assert hardening.acquire_early_singleton() is True

    hardening.release_early_singleton()
    lock.write_text("999999", encoding="utf-8")
    assert hardening.acquire_early_singleton() is False
    assert lock.read_text(encoding="utf-8") == "999999"


def test_stale_audio_cleanup_keeps_recent_and_active_files(tmp_path, monkeypatch):
    import core.database as database
    import core.globals as globals_module

    old = tmp_path / "old_video.mp3"
    recent = tmp_path / "recent_video.mp3"
    active = tmp_path / "active_video_64.mp3"
    for path in (old, recent, active):
        path.write_bytes(b"audio")
    stale_time = time.time() - 10 * 86400
    os.utime(old, (stale_time, stale_time))
    os.utime(active, (stale_time, stale_time))

    monkeypatch.setattr(globals_module, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(database, "CACHE_TTL_DAYS", 5)
    monkeypatch.setattr(hardening, "_active_video_ids", lambda: {"active_video"})

    assert hardening.cleanup_stale_cached_audio() == 1
    assert not old.exists()
    assert recent.exists()
    assert active.exists()


def test_optional_stage_failure_is_isolated():
    async def broken(*_args, **_kwargs):
        raise RuntimeError("optional failed")

    wrapped = hardening._safe_optional_wrapper("broken", broken, False)
    assert asyncio.run(wrapped()) is False


def test_optional_stage_does_not_swallow_cancellation():
    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    wrapped = hardening._safe_optional_wrapper("cancelled", cancelled, False)
    try:
        asyncio.run(wrapped())
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError must propagate")


def test_guarded_mp3_command_is_narrow(tmp_path):
    source = tmp_path / "video.mp3"
    output = tmp_path / "video_64.mp3"
    command = ["ffmpeg", "-i", str(source), "-b:a", "64k", "-y", str(output)]
    guarded = hardening._guarded_mp3_command(command)
    assert guarded is not None
    assert guarded[1:] == (source, output)
    assert hardening._guarded_mp3_command(["ffmpeg", "-i", str(source), str(output)]) is None


def test_mp3_proxy_commits_only_valid_temporary_output(tmp_path, monkeypatch):
    source = tmp_path / "video.mp3"
    output = tmp_path / "video_64.mp3"
    source.write_bytes(b"source" * 3000)
    command = ["ffmpeg", "-i", str(source), "-b:a", "64k", "-y", str(output)]

    def fake_run(cmd, *args, **kwargs):
        temp = Path(cmd[-1])
        temp.write_bytes(b"valid" * 3000)
        return hardening._subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(hardening._subprocess, "run", fake_run)
    monkeypatch.setattr(hardening, "_ffprobe_audio_ok", lambda path: path.exists() and path.stat().st_size > 10_240)

    result = hardening._SubprocessProxy().run(command, capture_output=True)
    assert result.returncode == 0
    assert output.exists()
    assert not list(tmp_path.glob("*.part-*.mp3"))


def test_mp3_proxy_rejects_failed_partial_output(tmp_path, monkeypatch):
    source = tmp_path / "video.mp3"
    output = tmp_path / "video_64.mp3"
    source.write_bytes(b"source" * 3000)
    command = ["ffmpeg", "-i", str(source), "-b:a", "64k", "-y", str(output)]

    def fake_run(cmd, *args, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial" * 3000)
        return hardening._subprocess.CompletedProcess(cmd, 1, b"", b"failure")

    monkeypatch.setattr(hardening._subprocess, "run", fake_run)
    monkeypatch.setattr(hardening, "_ffprobe_audio_ok", lambda _path: False)

    result = hardening._SubprocessProxy().run(command, capture_output=True)
    assert result.returncode == 1
    assert not output.exists()
    assert not list(tmp_path.glob("*.part-*.mp3"))
