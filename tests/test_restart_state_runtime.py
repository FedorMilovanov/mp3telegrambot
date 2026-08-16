from __future__ import annotations

import asyncio
import os
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from services import bot_lifecycle
from services import livedub_delivery_coordinator as delivery
from services import operator_runtime_status as operator_status
from services import restart_state_runtime as restart


def test_reset_releases_inflight_but_preserves_confirmed_success() -> None:
    pending = Future()
    pending_key = ("new", "chat", "reply", "pending")
    success_key = ("new", "chat", "reply", "success")
    with delivery._COMPANION_LOCK:
        delivery._COMPANION_INFLIGHT.clear()
        delivery._COMPANION_SENT.clear()
        delivery._COMPANION_INFLIGHT[pending_key] = pending
        delivery._COMPANION_SENT[success_key] = 123.0

    assert restart._reset_audio_coalescing() == 1
    assert pending.result(timeout=0) is False
    assert pending_key not in delivery._COMPANION_INFLIGHT
    assert success_key in delivery._COMPANION_SENT

    with delivery._COMPANION_LOCK:
        delivery._COMPANION_INFLIGHT.clear()
        delivery._COMPANION_SENT.clear()


def test_process_crash_cleanup_deletes_only_stale_deferred_files(tmp_path: Path) -> None:
    root = tmp_path / "mp3bot_livedub_deferred"
    root.mkdir()
    stale = root / "stale.mp3"
    recent = root / "recent.mp3"
    stale.write_bytes(b"old")
    recent.write_bytes(b"new")
    now = 1_000_000.0
    os.utime(stale, (now - 7 * 3600, now - 7 * 3600))
    os.utime(recent, (now - 60, now - 60))
    assert restart.cleanup_orphaned_deferred_files(max_age_hours=6, root=root, now=now) == 1
    assert not stale.exists()
    assert recent.exists()


def test_process_lifecycle_resets_before_async_runner(monkeypatch) -> None:
    order: list[str] = []

    async def runner():
        order.append("run")
        return None

    stub = SimpleNamespace(run_bot_async=runner)
    monkeypatch.setattr(bot_lifecycle, "_start_health_thread", lambda _module: None)
    monkeypatch.setattr(
        restart,
        "reset_cross_loop_state",
        lambda: order.append("reset") or {
            "audio_inflight": 0, "deferred_source": 0, "companion_marks": 0, "orphan_files": 0
        },
    )
    assert bot_lifecycle.run_bot_process(stub) == 0
    assert order == ["reset", "run"]


