from __future__ import annotations

import asyncio
import os
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from services import operator_runtime_status as operator_status
from services import restart_state_runtime as restart


class _FakeTask:
    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def test_reset_releases_inflight_but_preserves_confirmed_success():
    from services import livedub_quality_runtime as quality

    pending = Future()
    success_key = ("new", "chat", "reply", "file", "success")
    pending_key = ("new", "chat", "reply", "file", "pending")
    with quality._AUDIO_LOCK:
        quality._AUDIO_SENT.clear()
        quality._AUDIO_INFLIGHT.clear()
        quality._AUDIO_SENT[success_key] = 123.0
        quality._AUDIO_INFLIGHT[pending_key] = pending

    assert restart._reset_audio_coalescing() == 1
    assert pending.result(timeout=0) is False
    assert pending_key not in quality._AUDIO_INFLIGHT
    assert success_key in quality._AUDIO_SENT

    with quality._AUDIO_LOCK:
        quality._AUDIO_SENT.clear()
        quality._AUDIO_INFLIGHT.clear()


def test_reset_cleans_deferred_source_files_and_companion_marks(tmp_path: Path):
    from services import livedub_audio_dedupe as dedupe

    copy = tmp_path / "deferred.mp3"
    copy.write_bytes(b"audio")
    task = _FakeTask()
    key = ("chat", "reply")
    with dedupe._STATE_LOCK:
        dedupe._PENDING.clear()
        dedupe._COMPANION_OK.clear()
        dedupe._PENDING[key] = {
            "audio_path": copy,
            "timeout_task": task,
        }
        dedupe._COMPANION_OK.add(key)

    pending_count, mark_count = restart._reset_source_audio_dedupe()

    assert (pending_count, mark_count) == (1, 1)
    assert task.cancelled is True
    assert not copy.exists()
    assert dedupe._PENDING == {}
    assert dedupe._COMPANION_OK == set()


def test_process_crash_cleanup_deletes_only_stale_deferred_files(tmp_path: Path):
    root = tmp_path / "mp3bot_livedub_deferred"
    root.mkdir()
    stale = root / "stale.mp3"
    recent = root / "recent.mp3"
    stale.write_bytes(b"old")
    recent.write_bytes(b"new")
    now = 1_000_000.0
    os.utime(stale, (now - 7 * 3600, now - 7 * 3600))
    os.utime(recent, (now - 60, now - 60))

    assert restart.cleanup_orphaned_deferred_files(
        max_age_hours=6,
        root=root,
        now=now,
    ) == 1
    assert not stale.exists()
    assert recent.exists()
    assert root.exists()


def test_installer_resets_before_every_new_loop_and_binds_status(monkeypatch):
    order: list[str] = []

    async def original():
        order.append("run")
        return "ok"

    async def status_command(update, context):
        del update, context
        return "status"

    stub = SimpleNamespace(run_bot_async=original, status_command=status_command)
    monkeypatch.setattr(restart, "_INSTALLED", False)
    monkeypatch.setattr(operator_status, "_INSTALLED", False)
    monkeypatch.setattr(
        restart,
        "reset_cross_loop_state",
        lambda: order.append("reset") or {
            "audio_inflight": 0,
            "deferred_source": 0,
            "companion_marks": 0,
            "orphan_files": 0,
        },
    )

    restart.install_restart_state_runtime(stub)
    run_wrapper = stub.run_bot_async
    status_wrapper = stub.status_command
    assert getattr(status_wrapper, "_mp3bot_operator_runtime_status") is True
    assert asyncio.run(stub.run_bot_async()) == "ok"
    assert asyncio.run(stub.run_bot_async()) == "ok"
    assert order == ["reset", "run", "reset", "run"]

    restart.install_restart_state_runtime(stub)
    assert stub.run_bot_async is run_wrapper
    assert stub.status_command is status_wrapper
