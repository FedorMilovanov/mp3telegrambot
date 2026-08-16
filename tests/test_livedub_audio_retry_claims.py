from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services import livedub_delivery_coordinator as delivery


def test_failed_mp3_attempt_releases_singleflight_claim() -> None:
    delivery.reset_delivery_runtime_state()
    key = ("new", "chat", "reply", "video")

    async def scenario() -> None:
        async def fail() -> bool:
            raise RuntimeError("telegram send failed")

        with pytest.raises(RuntimeError, match="telegram send failed"):
            await delivery._singleflight(key, fail)
        assert key not in delivery._COMPANION_INFLIGHT
        assert key not in delivery._COMPANION_SENT

    asyncio.run(scenario())


def test_successful_duplicate_claim_remains_suppressed() -> None:
    delivery.reset_delivery_runtime_state()
    key = ("new", "chat", "reply", "video")
    calls = 0

    async def scenario() -> None:
        nonlocal calls

        async def succeed() -> bool:
            nonlocal calls
            calls += 1
            return True

        async def duplicate() -> bool:
            raise AssertionError("successful duplicate must stay suppressed")

        assert await delivery._singleflight(key, succeed) is True
        assert await delivery._singleflight(key, duplicate) is True
        assert key in delivery._COMPANION_SENT

    asyncio.run(scenario())
    assert calls == 1


def test_video_without_required_mp3_cannot_enter_file_id_cache() -> None:
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    start = src.index("# Companion delivery is an explicit transaction.")
    end = src.index("# В Quick QA", start)
    section = src[start:end]

    assert "_companions_ok = False" in section
    assert "_companions_ok = await deliver_new_companions(" in section
    assert "if _companions_ok:" in section
    cache_call = "await adb_set_livedub_file_id(media_id, _video_file_id)"
    assert cache_call in section
    assert section.index("if _companions_ok:") < section.index(cache_call)
    assert "Видео не сохранено" in section
