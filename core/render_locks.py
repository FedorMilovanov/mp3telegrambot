#!/usr/bin/env python3
"""Lightweight in-process locks for expensive video rendering actions.

Used by segment rendering commands/callbacks to avoid accidental parallel ffmpeg
jobs for the same video. This is deliberately process-local; it protects the
common Telegram double-click / repeated command case without introducing a DB job
queue yet.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderLockToken:
    key: str


_ACTIVE_RENDER_KEYS: set[str] = set()
_RENDER_LOCK_GUARD = asyncio.Lock()


def render_lock_key(kind: str, video_id: str) -> str:
    return f"{str(kind or 'render').strip()}:{str(video_id or '').strip()}"


async def try_acquire_render_lock(key: str) -> RenderLockToken | None:
    """Try to acquire render lock. Return token or None if already busy."""
    key = str(key or "").strip()
    if not key:
        return None
    async with _RENDER_LOCK_GUARD:
        if key in _ACTIVE_RENDER_KEYS:
            return None
        _ACTIVE_RENDER_KEYS.add(key)
        return RenderLockToken(key=key)


async def release_render_lock(token: RenderLockToken | None) -> None:
    if token is None:
        return
    async with _RENDER_LOCK_GUARD:
        _ACTIVE_RENDER_KEYS.discard(token.key)


async def is_render_locked(key: str) -> bool:
    async with _RENDER_LOCK_GUARD:
        return str(key or "").strip() in _ACTIVE_RENDER_KEYS
