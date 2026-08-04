#!/usr/bin/env python3
"""Runtime routing and per-task render overrides for Shorts Factory MAX."""
from __future__ import annotations

import copy
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_FACTORY_SHORTS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "factory_shorts_candidates", default=None
)
_FACTORY_LONGS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "factory_long_candidates", default=None
)
_FACTORY_SETTINGS: ContextVar[dict[str, bool] | None] = ContextVar(
    "factory_render_settings", default=None
)
_INSTALLED = False


@contextmanager
def factory_render_context(
    shorts_candidates: list[dict[str, Any]],
    long_candidates: list[dict[str, Any]],
) -> Iterator[None]:
    """Inject already judged candidates without global cross-user state."""
    short_token = _FACTORY_SHORTS.set(copy.deepcopy(shorts_candidates))
    long_token = _FACTORY_LONGS.set(copy.deepcopy(long_candidates))
    settings_token = _FACTORY_SETTINGS.set(
        {
            "shorts_subtitles": True,
            "shorts_audio_normalize": True,
            "clips": True,
        }
    )
    try:
        yield
    finally:
        _FACTORY_SETTINGS.reset(settings_token)
        _FACTORY_LONGS.reset(long_token)
        _FACTORY_SHORTS.reset(short_token)


def install_shorts_factory_mode(_main_module=None) -> bool:
    """Patch the two existing link entry points and reuse mature render pipelines."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import pipelines.clips as clips_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts as shorts_module
    from handlers.mode_command import get_user_mode

    original_process = commands_module.process_single_video
    original_shorts_candidates = shorts_module.create_shorts_candidates
    original_long_candidates = clips_module.create_clips_candidates
    original_shorts_setting = shorts_module.asettings_get

    async def process_link_by_mode(
        url,
        update,
        status_msg=None,
        progress_prefix="",
        context=None,
        silent_errors: bool = False,
    ):
        user = getattr(update, "effective_user", None)
        user_id = int(getattr(user, "id", 0) or 0)
        mode = await get_user_mode(user_id) if user_id else "rus"
        if mode == "shorts_max":
            from pipelines.shorts_factory import process_shorts_factory

            return await process_shorts_factory(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )
        return await original_process(
            url,
            update,
            status_msg=status_msg,
            progress_prefix=progress_prefix,
            context=context,
            silent_errors=silent_errors,
        )

    async def factory_shorts_candidates(*args, **kwargs):
        planned = _FACTORY_SHORTS.get()
        if planned is not None:
            return copy.deepcopy(planned)
        return await original_shorts_candidates(*args, **kwargs)

    async def factory_long_candidates(*args, **kwargs):
        planned = _FACTORY_LONGS.get()
        if planned is not None:
            return copy.deepcopy(planned)
        return await original_long_candidates(*args, **kwargs)

    async def factory_shorts_setting(key: str):
        overrides = _FACTORY_SETTINGS.get()
        if overrides is not None and key in overrides:
            return overrides[key]
        return await original_shorts_setting(key)

    commands_module.process_single_video = process_link_by_mode
    playlist_module.process_single_video = process_link_by_mode
    shorts_module.create_shorts_candidates = factory_shorts_candidates
    clips_module.create_clips_candidates = factory_long_candidates
    shorts_module.asettings_get = factory_shorts_setting

    _INSTALLED = True
    logger.info(
        "Shorts Factory MAX runtime installed: ordinary links and playlists are mode-aware"
    )
    return True


__all__ = ["factory_render_context", "install_shorts_factory_mode"]
