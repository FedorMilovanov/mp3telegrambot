#!/usr/bin/env python3
"""Install focused Factory overload and editorial hardening after MAX runtime."""
from __future__ import annotations

import logging
from pathlib import Path

from services.shorts_factory_editorial_bridge import (
    EDITORIAL_MODE,
    cleanup_pending_sources,
    deferred_factory_ai_data,
    install_mode_ui,
    persist_source_for_editorial,
    process_factory_with_editorial,
    process_translation_editorial_only,
    role_aware_factory_alignment,
    translation_video_with_boundary_evidence,
)
from services.shorts_factory_overload_runtime import (
    cleanup_retry_cache,
    create_factory_plan_resumable,
    download_factory_audio_with_retry_cache,
    factory_overload_error,
    factory_retryable_service_error,
)

logger = logging.getLogger(__name__)
_INSTALLED = False


def install_shorts_factory_overload_editorial_polish() -> bool:
    """Keep all non-Factory routes untouched and patch only active MAX seams."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import handlers.mode_command as mode_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts_factory as factory_module
    import services.shorts_factory_execution_guard as guard_module
    import services.shorts_factory_runtime as runtime_module

    install_mode_ui(mode_module)

    original_downloader = factory_module._download_factory_audio

    async def cached_downloader(url: str, media_id: str) -> Path:
        return await download_factory_audio_with_retry_cache(
            url,
            media_id,
            original_downloader=original_downloader,
        )

    factory_module._download_factory_audio = cached_downloader
    factory_module.create_factory_plan = create_factory_plan_resumable

    original_prepare = factory_module._prepare_translation_video

    async def prepared_with_evidence(url, workdir, duration, source_language):
        return await translation_video_with_boundary_evidence(
            url,
            workdir,
            duration,
            source_language,
            original_prepare=original_prepare,
        )

    factory_module._prepare_translation_video = prepared_with_evidence
    factory_module._shift_candidates_for_livedub = role_aware_factory_alignment

    original_persist = factory_module._persist_factory_source

    def persisted_for_editorial(source_path, media_id):
        return persist_source_for_editorial(
            source_path,
            media_id,
            original_persist=original_persist,
        )

    factory_module._persist_factory_source = persisted_for_editorial
    guard_module.factory_ai_data = deferred_factory_ai_data

    active_factory_process = factory_module.process_shorts_factory

    async def factory_process(
        url,
        update,
        status_msg=None,
        progress_prefix="",
        context=None,
        silent_errors=False,
    ):
        return await process_factory_with_editorial(
            active_factory_process,
            url,
            update,
            status_msg=status_msg,
            progress_prefix=progress_prefix,
            context=context,
            silent_errors=silent_errors,
        )

    factory_module.process_shorts_factory = factory_process

    previous_commands_process = commands_module.process_single_video
    previous_playlist_process = playlist_module.process_single_video

    def wrap_router(previous_process):
        async def polished_route(
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
                import services.shorts_video_impl as shorts_video_impl

                if not shorts_video_impl.HAS_FASTER_WHISPER:
                    if (
                        not silent_errors
                        and getattr(update, "effective_message", None) is not None
                    ):
                        await update.effective_message.reply_text(
                            "❌ SHORTS FACTORY MAX требует faster-whisper large-v3."
                        )
                    return False
                completion_token = runtime_module._FACTORY_COMPLETED_DELIVERIES.set(None)
                wrapped_status = (
                    runtime_module._FactoryStatusProxy(status_msg)
                    if status_msg is not None
                    else None
                )
                try:
                    result = await factory_module.process_shorts_factory(
                        url,
                        update,
                        status_msg=wrapped_status,
                        progress_prefix=progress_prefix,
                        context=context,
                        silent_errors=silent_errors,
                    )
                    shorts_sent, longs_sent = runtime_module.factory_completed_delivery_counts()
                    return bool(result and (shorts_sent or longs_sent))
                finally:
                    runtime_module._FACTORY_COMPLETED_DELIVERIES.reset(completion_token)
            return await previous_process(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )

        polished_route._factory_overload_editorial_polish = True  # type: ignore[attr-defined]
        return polished_route

    commands_module.process_single_video = wrap_router(previous_commands_process)
    playlist_module.process_single_video = wrap_router(previous_playlist_process)

    cleanup_retry_cache()
    cleanup_pending_sources()
    _INSTALLED = True
    logger.info(
        "Shorts Factory overload/editorial polish installed: Gemini 3.6/HIGH 3-pass preserved, "
        "Factory-only HTTP retry ownership, resumable pass rotation, bounded lossless retry cache, "
        "active VOT RU proof, post-alignment ai_data, editorial ZIP and standalone ENG editor"
    )
    return True


__all__ = [
    "create_factory_plan_resumable",
    "factory_overload_error",
    "factory_retryable_service_error",
    "install_shorts_factory_overload_editorial_polish",
    "process_translation_editorial_only",
    "role_aware_factory_alignment",
]
