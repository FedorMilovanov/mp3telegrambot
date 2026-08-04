#!/usr/bin/env python3
"""Fail-closed translated-source contract for legacy ENG cut modes."""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_ENG_CUT_MODES = frozenset({"eng", "eng_fast", "eng_fast_qa"})
_CUT_SOURCE_MODE: ContextVar[str] = ContextVar(
    "legacy_cut_source_mode",
    default="rus",
)
_INSTALLED = False


def normalized_cut_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode else "rus"


def translated_source_required(mode: str | None = None) -> bool:
    current = normalized_cut_mode(
        _CUT_SOURCE_MODE.get() if mode is None else mode
    )
    return current in _ENG_CUT_MODES


def cut_source_is_usable(
    livedub_video_path: Any,
    *,
    mode: str | None = None,
) -> bool:
    """Return whether a cut may run under the current persisted link mode."""
    if not translated_source_required(mode):
        return True
    if not livedub_video_path:
        return False
    try:
        return Path(livedub_video_path).is_file()
    except (OSError, TypeError, ValueError):
        return False


@contextmanager
def cut_source_mode_context(mode: str) -> Iterator[None]:
    token = _CUT_SOURCE_MODE.set(normalized_cut_mode(mode))
    try:
        yield
    finally:
        _CUT_SOURCE_MODE.reset(token)


def install_cut_mode_source_policy() -> bool:
    """Bind one task-local mode and reject untranslated ENG cut fallbacks."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import pipelines.clips as clips_module
    import pipelines.main_pipeline as main_pipeline_module
    import pipelines.montage as montage_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts as shorts_module
    from handlers.mode_command import get_user_mode

    original_process_single_video = main_pipeline_module.process_single_video
    original_shorts = shorts_module.process_and_send_shorts
    original_clips = clips_module.process_and_send_clips
    original_montage = montage_module.process_and_send_montage
    original_highlights = montage_module.process_and_send_highlights

    @functools.wraps(original_process_single_video)
    async def process_single_video_with_mode(
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
        with cut_source_mode_context(mode):
            return await original_process_single_video(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )

    def _skip_reason(kind: str) -> None:
        logger.warning(
            "%s skipped fail-closed: mode=%s requires a verified translated "
            "LiveDub source; original-language fallback is forbidden",
            kind,
            _CUT_SOURCE_MODE.get(),
        )

    @functools.wraps(original_shorts)
    async def guarded_shorts(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 14:
            path = args[13]
        if not cut_source_is_usable(path):
            _skip_reason("Shorts")
            return None
        return await original_shorts(*args, **kwargs)

    @functools.wraps(original_clips)
    async def guarded_clips(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 13:
            path = args[12]
        if not cut_source_is_usable(path):
            _skip_reason("Clips")
            return None
        return await original_clips(*args, **kwargs)

    @functools.wraps(original_montage)
    async def guarded_montage(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 14:
            path = args[13]
        if not cut_source_is_usable(path):
            _skip_reason("Montage")
            return None
        return await original_montage(*args, **kwargs)

    @functools.wraps(original_highlights)
    async def guarded_highlights(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 14:
            path = args[13]
        if not cut_source_is_usable(path):
            _skip_reason("Highlights")
            return None
        return await original_highlights(*args, **kwargs)

    main_pipeline_module.process_single_video = process_single_video_with_mode
    commands_module.process_single_video = process_single_video_with_mode
    playlist_module.process_single_video = process_single_video_with_mode

    shorts_module.process_and_send_shorts = guarded_shorts
    clips_module.process_and_send_clips = guarded_clips
    montage_module.process_and_send_montage = guarded_montage
    montage_module.process_and_send_highlights = guarded_highlights

    main_pipeline_module.process_and_send_shorts = guarded_shorts
    main_pipeline_module.process_and_send_clips = guarded_clips
    main_pipeline_module.process_and_send_montage = guarded_montage
    main_pipeline_module.process_and_send_highlights = guarded_highlights

    _INSTALLED = True
    logger.info(
        "Legacy ENG cut source policy installed: untranslated "
        "Shorts/Clips/Montage/Highlights are forbidden"
    )
    return True


__all__ = [
    "cut_source_is_usable",
    "cut_source_mode_context",
    "install_cut_mode_source_policy",
    "normalized_cut_mode",
    "translated_source_required",
]
