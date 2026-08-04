#!/usr/bin/env python3
"""Fail-closed translated-source and cache contract for legacy cut modes."""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from core.database import asettings_get_all
from services.media_delivery_probe import (
    media_probe_is_deliverable,
    probe_media_async,
)

logger = logging.getLogger(__name__)

_ENG_CUT_MODES = frozenset({"eng", "eng_fast", "eng_fast_qa"})
_CUT_SETTING_KEYS = (
    "shorts",
    "clips",
    "shorts_montage",
    "shorts_highlights",
)
_CUT_SOURCE_MODE: ContextVar[str] = ContextVar(
    "legacy_cut_source_mode",
    default="rus",
)
_CUT_PIPELINE_REQUESTED: ContextVar[bool] = ContextVar(
    "legacy_cut_pipeline_requested",
    default=False,
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


def cut_pipeline_requested() -> bool:
    return bool(_CUT_PIPELINE_REQUESTED.get())


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
def cut_source_mode_context(
    mode: str,
    *,
    pipeline_requested: bool = False,
) -> Iterator[None]:
    mode_token = _CUT_SOURCE_MODE.set(normalized_cut_mode(mode))
    requested_token = _CUT_PIPELINE_REQUESTED.set(bool(pipeline_requested))
    try:
        yield
    finally:
        _CUT_PIPELINE_REQUESTED.reset(requested_token)
        _CUT_SOURCE_MODE.reset(mode_token)


class _ClipMessageProxy:
    """Bind Telegram metadata to the proved rendered Clip, not the AI plan."""

    def __init__(self, message: Any) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply_video(self, *args, **kwargs):
        video = kwargs.get("video")
        if video is None and args:
            video = args[0]
        try:
            path = Path(video)
        except (TypeError, ValueError, OSError) as exc:
            raise RuntimeError("Clip delivery path is invalid") from exc

        probe = await probe_media_async(path)
        if not media_probe_is_deliverable(probe):
            raise RuntimeError(
                "Clip delivery rejected: final file lacks proved video+audio"
            )
        assert probe is not None
        kwargs["duration"] = max(1, int(round(probe.duration)))
        logger.info(
            "Clip delivery metadata proved: file=%s duration=%.3fs",
            path.name,
            probe.duration,
        )
        return await self._message.reply_video(*args, **kwargs)


class _ClipUpdateProxy:
    def __init__(self, update: Any) -> None:
        self._update = update
        self.message = _ClipMessageProxy(update.message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._update, name)


def install_cut_mode_source_policy() -> bool:
    """Bind task-local cut intent and reject misleading cache/source fallbacks."""
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

    original_main_process = main_pipeline_module.process_single_video
    original_commands_process = commands_module.process_single_video
    original_playlist_process = playlist_module.process_single_video
    original_adb_get = main_pipeline_module.adb_get
    original_is_cache_valid = main_pipeline_module.is_cache_valid
    original_shorts = shorts_module.process_and_send_shorts
    original_clips = clips_module.process_and_send_clips
    original_montage = montage_module.process_and_send_montage
    original_highlights = montage_module.process_and_send_highlights

    async def _cut_features_requested() -> bool:
        try:
            settings = await asettings_get_all()
        except Exception as exc:
            logger.warning(
                "Cut settings read failed; preserving ordinary cache behavior: %s",
                str(exc)[:160],
            )
            return False
        return any(bool(settings.get(key, False)) for key in _CUT_SETTING_KEYS)

    def _wrap_process_entry(original_process):
        @functools.wraps(original_process)
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
            requested = await _cut_features_requested()
            with cut_source_mode_context(
                mode,
                pipeline_requested=requested,
            ):
                if requested:
                    logger.info(
                        "Legacy cut pipeline requested: bypassing analysis cache "
                        "so enabled render stages execute"
                    )
                return await original_process(
                    url,
                    update,
                    status_msg=status_msg,
                    progress_prefix=progress_prefix,
                    context=context,
                    silent_errors=silent_errors,
                )

        return process_single_video_with_mode

    main_process_with_mode = _wrap_process_entry(original_main_process)
    commands_process_with_mode = _wrap_process_entry(original_commands_process)
    playlist_process_with_mode = _wrap_process_entry(original_playlist_process)

    async def cut_aware_adb_get(video_id):
        cached = await original_adb_get(video_id)
        if (
            cached
            and cut_pipeline_requested()
            and translated_source_required()
            and cached.get("livedub_file_id")
        ):
            logger.info(
                "Ignoring cached LiveDub file_id for %s because enabled cut "
                "modes require a local translated video source",
                video_id,
            )
            return {
                **cached,
                "livedub_file_id": "",
                "livedub_file_id_version": "",
            }
        return cached

    def cut_aware_is_cache_valid(cached):
        valid, reason = original_is_cache_valid(cached)
        if valid and cut_pipeline_requested():
            return False, "cut_pipeline_requested"
        return valid, reason

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

        if "update" in kwargs:
            call_kwargs = dict(kwargs)
            call_kwargs["update"] = _ClipUpdateProxy(call_kwargs["update"])
            return await original_clips(*args, **call_kwargs)

        call_args = list(args)
        if len(call_args) >= 8:
            call_args[7] = _ClipUpdateProxy(call_args[7])
        return await original_clips(*call_args, **kwargs)

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

    main_pipeline_module.process_single_video = main_process_with_mode
    commands_module.process_single_video = commands_process_with_mode
    playlist_module.process_single_video = playlist_process_with_mode
    main_pipeline_module.adb_get = cut_aware_adb_get
    main_pipeline_module.is_cache_valid = cut_aware_is_cache_valid

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
        "Legacy cut policy installed: enabled cuts bypass analysis/file-id "
        "cache and untranslated ENG fallbacks are forbidden"
    )
    return True


__all__ = [
    "cut_pipeline_requested",
    "cut_source_is_usable",
    "cut_source_mode_context",
    "install_cut_mode_source_policy",
    "normalized_cut_mode",
    "translated_source_required",
]
