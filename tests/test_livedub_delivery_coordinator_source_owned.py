from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services import livedub_delivery_coordinator as delivery


@pytest.mark.asyncio
async def test_singleflight_runs_one_companion_transaction() -> None:
    delivery._COMPANION_INFLIGHT.clear()
    delivery._COMPANION_SENT.clear()
    calls = 0
    async def operation() -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return True
    key = ("new", "chat", "reply", "video")
    first, second = await asyncio.gather(delivery._singleflight(key, operation), delivery._singleflight(key, operation))
    assert first is True and second is True
    assert calls == 1


def test_delivery_coordinator_is_explicit_not_installed() -> None:
    source = Path(delivery.__file__).read_text(encoding="utf-8")
    assert "def install" not in source
    assert "sys.modules" not in source
    assert "setattr(" not in source
    assert "deliver_new_companions" in source
    assert "deliver_cached_companions" in source
    assert "SourceAudioDeferral" in source
