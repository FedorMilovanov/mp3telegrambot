from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from handlers import dub_audio_repair as handler


def test_handler_failure_propagates_and_releases_process_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".dubfix.request.lock"
    monkeypatch.setattr(handler, "_process_lock_path", lambda: lock_path)

    async def failing_command(_update, _context) -> None:
        raise RuntimeError("unexpected handler failure")

    monkeypatch.setattr(handler, "_dubfix_command_unlocked", failing_command)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="unexpected handler failure"):
            await handler.dubfix_command(
                SimpleNamespace(effective_message=None),
                object(),
            )

    asyncio.run(run())
    assert not lock_path.exists()

    # A later command can immediately acquire the same process lock.
    with handler._dubfix_process_lock():
        assert lock_path.is_file()
    assert not lock_path.exists()
