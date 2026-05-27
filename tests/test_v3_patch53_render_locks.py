"""Regression tests for v3 patch 53 — render locks for segment ffmpeg jobs."""

import pytest
from pathlib import Path

from core.render_locks import is_render_locked, release_render_lock, render_lock_key, try_acquire_render_lock


@pytest.mark.asyncio
async def test_render_lock_allows_single_owner_and_release():
    key = render_lock_key("segment", "vid-lock")
    token1 = await try_acquire_render_lock(key)
    token2 = await try_acquire_render_lock(key)
    assert token1 is not None
    assert token2 is None
    assert await is_render_locked(key)
    await release_render_lock(token1)
    assert not await is_render_locked(key)
    token3 = await try_acquire_render_lock(key)
    assert token3 is not None
    await release_render_lock(token3)


def test_cutseg_and_segcut_callbacks_use_render_locks():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    callbacks = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    assert "try_acquire_render_lock" in commands
    assert "release_render_lock(_render_token)" in commands
    assert "Для этого видео уже идёт рендер" in commands
    assert "try_acquire_render_lock" in callbacks
    assert "release_render_lock(_render_token)" in callbacks
    assert "Для этого видео уже идёт рендер" in callbacks
    assert "Не удалось скачать видео" in callbacks
