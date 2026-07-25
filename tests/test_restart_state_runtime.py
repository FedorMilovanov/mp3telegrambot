from __future__ import annotations

import asyncio
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

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


def test_installer_resets_before_every_new_loop(monkeypatch):
    order: list[str] = []

    async def original():
        order.append("run")
        return "ok"

    stub = SimpleNamespace(run_bot_async=original)
    monkeypatch.setattr(restart, "_INSTALLED", False)
    monkeypatch.setattr(
        restart,
        "reset_cross_loop_state",
        lambda: order.append("reset") or {
            "audio_inflight": 0,
            "deferred_source": 0,
            "companion_marks": 0,
        },
    )

    restart.install_restart_state_runtime(stub)
    wrapper = stub.run_bot_async
    assert asyncio.run(stub.run_bot_async()) == "ok"
    assert asyncio.run(stub.run_bot_async()) == "ok"
    assert order == ["reset", "run", "reset", "run"]

    restart.install_restart_state_runtime(stub)
    assert stub.run_bot_async is wrapper
