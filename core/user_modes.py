#!/usr/bin/env python3
"""Persistent user-mode settings shared by handlers, pipelines and services."""

from __future__ import annotations

import asyncio
from typing import cast

from core.database import _db_conn

EDITORIAL_MODE = "translation_editorial"
VALID_MODES = (
    "rus",
    "eng",
    "eng_fast",
    "eng_fast_qa",
    "shorts_max",
    EDITORIAL_MODE,
)
_SHORTS_FACTORY_FULL_VIDEO_PREFIX = "shorts_factory_full_video_"


async def _read_user_mode(user_id: int) -> str:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_user_mode_raw, user_id)
    except Exception:
        return "rus"


async def get_user_mode(user_id: int) -> str:
    return await _read_user_mode(user_id)


async def get_shorts_factory_full_video(user_id: int) -> bool:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_shorts_factory_full_video_raw, user_id)
    except Exception:
        return False


def _get_user_mode_raw(user_id: int) -> str:
    try:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (f"user_mode_{user_id}",),
            ).fetchone()
        if row and row[0] in VALID_MODES:
            return cast(str, row[0])
    except Exception:
        pass
    return "rus"


def _set_user_mode_raw(user_id: int, mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode}")
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (f"user_mode_{user_id}", mode),
        )
        conn.commit()


def _get_shorts_factory_full_video_raw(user_id: int) -> bool:
    try:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (f"{_SHORTS_FACTORY_FULL_VIDEO_PREFIX}{user_id}",),
            ).fetchone()
        if row:
            return str(row[0]).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        pass
    return False


def _set_shorts_factory_full_video_raw(user_id: int, enabled: bool) -> None:
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (f"{_SHORTS_FACTORY_FULL_VIDEO_PREFIX}{user_id}", "1" if enabled else "0"),
        )
        conn.commit()


__all__ = [
    "EDITORIAL_MODE",
    "VALID_MODES",
    "get_shorts_factory_full_video",
    "get_user_mode",
]
