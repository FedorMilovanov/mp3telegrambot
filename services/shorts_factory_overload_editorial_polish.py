#!/usr/bin/env python3
"""Temporary routing bridge while video dispatch becomes source-owned.

Factory source, plan, translation, publication and delivery behavior are no
longer patched here. This bridge only preserves the two mode routes until
handlers/playlist import ``pipelines.video_dispatch`` directly.
"""
from __future__ import annotations

import logging

from services.shorts_factory_editorial_bridge import (
    EDITORIAL_MODE,
    cleanup_pending_sources,
    install_mode_ui,
    process_translation_editorial_only,
)
from services.shorts_factory_retry_cache import cleanup_retry_cache

logger = logging.getLogger(__name__)
_INSTALLED = False


def install_shorts_factory_overload_editorial_polish() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import handlers.mode_command as mode_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts_factory as factory_module

    install_mode_ui(mode_module)

    previous_commands_process = commands_module.process_single_video
    previous_playlist_process = playlist_module.process_single_video

    def wrap_router(previous_process):
        async def explicit_route(
            url,
            update,
            status_msg=None,
            progress_prefix="",
            context=None,
            silent_errors=False,
        ):
            user = getattr(update, "effective_user", None)
            user_id = int(getattr(user, "id", 0) or 0)
            mode = await mode_module.get_user_mode(user_id) if user_id else "rus"
            if mode == EDITORIAL_MODE:
                return await process_translation_editorial_only(
                    url,
                    update,
                    status_msg=status_msg,
                    progress_prefix=progress_prefix,
                    context=context,
                    silent_errors=silent_errors,
                )
            if mode == "shorts_max":
                return await factory_module.process_shorts_factory(
                    url,
                    update,
                    status_msg=status_msg,
                    progress_prefix=progress_prefix,
                    context=context,
                    silent_errors=silent_errors,
                )
            return await previous_process(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )

        return explicit_route

    commands_module.process_single_video = wrap_router(previous_commands_process)
    playlist_module.process_single_video = wrap_router(previous_playlist_process)

    cleanup_retry_cache()
    cleanup_pending_sources()
    _INSTALLED = True
    logger.info(
        "Temporary Factory routing bridge installed; Factory execution is source-owned"
    )
    return True


__all__ = [
    "install_shorts_factory_overload_editorial_polish",
    "process_translation_editorial_only",
]
